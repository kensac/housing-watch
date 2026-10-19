from pathlib import Path

import pytest

from housing_watch.adapters import sanel
from housing_watch.adapters.sanel import Sanel, parse_page
from housing_watch.criteria import listing_verdict
from housing_watch.fetch import FetchError
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "sanel"
BASE = "https://sanel.nyc/affordable-housing/"


def url(name):
    return BASE + name.replace("__", "/")


def page(name):
    return parse_page(url(name), (F / f"{name}.html").read_text())


def test_lottery_tiers_grouped_per_household():
    lst = page("queens__domi-1224sz")
    assert (lst.title, lst.borough, lst.kind, lst.status, lst.deadline) == (
        "DOMI",
        "Queens",
        "lottery",
        "open",
        "2026-11-10",
    )
    assert lst.address == "29-24 40th Ave, Queens, NY 11101"
    assert "housingconnect.nyc.gov/PublicWeb/details/7693" in lst.notes
    assert [(u.size, u.rent, u.ami) for u in lst.units] == [
        ("studio", 1842, 70),
        ("1br", 1962, 70),
        ("1br", 3784, 130),
        ("2br", 4518, 130),
        ("3br", 5200, 130),
    ]
    u130 = lst.units[2]
    assert u130.bands[:2] == [Band(1, 135360, 154440), Band(2, 135360, 176410)]
    v = listing_verdict(lst)
    assert v.verdict == "eligible"
    assert [u.ami for u, uv, _ in v.units if uv == "eligible"] == [130]


def test_multi_household_row_uses_per_size_cap():
    lst = page("manhattan__waterside-plaza-unit-10-18k-4d4ya4")
    assert lst.borough == "Manhattan" and lst.deadline == "2027-08-10"
    (u,) = lst.units
    assert (u.size, u.rent, u.ami) == ("1br", 3094, 160)
    # "1–3 people $112,046 – $244,320": $244,320 is the 3-person cap, 1 person caps at 160% = $190,080
    assert u.bands == [Band(1, 112046, 190080), Band(2, 112046, 217120), Band(3, 112046, 244320)]
    assert listing_verdict(lst).verdict == "eligible"


def test_old_year_limits_detected():
    # "130%, 1–2 people, $77,303 – $168,480": $168,480 is the 2025 2-person cap, so 1 person is ~$147k
    lst = page("queens__sven-unit-21n-jhmw82")
    (u,) = lst.units
    assert u.size == "studio" and u.bands[0].household == 1 and u.bands[0].income_max < 150000
    assert listing_verdict(lst).verdict == "excluded"


def test_bronx_excluded_by_url_borough():
    lst = page("bronx__86-west-174-st-unit-3b-1l583o")
    assert lst.borough == "Bronx" and listing_verdict(lst).reason == "in Bronx"


def test_no_tier_table_is_possible():
    lst = page("queens__apex-place-apartments-lsp339")
    assert lst.borough == "Queens" and lst.kind == "re-rental"
    assert [u.size for u in lst.units] == ["1br", "2br"]
    assert listing_verdict(lst).verdict == "possible"


def test_waitlist_without_details():
    lst = page("queens__aura-1bpj4o")
    assert (lst.kind, lst.status) == ("waitlist", "waitlist")
    assert [u.size for u in lst.units] == ["unknown"]
    assert "prontohousingrentals.com/listings/property/12030" in lst.notes
    assert listing_verdict(lst).verdict == "possible"


def test_closed_senior_sale_and_preference():
    closed = page("manhattan__lyra-waiting-list-2v6pjo")
    assert closed.status == "closed" and listing_verdict(closed).verdict == "excluded"
    senior = page("brooklyn__cabs-senior-45ors")
    assert senior.senior_only and listing_verdict(senior).reason == "senior only"
    sale = page("brooklyn__belle-gardens-gre5ps")
    assert sale.kind == "homeownership"
    # "Senior Preference 60+" is a preference, not a restriction
    pref = page("brooklyn__1718-president-street-unit-6d-sh3g4v")
    assert not pref.senior_only and listing_verdict(pref).reason == "2br not wanted"


def test_not_found_page_raises():
    with pytest.raises(ValueError):
        page("unknown-borough__1052-greene-ave-unit-4a-tuq7sq")


def test_fetch_caches_and_skips_404(monkeypatch):
    names = ["queens__domi-1224sz", "queens__aura-1bpj4o", "unknown-borough__1052-greene-ave-unit-4a-tuq7sq"]
    sitemap = "".join(f"<url><loc>{url(n)}</loc></url>" for n in names) + f"<url><loc>{BASE}queens</loc></url>"
    calls = []

    def get(u):
        calls.append(u)
        if u.startswith(BASE + "unknown-borough"):
            return None
        return (F / (u.removeprefix(BASE).replace("/", "__") + ".html")).read_text()

    monkeypatch.setattr(sanel.fetch, "html", lambda u, **kw: sitemap)
    monkeypatch.setattr(sanel, "_get", get)
    monkeypatch.setattr(sanel, "GAP_S", 0)
    monkeypatch.setattr(Sanel, "min_listings", 1)
    a = Sanel()
    assert sorted(x.title for x in a.fetch().listings) == ["Aura", "DOMI"]
    assert len(calls) == 3
    calls.clear()
    b = Sanel(a.cache)  # everything fresh: nothing refetched except the rolling share (len // 8 == 0)
    assert sorted(x.title for x in b.fetch().listings) == ["Aura", "DOMI"] and calls == []

    def boom(u):
        raise FetchError("down")

    monkeypatch.setattr(sanel, "_get", boom)
    monkeypatch.setattr(sanel, "MAX_AGE_H", -1)  # everything stale: a failed refetch keeps the cached copy
    assert sorted(x.title for x in Sanel(a.cache).fetch().listings) == ["Aura", "DOMI"]
