import json
from pathlib import Path

from housing_watch.adapters import housing_connect
from housing_watch.adapters.housing_connect import HousingConnect, parse_ad
from housing_watch.criteria import listing_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "housing_connect"


def load(name):
    return json.loads((F / f"{name}.json").read_text())


def lottery(lid):
    rec = next(r for r in load("search")["rentals"] if r["lotteryId"] == lid)
    return parse_ad(rec, load(lid))


def test_domi_130_tier_eligible():
    lst = lottery(7693)
    assert (lst.title, lst.borough, lst.kind, lst.status, lst.deadline) == (
        "DOMI",
        "Queens",
        "lottery",
        "open",
        "2026-11-10",
    )
    assert lst.address == "29-24 40th Ave, Queens"
    assert [(u.size, u.ami, u.rent) for u in lst.units] == [
        ("studio", 70, 1842.0),
        ("1br", 70, 1962.0),
        ("1br", 130, 3784.0),
        ("2br", 130, 4518.0),
        ("3br", 130, 5200.0),
    ]
    assert lst.units[2].bands[0] == Band(1, 135360, 154440)
    v = listing_verdict(lst)
    assert v.verdict == "eligible"
    assert [r for u, r_v, r in v.units if r_v == "eligible"] == ["$135,360-$154,440 for 1"]
    assert "349 Keap Street, Brooklyn, NY 11211" in lst.notes


def test_bronx_excluded_by_borough():
    lst = lottery(7607)  # One River Park: eligible 130% tiers, but in the Bronx
    assert lst.borough == "Bronx" and listing_verdict(lst).reason == "in Bronx"


def test_borough_from_zip_when_search_has_none():
    lst = lottery(7659)  # search record has borough null
    assert lst.borough == "Manhattan"
    assert listing_verdict(lst).verdict == "excluded"  # 60% / 100% AMI only


def test_set_asides_in_notes_and_low_ami_excluded():
    lst = lottery(7494)
    assert "Mobility 5%" in lst.notes and "Vision/Hearing 2%" in lst.notes
    assert lst.address == "42-78 65 PLACE, WOODSIDE" and lst.borough == "Queens"
    assert listing_verdict(lst).verdict == "excluded"


def test_units_collapse_to_tiers():
    lst = lottery(7662)  # 1 Gouverneur Lane: one row per apartment upstream
    assert len({u.key() + str(u.bands) for u in lst.units}) == len(lst.units)
    assert listing_verdict(lottery(7660)).verdict == "eligible"  # Cantilever House 130% 1BR


def test_fetch_uses_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(housing_connect.fetch, "post_json", lambda url, body, **kw: load("search"))

    def fake_json(url, **kw):
        calls.append(url)
        return load(url.split("=")[-1])

    monkeypatch.setattr(housing_connect.fetch, "json", fake_json)
    a = HousingConnect()
    first = a.fetch().listings
    assert len(first) == 6 and len(calls) == 6
    second = HousingConnect(a.cache).fetch().listings
    assert len(calls) == 6 and [x.key() for x in second] == [x.key() for x in first]
