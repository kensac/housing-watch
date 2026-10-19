from pathlib import Path

import pytest

from housing_watch.adapters import affordablehousing_com as ah
from housing_watch.criteria import Profile, listing_verdict, unit_verdict

F = Path(__file__).parent / "fixtures" / "affordablehousing_com"
NO_BRONX = Profile(household=1, income=150000, sizes=frozenset({"studio", "1br"}), excluded_boroughs=frozenset())


def cards():
    out = {}
    for county in ("kings-county-ny", "bronx-county-ny"):
        _, cs = ah.parse_search((F / f"{county}.html").read_text())
        out.update({c["id"]: c for c in cs})
    return out


def detail(cid):
    return ah.parse_detail(cards()[cid], (F / f"{cid}.html").read_text())


def test_search_page_cards():
    total, cs = ah.parse_search((F / "kings-county-ny.html").read_text())
    assert total == 21 and len(cs) == 21
    c = next(c for c in cs if c["id"] == "800990")
    assert c["url"] == "https://www.affordablehousing.com/brooklyn-ny/280-e-21st-st-800990/"
    assert (c["price"], c["avail"], c["address"]) == (
        "$1,835",
        "Available Now",
        "280 E 21st St, 6F, Brooklyn, NY 11226",
    )
    sea = next(c for c in cs if c["id"] == "980978")
    assert sea["price"] == "$1,714 - $2,867" and sea["comment"].startswith("Sea Park Affordable Housing")


def test_unfiltered_page_is_refused():
    with pytest.raises(RuntimeError, match="filter not applied"):
        ah.parse_search('<h1 id="MainContentPlaceHolder_lblHeading">Rental Properties in Kings County, NY</h1>')


def test_building_with_per_household_caps():
    lst = detail("980978")
    assert lst.title == "Sea Park (2930 W 30th St, Brooklyn, NY 11224)" and lst.borough == "Brooklyn"
    assert [(u.size, u.rent, u.ami) for u in lst.units] == [
        ("1br", 1714.0, 60),
        ("2br", 2045.0, 60),
        ("3br", 2351.0, 60),
        ("4br", 2610.0, 60),
        ("5br", 2867.0, 60),
    ]
    assert [(b.household, b.income_min, b.income_max) for b in lst.units[0].bands] == [
        (1, None, 68040),
        (2, None, 77760),
        (3, None, 87480),
    ]
    v = listing_verdict(lst)
    assert v.verdict == "excluded" and v.units[0][2] == "over income (max $68,040)"


def test_single_unit_ami_range():
    lst = detail("1084028")
    assert lst.borough == "Bronx" and lst.status == "open" and not lst.senior_only
    (u,) = lst.units
    assert (u.size, u.rent, u.ami, u.unit_no) == ("studio", 786.0, 130, "12E")
    # "130% AMI Income Min-Max: $26,944.80 - $176,410": the top is a 2-person cap; 1 person gets 130% of AMI100[1]
    assert [(b.household, b.income_min, b.income_max) for b in u.bands] == [(1, 26944, 154440)]
    assert listing_verdict(lst).verdict == "excluded"  # Bronx
    assert listing_verdict(lst, NO_BRONX).verdict == "eligible"


def test_range_without_ami_bounds_one_person():
    (u,) = detail("1013542").units
    assert (u.size, u.rent, u.ami) == ("1br", 1822.0, None)
    assert [(b.household, b.income_min, b.income_max) for b in u.bands] == [(1, 62454, 114525)]
    assert unit_verdict(u, NO_BRONX) == ("excluded", "over income (max $114,525)")


def test_age_restricted_flag_is_senior():
    lst = detail("800990")
    assert lst.senior_only and lst.units[0].rent == 1835.0 and lst.units[0].unit_no == "6F"
    assert listing_verdict(lst).reason == "senior only"


def test_disability_set_aside():
    (u,) = detail("1074392").units
    assert (u.size, u.set_aside) == ("2br", "disability")


def test_studio_without_income_data_is_possible():
    lst = detail("709906")
    (u,) = lst.units
    assert (u.size, u.rent, u.unit_no, u.bands) == ("studio", 2624.0, "902-217", [])
    assert listing_verdict(lst).verdict == "possible"


def test_waiting_list_takes_rent_from_card():
    lst = detail("1051684")
    assert lst.status == "waitlist" and lst.kind == "waitlist"
    assert [(u.size, u.rent, u.unit_no) for u in lst.units] == [("1br", 3150.0, "17F")]
    assert listing_verdict(lst).verdict == "possible"


def test_fetch_paginates_caches_and_drops_unrestricted(monkeypatch):
    calls = []

    def fake_html(url, **kw):
        calls.append(url)
        for county in ("kings-county-ny", "bronx-county-ny"):
            if url == f"{ah.BASE}/{county}/income-restricted/":
                return (F / f"{county}.html").read_text()
        if "/income-restricted/" in url:
            return '<h1 id="MainContentPlaceHolder_lblHeading">Income Restricted Rental Properties</h1>0 Rentals'
        cid = url.rstrip("/").rsplit("-", 1)[-1]
        p = F / f"{cid}.html"
        return (
            p.read_text() if p.exists() else "<script>propertyDetailsModel.domain.isIncomeRestricted(false);</script>"
        )

    monkeypatch.setattr(ah.fetch, "html", fake_html)
    a = ah.AffordableHousingCom()
    res = a.fetch()
    got = {lst.ext_id for lst in res.listings}
    assert got == {"980978", "1084028", "800990", "1013542", "1074392", "709906", "1051684"}
    assert len(a.cache["detail"]) == 30  # every card cached, unrestricted ones as None
    first = len(calls)
    assert first == 5 + 30

    calls.clear()
    again = ah.AffordableHousingCom(a.cache).fetch()
    assert len(calls) == 5  # search pages only: details come from the cache
    assert {lst.ext_id for lst in again.listings} == got
    sea = next(x for x in again.listings if x.ext_id == "980978")
    assert sea.units[0].bands[0].income_max == 68040
