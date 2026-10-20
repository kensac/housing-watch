from datetime import datetime
from zoneinfo import ZoneInfo

from housing_watch import commute as C
from housing_watch import filters as F
from housing_watch import queries as Q
from seed import seed

NY = ZoneInfo("America/New_York")

RESP = {
    "routes": [
        {
            "duration": "2040s",
            "legs": [
                {
                    "steps": [
                        {"travelMode": "WALK", "staticDuration": "300s"},
                        {"travelMode": "TRANSIT", "staticDuration": "900s",
                         "transitDetails": {"transitLine": {"nameShort": "A", "vehicle": {"type": "SUBWAY"}}}},
                        {"travelMode": "TRANSIT", "staticDuration": "600s",
                         "transitDetails": {"transitLine": {"name": "Q32", "vehicle": {"type": "BUS"}}}},
                        {"travelMode": "WALK", "staticDuration": "120s"},
                    ]
                }
            ],
        }
    ]
}  # fmt: skip


def test_origin():
    assert C.origin("260 Knickerbocker Ave, Brooklyn, NY", "Brooklyn") == "260 Knickerbocker Ave, Brooklyn, NY"
    assert C.origin("  264 Sullivan Place –", "Brooklyn") == "264 Sullivan Place, Brooklyn, NY"
    assert C.origin("1134 Fulton Street", None) is None and C.origin(None, "Queens") is None


def test_departure_is_the_next_weekday_morning():
    fri_noon = datetime(2026, 10, 23, 12, 0, tzinfo=NY)
    assert C.departure(fri_noon) == datetime(2026, 10, 26, 8, 30, tzinfo=NY)  # Monday
    tue_early = datetime(2026, 10, 20, 7, 0, tzinfo=NY)
    assert C.departure(tue_early) == datetime(2026, 10, 20, 8, 30, tzinfo=NY)


def test_summarize():
    assert C.summarize(RESP) == (34, {"lines": ["A", "Q32"], "walk_min": 7, "transfers": 1})
    assert C.summarize({}) == (None, {})
    assert C.line_name("F Line") == "F" and C.line_name("M34A-SBS") == "M34A-SBS"


def test_refresh_routes_only_eligible_listings_once(pg, monkeypatch):
    seed(pg)  # a:1 is the only eligible listing (1 Main St, Queens)
    monkeypatch.setattr(C, "DESTINATION", "1 Work Pl, New York, NY")
    monkeypatch.setattr(C, "KEY", "test")
    sent = []
    monkeypatch.setattr(C, "route", lambda src, dest, depart: sent.append(src) or RESP)
    assert C.refresh(pg) == 1 and sent == ["1 Main St, Queens, NY"]
    assert C.refresh(pg) == 0  # fresh: not routed again
    (x,) = [x for x in Q.judged(pg) if x.key == "a:1"]
    assert x.commute == 34 and x.commute_route["lines"] == ["A", "Q32"]
    assert all(y.commute is None for y in Q.judged(pg) if y.key != "a:1")
    hits = Q.search_listings(pg, Q.ListingQuery([F.parse_one("commute.le.40", F.LISTING_FIELDS)])).items
    assert [h.key for h in hits] == ["a:1"]
    assert Q.listing_detail(pg, "a:1").summary.commute == 34  # the drawer too
    units = Q.search_units(pg, Q.ListingQuery(sort="commute")).items
    assert units[0].key == "a:1" and units[0].commute == 34


def test_refresh_is_off_without_key_or_destination(pg, monkeypatch):
    seed(pg)
    monkeypatch.setattr(C, "KEY", "")
    assert C.refresh(pg) == 0
