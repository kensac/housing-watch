from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from housing_watch import filters as F
from housing_watch import queries as Q
from housing_watch import tracker as T
from housing_watch.orm import TrackerEventRow, TrackerRow
from housing_watch.web.app import create_app
from seed import L, Session, add_listing, gql, gql_error, nodes, seed

SAME = {"origin": "http://testserver"}
TRACKER = """{ tracker { configured rows { property status stage group stage_since stage_since_baseline page_url
  cells { name value link } events { at stage_from stage_to group } listings { key tracker_match } } } }"""
LINK = """mutation($k: ID!, $r: ID) {
  linkTracker(input: { listing_key: $k, row_key: $r }) { listing_key row_key } }"""
UNLINK = """mutation($k: ID!) {
  unlinkTracker(input: { listing_key: $k }) { listing_key row_key listing { application tracker_match } } }"""
APPLICATION = "query($k: ID!) { listing(key: $k) { summary { application } } }"
LISTINGS = """query($filter: ListingFilterInput, $sort: [ListingSortInput!]) {
  listings(filter: $filter, sort: $sort) { edges { node { key tracker_match application_since } } } }"""


def application(c, key: str) -> str | None:
    return gql(c, APPLICATION, k=key)["data"]["listing"]["summary"]["application"]


def cell(text, link=None, rich=None):
    v = {"formattedValue": text}
    if link:
        v["hyperlink"] = link
    if rich:
        v["textFormatRuns"] = [{"startIndex": 0, "format": {"link": {"uri": rich}}}]
    return v


HEAD = ["Property", "Status", "Apply / Source", "Borough", "Notes", "Property Page"]
SHEET = {"sheets": [{"data": [{"rowData": [
    {"values": [cell(h) for h in HEAD]},
    {"values": [cell("SVEN"), cell("Applied"), cell("NYS Housing Search", "https://example.org/portal/"),
                cell("Queens"), cell("Log #8984."), cell("Official property", rich="https://svenlic.com/")]},
    {"values": [cell("ONE LIC (42-10 27th St)"), cell("To Apply"), cell("iAfford", "https://iaffordny.com/re-rentals/1")]},
    {"values": [cell("Ruby (241 West 28th Street)"), cell("Contacted"), cell("NYC Housing Partnership")]},
    {"values": [cell("")]},
    {"values": [cell("SVEN"), cell("Seen")]},
]}]}]}  # fmt: skip


def test_parse_reads_text_links_and_skips_blank_rows():
    rows = T.parse(SHEET)
    assert [r["Property"] for r in rows] == ["SVEN", "ONE LIC (42-10 27th St)", "Ruby (241 West 28th Street)", "SVEN"]
    assert rows[0]["Apply / Source__link"] == "https://example.org/portal/" and rows[0]["_row"] == 2
    assert rows[0]["Property Page__link"] == "https://svenlic.com/"  # a rich-text link
    with pytest.raises(ValueError):
        T.parse({"sheets": [{"data": [{"rowData": [{"values": [cell("Name")]}]}]}]})


def test_keys():
    assert T.addr_keys("ONE LIC (42-10 27th St)") == {"42-10 27 st"}
    assert T.addr_keys("241 West 28th Street, New York, NY 10001") == {"241 w 28 st"}
    assert T.addr_keys("ONE LIC Multiple units available 0926") == set()
    assert T.name_key("The Cove (43-12 Hunter St, LIC)") == "cove" and T.name_key("Apartments") is None
    assert T.url_key("https://www.IAffordNY.com/re-rentals/1/?x=1") == "iaffordny.com/re-rentals/1"


def test_match():
    rows = [SimpleNamespace(row_key=T.row_key(p), property=p, status=s, notes=None, source_url=u)
            for p, s, u in [("SVEN", "Applied", None), ("ONE LIC (42-10 27th St)", "To Apply", "https://iaffordny.com/re-rentals/1"),
                            ("Ruby (241 West 28th Street)", "Contacted", None)]]  # fmt: skip

    def x(key, title, address=None, url=""):
        return SimpleNamespace(key=key, title=title, address=address, url=url)

    listings = [
        x("a", "SVEN #21N", "29-59 Northern Blvd"),
        x("b", "ONE LIC #13D", url="https://iaffordny.com/re-rentals/1/"),
        x("c", "ONE LIC Multiple units", "42-10 27th St, Queens"),
        x("d", "Ruby", "241 W 28th St, New York, NY"),
        x("e", "Unrelated", "1 Main St"),
    ]
    got = T.match(listings, rows, {"d": None})
    assert {k: (r.status, r.how) for k, r in got.items()} == {
        "a": ("Applied", "name"),
        "b": ("To Apply", "url"),
        "c": ("To Apply", "address"),
    }  # d: the owner said "not this row"; e: nothing matches
    assert T.match([x("e", "Unrelated")], rows, {"e": T.row_key("SVEN")})["e"].how == "manual"
    portal = [
        SimpleNamespace(
            row_key="p", property="Portal row", status="Applied", notes=None, source_url="https://agent.example/"
        )
    ]
    shared = [x("f", "Building F", url="https://agent.example/"), x("g", "Building G", url="https://agent.example/")]
    assert T.match(shared, portal, {}) == {}  # a page many listings share identifies none of them


@pytest.fixture
def synced(pg, monkeypatch, tmp_path):
    seed(pg)
    with Session(pg) as s, s.begin():
        add_listing(s, L("i", "1", "Queens", address="42-10 27th St, Queens, NY", status="open"))
    key = tmp_path / "k.json"
    key.write_text("{}")
    monkeypatch.setattr(T, "SHEET", "sheet-id")
    monkeypatch.setattr(T, "KEY_FILE", str(key))
    monkeypatch.setattr(T, "_token", lambda: "tok")
    monkeypatch.setattr(T.fetch, "get", lambda url, headers=None: SimpleNamespace(json=lambda: SHEET))
    assert T.sync(pg) == 3  # the second SVEN row is a duplicate
    return pg


def test_sync_and_read(synced):
    (x,) = [x for x in Q.judged(synced) if x.key == "i:1"]
    assert (x.application, x.tracker_match, x.tracker_row) == ("To Apply", "address", "ONE LIC (42-10 27th St)")
    hits = Q.search_listings(
        synced, Q.ListingQuery(F.from_tree({"application": {"in": ["To Apply"]}}, F.LISTING_FIELDS))
    ).items
    assert [h.key for h in hits] == ["i:1"]
    none = Q.search_listings(
        synced, Q.ListingQuery(F.from_tree({"application": {"empty": True}}, F.LISTING_FIELDS))
    ).items
    assert "i:1" not in {h.key for h in none} and "a:1" in {h.key for h in none}
    units = Q.search_units(
        synced, Q.ListingQuery(F.from_tree({"application": {"empty": False}}, F.LISTING_FIELDS))
    ).items
    assert {u.key for u in units} == {"i:1"}


def test_tracker_api_and_manual_links(synced):
    c = TestClient(create_app(synced))
    t = gql(c, TRACKER)["data"]["tracker"]
    rows = {r["property"]: r for r in t["rows"]}
    assert t["configured"] and [x["key"] for x in rows["ONE LIC (42-10 27th St)"]["listings"]] == ["i:1"]
    assert rows["SVEN"]["listings"] == [] and rows["SVEN"]["page_url"] == "https://svenlic.com/"
    one = rows["ONE LIC (42-10 27th St)"]
    assert one["group"] == "your_move" and one["events"][0]["group"] == "your_move"
    src = {"name": "Apply / Source", "value": "NYS Housing Search", "link": "https://example.org/portal/"}
    assert {"name": "Borough", "value": "Queens", "link": None} in rows["SVEN"]["cells"] and src in rows["SVEN"][
        "cells"
    ]
    assert one["listings"][0]["tracker_match"] == "address"
    sven = T.row_key("SVEN")
    assert gql_error(gql(c, LINK, k="a:1", r=sven)) == "cross-origin request refused"
    assert gql(c, LINK, SAME, k="a:1", r=sven)["data"]["linkTracker"] == {"listing_key": "a:1", "row_key": sven}
    assert application(c, "a:1") == "Applied"
    assert gql_error(gql(c, LINK, SAME, k="a:1", r="nope")) == "No such tracker row"
    assert gql_error(gql(c, LINK, SAME, k="zz:9", r=sven)) == "No such listing"
    assert gql(c, LINK, SAME, k="i:1", r=None)["data"]["linkTracker"]["row_key"] is None
    assert application(c, "i:1") is None  # "not this row"
    assert gql_error(gql(c, UNLINK, k="i:1")) == "cross-origin request refused"
    un = gql(c, UNLINK, SAME, k="i:1")["data"]["unlinkTracker"]
    assert un["row_key"] == T.row_key("ONE LIC (42-10 27th St)") and un["listing"]["tracker_match"] == "address"
    assert application(c, "i:1") == "To Apply"  # automatic again
    assert gql_error(gql(c, UNLINK, SAME, k="i:1")) == "No manual link for that listing"
    lst = gql(c, LISTINGS, filter={"application": {"in": ["Applied"]}})["data"]["listings"]
    assert [x["key"] for x in nodes(lst)] == ["a:1"] and nodes(lst)[0]["tracker_match"] == "manual"


def test_sync_is_off_unconfigured(pg, monkeypatch):
    monkeypatch.setattr(T, "SHEET", "")
    assert T.sync(pg) == 0


def test_units():
    assert T.units("115 Stanwix St, Unit 218") == {"218"}
    assert T.units("2 Blue Slip, Units 12N / 14N, Greenpoint") == {"12N", "14N"}
    assert T.units("69 Amboy St 4B") == {"4B"} and T.units("2710 Decatur Ave PH2") == {"PH2"}
    assert T.units("2710 Decatur Ave #PH2") == {"PH2"} and T.units("Units #7D, #13D.") == {"7D", "13D"}
    assert T.units("Waterside Plaza #10-03G") == {"10-03G"} and T.units("SVEN") == set()
    assert T.addr_keys("69 Amboy St 4B") == {"69 amboy st"}


def test_a_unit_row_does_not_cover_other_units():
    rows = [SimpleNamespace(row_key=T.row_key(p), property=p, status=s, notes=n, source_url=None)
            for p, s, n in [("115 Stanwix St, Unit 218", "Interest Form Filled", None),
                            ("2710 Decatur Ave, Unit 1B, Bronx", "To Apply", None),
                            ("2710 Decatur Ave PH2", None, None),
                            ("ONE LIC (42-10 27th St)", "To Apply", "Units #7D, #13D.\nApplication: paper."),
                            ("Waterside Plaza", "Applied", None),
                            ("SVEN", "Applied", "Log #8984.\nRents checked.")]]  # fmt: skip

    def x(key, title, address=None):
        return SimpleNamespace(key=key, title=title, address=address, url="")

    got = T.match([x("s218", "115 Stanwix Street Apartment – Unit 218", "115 Stanwix Street"),
                   x("s302", "115 Stanwix Street Apartment – Unit 302", "115 Stanwix Street"),
                   x("d1b", "2710 Decatur Avenue, Unit 1B", "2710 Decatur Avenue, Bronx, NY"),
                   x("dph", "2710 Decatur Ave #PH2", "2710 Decatur Ave, Bronx, NY 10458"),
                   x("o13", "ONE LIC Multiple units available 0926 #13D", "42-10 27th St, Queens"),
                   x("o9", "ONE LIC #9A", "42-10 27th St, Queens"),
                   x("w", "Waterside Plaza #10-03G"), x("sv", "SVEN #21N"),
                   x("sb", "115 Stanwix Street, Brooklyn NY, 11206", "115 Stanwix Street")], rows, {})  # fmt: skip
    assert {k: r.property for k, r in got.items()} == {
        "s218": "115 Stanwix St, Unit 218",
        "d1b": "2710 Decatur Ave, Unit 1B, Bronx",
        "dph": "2710 Decatur Ave PH2",
        "o13": "ONE LIC (42-10 27th St)",
        "w": "Waterside Plaza",  # a building-wide row covers every unit
        "sv": "SVEN",  # "Log #8984" in the notes is not a unit
        "sb": "115 Stanwix St, Unit 218",  # a listing naming no unit takes its building's row
    }  # s302 and o9 are other units of buildings whose rows name specific units


def test_stages_and_legacy_statuses():
    assert [T.stage(s) for s in ("To Apply", " ready  to send ", "OFFER", "", None)] == [
        "To Apply", "Ready to Send", "Offer", None, None,
    ]  # fmt: skip
    legacy = ["Interest Form Filled", "Contacted", "Under Review", "Appeal Under Review", "Rejected", "Not interested"]
    assert [T.stage(s) for s in legacy] == [
        "Applied", "Form Requested", "In Processing", "In Processing", "Closed", "Closed",
    ]  # fmt: skip
    assert T.stage("Seen") is None and T.stage("Waiting on landlord") == "Waiting on landlord"  # unknown: as written
    assert [T.group(s) for s in ("Action Needed", "Tour Scheduled", "Offer", "Waiting on landlord", None)] == [
        T.YOU, T.THEM, T.DONE, None, None,
    ]  # fmt: skip
    ranked = sorted([None, "Closed", "Waiting on landlord", "Applied", "To Apply", "Offer"], key=T.rank)
    assert ranked == ["To Apply", "Applied", "Offer", "Closed", "Waiting on landlord", None]


def sheet_of(rows: list[tuple[str, str]]) -> dict:
    head = {"values": [cell("Property"), cell("Status"), cell("Notes")]}
    body = [{"values": [cell(p), cell(s), cell(f"Note for {p}.")]} for p, s in rows]
    return {"sheets": [{"data": [{"rowData": [head, *body]}]}]}


@pytest.fixture
def sheet(pg, monkeypatch, tmp_path):
    """Listings t:1..t:5 (invented addresses) and a sheet whose rows a test sets before each `sync()`."""
    with Session(pg) as s, s.begin():
        for n, addr in enumerate(["10 Elm St", "20 Oak Ave", "30 Pine Rd", "1 Nowhere Ln", "40 Cedar Pl"], start=1):
            add_listing(s, L("t", str(n), "Queens", address=f"{addr}, Queens, NY"))
    key = tmp_path / "k.json"
    key.write_text("{}")
    monkeypatch.setattr(T, "SHEET", "sheet-id")
    monkeypatch.setattr(T, "KEY_FILE", str(key))
    monkeypatch.setattr(T, "_token", lambda: "tok")
    box = SimpleNamespace(rows=[], engine=pg, sync=lambda: T.sync(pg))
    monkeypatch.setattr(T.fetch, "get", lambda url, headers=None: SimpleNamespace(json=lambda: sheet_of(box.rows)))
    return box


def tracker_events(pg) -> list[tuple]:
    with Session(pg) as s:
        q = select(TrackerEventRow).order_by(TrackerEventRow.id)
        return [(e.row_key, e.status_from, e.status_to, e.baseline) for e in s.scalars(q)]


def test_sync_records_status_changes(sheet):
    pg = sheet.engine
    with Session(pg) as s, s.begin():  # a row synced before events existed
        s.add(TrackerRow(row_key="10 elm st", position=2, property="10 Elm St", status="Contacted", cells={}))
    sheet.rows = [("10 Elm St", "Contacted"), ("20 Oak Ave", "To Apply")]
    sheet.sync()
    assert tracker_events(pg) == [("10 elm st", None, "Contacted", True), ("20 oak ave", None, "To Apply", False)]
    sheet.sync()  # nothing changed: no event
    assert len(tracker_events(pg)) == 2
    sheet.rows = [("10 Elm St", "Form Requested"), ("20 Oak Ave", "Applied")]
    sheet.sync()
    assert tracker_events(pg)[2:] == [("10 elm st", "Contacted", "Form Requested", False),
                                      ("20 oak ave", "To Apply", "Applied", False)]  # fmt: skip
    hist = T.history(pg)  # Contacted -> Form Requested is a rename, not a new stage: the baseline still holds
    assert [(h.stage_from, h.stage_to, h.baseline) for h in hist["10 elm st"]] == [(None, "Form Requested", True)]
    assert [(h.stage_from, h.stage_to) for h in hist["20 oak ave"]] == [(None, "To Apply"), ("To Apply", "Applied")]
    sheet.rows = [("20 Oak Ave", "Applied")]  # a row removed from the sheet records nothing and keeps its events
    sheet.sync()
    assert len(tracker_events(pg)) == 4 and "10 elm st" in T.history(pg)
    sheet.rows = [("20 Oak Ave", "Applied"), ("10 Elm St", "Form Requested")]  # back, unchanged: still nothing
    sheet.sync()
    assert len(tracker_events(pg)) == 4


def test_stage_order_group_filter_and_api(sheet):
    pg = sheet.engine
    sheet.rows = [("10 Elm St", "Rejected"), ("20 Oak Ave", "Action Needed"), ("30 Pine Rd", "Applied"),
                  ("40 Cedar Pl", "Waiting on landlord"), ("50 Spruce St", "Applied")]  # fmt: skip
    sheet.sync()
    with pg.begin() as c:  # Spruce has been Applied for longer than Pine
        c.execute(update(TrackerEventRow).where(TrackerEventRow.row_key == "50 spruce st")
                  .values(at=datetime(2026, 10, 1, tzinfo=UTC)))  # fmt: skip
    got = Q.search_listings(pg, Q.ListingQuery(sort=(Q.Sort("application"),))).items
    assert [(x.key, x.application, x.application_group) for x in got] == [
        ("t:2", "Action Needed", T.YOU), ("t:3", "Applied", T.THEM), ("t:1", "Closed", T.DONE),
        ("t:5", "Waiting on landlord", None), ("t:4", None, None),
    ]  # fmt: skip
    assert got[0].application_since and got[0].application_since_baseline is False
    assert Q.search_listings(pg, Q.ListingQuery(sort=(Q.Sort("application", "desc"),))).items[0].key == "t:4"
    units = Q.search_units(pg, Q.ListingQuery(sort=(Q.Sort("application"),))).items
    assert [u.key for u in units] == ["t:2", "t:3", "t:1", "t:5", "t:4"] and units[0].application_group == T.YOU
    mine = F.from_tree({"application_group": {"in": ["your move", "done"]}}, F.LISTING_FIELDS)
    assert {x.key for x in Q.search_listings(pg, Q.ListingQuery(mine)).items} == {"t:1", "t:2"}
    with pytest.raises(F.FilterError):
        F.from_tree({"application_group": {"in": ["mine"]}}, F.LISTING_FIELDS)
    counts = Q.value_counts(pg, "listings", F.LISTING_FIELDS["application"], [])
    assert [v for v, _ in counts][:9] == list(T.STAGES) and dict(counts)["Applied"] == 1
    assert {v for v, _ in counts[9:]} == {"Waiting on landlord", None}  # unknown statuses after the stages

    c = TestClient(create_app(pg))
    theirs = {"application_group": {"in": ["their_move"]}}
    lst = gql(c, LISTINGS, sort=[{"field": "application"}], filter=theirs)["data"]["listings"]
    assert [x["key"] for x in nodes(lst)] == ["t:3"] and nodes(lst)[0]["application_since"]
    units = gql(c, "{ units(sort: [{ field: application }]) { edges { node { application_group } } } }")["data"][
        "units"
    ]
    assert nodes(units)[0]["application_group"] == "your_move"
    rows = gql(c, TRACKER)["data"]["tracker"]["rows"]
    assert [r["property"] for r in rows] == ["20 Oak Ave", "50 Spruce St", "30 Pine Rd", "10 Elm St", "40 Cedar Pl"]
    elm = rows[3]
    assert (elm["status"], elm["stage"], elm["group"]) == ("Rejected", "Closed", "done")
    assert [(e["stage_from"], e["stage_to"]) for e in elm["events"]] == [(None, "Closed")]
    assert elm["stage_since"] == elm["events"][0]["at"] and elm["stage_since_baseline"] is False
