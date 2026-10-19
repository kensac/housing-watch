import json
from pathlib import Path

from housing_watch import db
from housing_watch.adapters import nys_housing_search as nys
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "nys_housing_search"
RECORDS = json.loads((F / "records.json").read_text())
RULES = json.loads((F / "rules.json").read_text())


def rec(name):
    return next(r for r in RECORDS if r["ProjectName"] == name)


def listing(name):
    r = rec(name)
    return nys.to_listing(r, RULES.get(r["Id"]))


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
