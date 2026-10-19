import json
from pathlib import Path

from housing_watch.adapters import pronto
from housing_watch.adapters.pronto import Pronto, ny_records, parse_property
from housing_watch.criteria import listing_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "pronto"


def load(name):
    return json.loads((F / f"{name}.json").read_text())


def prop(pid):
    return parse_property(load(pid))


def test_search_filters_pronto_ny():
    assert sorted(r["id"] for r in ny_records(load("search"))) == [4934, 7409, 10145, 12030]


def test_open_units_with_per_household_bands():
    lst = prop(7409)  # Waterside Plaza
    assert (lst.borough, lst.status, lst.kind, lst.deadline) == ("Manhattan", "open", "re-rental", "2027-08-10")
    assert lst.url == "https://prontohousingrentals.com/listings/property/7409"
    one = next(u for u in lst.units if u.unit_no == "10-18K")
    assert (one.size, one.rent, one.ami, one.available) == ("1br", 3094.0, 160, True)
    assert one.bands[0] == Band(1, 112046, 190080) and one.bands[1] == Band(2, 112046, 217120)
    two = next(u for u in lst.units if u.unit_no == "20-17A")
    assert two.size == "2br" and min(b.household for b in two.bands) == 2
    assert "waterside@prontohousing.io" in lst.notes and "registration" in lst.notes
    assert listing_verdict(lst).verdict == "eligible"


def test_open_units_plus_waitlist_tiers():
    lst = prop(4934)  # SVEN: 2 open units and a waitlist
    assert lst.borough == "Queens" and lst.status == "open"
    named = {u.unit_no: u for u in lst.units if u.unit_no}
    assert (named["21N"].size, named["21N"].rent, named["21N"].bands[0]) == ("studio", 2189.0, Band(1, 77303, 168480))
    assert named["55L"].size == "1br"
    assert any(u.size == "unknown" and u.unit_no is None for u in lst.units)
    assert listing_verdict(lst).verdict == "eligible"


def test_waitlist_without_rent_is_possible_and_junk_rows_dropped():
    lst = prop(12030)  # Aura
    assert (lst.status, lst.kind, lst.borough) == ("waitlist", "waitlist", "Queens")
    assert len(lst.units) == 2  # two real tiers; the "$100-$150" placeholder rows are gone
    t = next(u for u in lst.units if u.bands[0].household == 1)
    assert (t.size, t.rent, t.ami, t.bands[0]) == ("unknown", None, 130, Band(1, 125966, 154440))
    assert all(b.income_max > 1000 for u in lst.units for b in u.bands)
    v = listing_verdict(lst)
    assert v.verdict == "possible" and v.reason == "income fits, size unknown"
    assert listing_verdict(prop(9672)).verdict == "possible"  # Caesura


def test_waitlists_ruled_out_and_no_bands():
    assert listing_verdict(prop(9678)).reason == "over income (max $71,280)"  # House39 60% AMI
    assert listing_verdict(prop(10389)).reason == "needs household of 2+"  # Victoria Tower
    north5 = prop(10912)
    assert north5.units == [] and listing_verdict(north5).verdict == "possible"


def test_bronx_and_outside_nyc():
    east = prop(12788)
    assert east.borough == "Bronx" and listing_verdict(east).verdict == "excluded"
    assert prop(10145) is None  # Sawyer Place, Yonkers


def test_fetch_uses_cache(monkeypatch):
    calls = []

    def fake_json(url, **kw):
        calls.append(url)
        if "search" in url:
            return load("search")
        return load(url.rstrip("/").split("/")[-1])

    monkeypatch.setattr(pronto.fetch, "json", fake_json)
    monkeypatch.setattr(Pronto, "min_listings", 1)
    a = Pronto()
    first = a.fetch().listings
    assert sorted(x.ext_id for x in first) == ["12030", "4934", "7409"]
    n = len(calls)
    second = Pronto(a.cache).fetch().listings
    assert len(calls) == n + 1  # only the search was refetched
    assert [x.key() for x in second] == [x.key() for x in first]
