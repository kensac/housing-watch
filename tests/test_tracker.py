from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from housing_watch import filters as F
from housing_watch import queries as Q
from housing_watch import tracker as T
from housing_watch.web.app import create_app
from seed import L, Session, add_listing, seed

SAME = {"origin": "http://testserver"}


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
    hits = Q.search_listings(synced, Q.ListingQuery([F.parse_one("application.in.To Apply", F.LISTING_FIELDS)])).items
    assert [h.key for h in hits] == ["i:1"]
    none = Q.search_listings(synced, Q.ListingQuery([F.parse_one("application.empty", F.LISTING_FIELDS)])).items
    assert "i:1" not in {h.key for h in none} and "a:1" in {h.key for h in none}
    units = Q.search_units(synced, Q.ListingQuery([F.parse_one("application.notempty", F.LISTING_FIELDS)])).items
    assert {u.key for u in units} == {"i:1"}


def test_tracker_api_and_manual_links(synced):
    c = TestClient(create_app(synced))
    t = c.get("/api/tracker").json()
    rows = {r["property"]: r for r in t["rows"]}
    assert t["configured"] and [x["key"] for x in rows["ONE LIC (42-10 27th St)"]["listings"]] == ["i:1"]
    assert rows["SVEN"]["listings"] == [] and rows["SVEN"]["page_url"] == "https://svenlic.com/"
    sven = T.row_key("SVEN")
    assert c.post("/api/tracker/links", json={"listing_key": "a:1", "row_key": sven}).status_code == 403
    assert c.post("/api/tracker/links", json={"listing_key": "a:1", "row_key": sven}, headers=SAME).status_code == 200
    assert c.get("/api/listing?key=a%3A1").json()["summary"]["application"] == "Applied"
    assert c.post("/api/tracker/links", json={"listing_key": "a:1", "row_key": "nope"}, headers=SAME).status_code == 404
    assert c.post("/api/tracker/links", json={"listing_key": "i:1", "row_key": None}, headers=SAME).status_code == 200
    assert c.get("/api/listing?key=i%3A1").json()["summary"]["application"] is None  # "not this row"
    assert c.delete("/api/tracker/links?listing_key=i%3A1", headers=SAME).status_code == 204
    assert c.get("/api/listing?key=i%3A1").json()["summary"]["application"] == "To Apply"  # automatic again
    lst = c.get("/api/listings?f=application.in.Applied").json()
    assert [x["key"] for x in lst["items"]] == ["a:1"] and lst["items"][0]["tracker_match"] == "manual"


def test_sync_is_off_unconfigured(pg, monkeypatch):
    monkeypatch.setattr(T, "SHEET", "")
    assert T.sync(pg) == 0
