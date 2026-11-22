import json
from pathlib import Path

from housing_watch import db, parse
from housing_watch.adapters import nys_housing_search as nys
from housing_watch.eligibility import listing_verdict

F = Path(__file__).parent / "fixtures" / "nys_housing_search"
RECORDS = json.loads((F / "records.json").read_text())
RULES = json.loads((F / "rules.json").read_text())
BUILDINGS = json.loads((F / "buildings.json").read_text())  # getBuildings per non-Mitchell-Lama record


def rec(name):
    return next(r for r in RECORDS if r["ProjectName"] == name)


def listing(name):
    r = rec(name)
    return nys.to_listing(r, RULES.get(r["Id"]), BUILDINGS.get(r["Id"]))


def test_county_to_borough():
    assert nys.borough("Kings (Brooklyn)") == "Brooklyn"
    assert nys.borough("New York (Manhattan)") == "Manhattan"
    assert nys.borough("Richmond (Staten Island)") == "Staten Island"
    assert nys.borough("Monroe") is None
    assert sum(1 for r in RECORDS if nys.borough(r["County"])) == 51


def test_mitchell_lama_units_table():
    a = listing("Amalgamated Houses")
    assert (a.kind, a.status, a.borough, a.deadline) == ("waitlist", "waitlist", "Bronx", None)
    s, one = a.units[0], a.units[1]
    assert (s.size, s.rent, s.available) == ("studio", 1080.43, True)
    assert [(b.household, b.income_min, b.income_max) for b in one.bands] == [(1, 50488, 204983), (2, 50488, 204983)]
    assert a.url == "https://housingsearch.hcr.ny.gov/housing/s/waitlist/" + rec("Amalgamated Houses")["Id"]
    assert "Mitchell-Lama" in a.notes and "equity" in a.notes


def test_lottery_rules_grouped_by_size_and_ami():
    lg = listing("Luna Green")
    assert (lg.kind, lg.status, lg.deadline, lg.senior_only) == ("lottery", "open", "2026-11-06", True)
    one = next(u for u in lg.units if u.size == "1br")
    assert one.ami == 50 and [(b.household, b.income_max) for b in one.bands] == [(1, 59400), (2, 67850)]
    assert listing_verdict(lg).verdict == "excluded"


def test_eligible_manhattan_waitlist():
    hf = listing("Historic Front Street Development")
    assert hf.borough == "Manhattan" and listing_verdict(hf).verdict == "eligible"


def test_repeated_size_rows_merge_into_one_unit():
    cc = listing("Co-op City (Riverbay)")
    keys = [u.key() for u in cc.units]
    assert len(keys) == len(set(keys))  # the database fingerprints units by key
    one = next(u for u in cc.units if u.size == "1br")
    assert one.rent == min(r["minRent"] for r in RULES[rec("Co-op City (Riverbay)")["Id"]] if r["unitType"] == "1BR")
    db._fp(cc)  # sortable: no duplicate keys with mixed None/float fields


def test_rules_without_income_get_the_ami_cap():
    # Inwood 405-407 W 206th St prints only "120%" per rule; the card's range is $0-$0
    r = {**rec("Historic Front Street Development"), "ProjectName": "Inwood", "MinimumIncome": 0, "MaximumIncome": 0}
    rules = [
        {"amiCategory": "120%", "householdSize": str(n), "monthlyRent": 3306, "unitSize": "Studio"} for n in (1, 2)
    ]
    (u,) = nys.to_listing(r, rules).units
    assert (u.size, u.ami) == ("studio", 120)
    assert [(b.household, b.income_min, b.income_max) for b in u.bands] == [(1, None, 142560), (2, None, 162840)]
    assert listing_verdict(nys.to_listing(r, rules)).reason == "over income (max $142,560)"


def test_card_range_kept_when_no_units_table():
    r = rec("Jamie Towers")
    lst = nys.to_listing(r, [])
    (u,) = lst.units
    assert u.size == "unknown" and (u.bands[0].income_min, u.bands[0].income_max) == (45000, 281923)


def test_closed_record_and_change_detection(store):
    r = dict(rec("Amalgamated Houses"))
    rules = RULES[r["Id"]]
    c = store
    db.record_listings(c, "nys_housing_search", [nys.to_listing(r, rules)])
    assert db.record_listings(c, "nys_housing_search", [nys.to_listing(r, rules)]) == 0
    r["Status"] = "Closed"
    closed = nys.to_listing(r, rules)
    assert closed.status == "closed"
    assert db.record_listings(c, "nys_housing_search", [closed]) == 1


def test_street_address_from_the_buildings_table():
    # SANEL lists these HCR waitlists by street address; their cards have no city (Cities is "")
    names = ("Joseph House", "4519 WPR (The Plains)", "Saint James Terrace")
    assert {n: listing(n).address for n in names} == {
        "Joseph House": "101 Myrtle Avenue, Staten Island, NY 10310",
        "4519 WPR (The Plains)": "4519 White Plains Road, Bronx, NY 10470",
        "Saint James Terrace": "2520 Jerome Avenue, Bronx, NY 10468",
    }
    sh = listing("Self Help KI KII Associates LLC")
    assert sh.address == "137-47 45th Avenue, Queens, NY 11355; 45-25 Kissena Boulevard, Queens, NY 11355"
    assert parse.addr_keys("45-25 Kissena Blvd, NY, 11355") <= parse.addr_keys(sh.address)  # SANEL's copy
    assert listing("Inwood 405-407 W 206th St").address == (
        "405 West 206th Street, New York, NY 10034; 407 West 206th Street, New York, NY 10034"
    )


def test_no_buildings_keeps_the_card_location():
    assert listing("Amalgamated Houses").address == "Bronx, Bronx"  # Mitchell-Lama: getBuildings has none
    r = rec("Joseph House")
    assert nys.to_listing(r, RULES.get(r["Id"]), []).address == "Richmond (Staten Island)"


def test_every_nyc_record_is_kept():
    # the four HCR waitlists SANEL also lists: open NYC waitlists, none filtered by status, kind or senior flag
    ids = {"a0Ycs00000iE3RTEA0", "a0Ycs00000iQZosEAG", "a0Ycs00000iE3RcEAK", "a0Ycs00000iE3ReEAK"}
    got = [listing(r["ProjectName"]) for r in RECORDS if r["Id"] in ids]
    assert {x.ext_id for x in got} == ids
    assert {(x.kind, x.status, x.senior_only) for x in got} == {("waitlist", "waitlist", False)}


def test_fetch_posts_through_fetch_and_builds_the_fixture_listings(monkeypatch):
    page = '"fwuid":"FW" "APPLICATION@markup://siteforce:communityApp":"1"'
    monkeypatch.setattr(nys.fetch, "html", lambda url: page)
    ml = {r["Id"] for r in RECORDS if r["IsMitchellLama"]}
    asked: list[tuple[str, str]] = []

    def post_json(url, *, data, timeout):
        actions = json.loads(data["message"])["actions"]
        reply = []
        for a in actions:
            p = a["params"]
            if p["method"] == "getWaitListRecords":
                rv = RECORDS
            else:
                wid = p["params"]["waitlistId"]
                asked.append((p["method"], wid))
                rv = (BUILDINGS if p["method"] == "getBuildings" else RULES).get(wid)
            reply.append({"id": a["id"], "state": "SUCCESS", "returnValue": {"returnValue": rv}})
        return {"actions": reply[::-1]}  # the reply order is not the request order

    monkeypatch.setattr(nys.fetch, "post_json", post_json)
    got = nys.NysHousingSearch().fetch().listings
    want = [listing(r["ProjectName"]) for r in RECORDS if nys.borough(r["County"])]
    assert got == want and len(got) == 51
    assert not {w for m, w in asked if m == "getBuildings"} & ml  # Mitchell-Lama pages have no buildings table
    assert rec("Saint James Terrace")["Id"] in {w for m, w in asked if m == "getBuildings"}
