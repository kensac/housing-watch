import datetime as dt
import json
from pathlib import Path

from housing_watch.adapters import afny
from housing_watch.adapters.afny import AFNY, parse_detail
from housing_watch.criteria import listing_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "afny"
TODAY = dt.date(2026, 10, 19)


def rec(i):
    return json.loads((F / f"{i}.json").read_text())["data"]["record"]


def test_the_88_units_and_tiers():
    lst = parse_detail("afny", "https://afny.org", rec("3319305"), TODAY)
    assert lst.borough == "Queens" and lst.url == "https://afny.org/re-rentals/3319305"
    assert [(u.size, u.unit_no, u.rent, u.ami) for u in lst.units] == [
        ("1br", "527", 2630.2, 130),
        ("1br", "227", 2499.0, 130),
        ("studio", "606", 2157.75, 130),
        ("studio", "406", 2157.75, 130),
        ("2br", "400", 2073.5, 80),
        ("studio", "617", 1628.59, 80),
    ]
    assert lst.units[0].bands[0] == Band(1, 96075, 154440)
    assert lst.units[-1].bands[0] == Band(1, 60980, 95040)
    assert "365 Flushing Avenue Suite 503" in lst.notes and "G-1" in lst.notes
    v = listing_verdict(lst)
    assert v.verdict == "eligible"
    assert [uv for _, uv, _ in v.units] == ["eligible"] * 4 + ["excluded"] * 2


def test_75_dupont_single_unit():
    lst = parse_detail("afny", "https://afny.org", rec("2995707"), TODAY)
    (u,) = lst.units
    assert (u.size, u.unit_no, u.rent, u.bands[0]) == ("1br", "421", 3661.0, Band(1, 131143, 154440))
    assert lst.borough == "Brooklyn" and listing_verdict(lst).verdict == "eligible"


def test_stale_end_date_is_not_a_deadline():
    lst = parse_detail("afny", "https://afny.org", rec("3399348"), TODAY)
    assert lst.deadline is None and lst.status == "open"
    assert len(lst.units) == 22 and "streeteasy.com" in lst.notes


def test_fetch_uses_origin_and_caches(monkeypatch):
    calls = []

    def fake_json(url, headers=None, **kw):
        calls.append((url, headers))
        assert headers["Origin"] == "https://afny.org"
        if url.endswith("page=0"):
            return json.loads((F / "list.json").read_text())
        return json.loads((F / f"{url.rstrip('/').split('/')[-1]}.json").read_text())

    monkeypatch.setattr(afny.fetch, "json", fake_json)
    a = AFNY({})
    res = a.fetch()
    assert len(res.listings) == 3 and len(calls) == 4
    assert calls[1][1]["Referer"].startswith("https://afny.org/re-rentals/")
    res2 = AFNY(a.cache).fetch()  # same cards today: no detail refetch
    assert len(calls) == 5 and [x.ext_id for x in res2.listings] == [x.ext_id for x in res.listings]
    assert res2.listings[1].units[0].bands == res.listings[1].units[0].bands
