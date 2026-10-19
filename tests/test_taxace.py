from pathlib import Path

import pytest

from housing_watch.adapters import taxace
from housing_watch.adapters.taxace import Taxace, bands_for, parse_page
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "taxace"
HTML = (F / "projects-8.html").read_text()
DEAD = "https://forms.clickup.com/14203750/f/dhev6-8873/7YBOR67912I9V7B2ED"


@pytest.fixture(scope="module")
def listings():
    lst, _ = parse_page(HTML, {DEAD: False})
    return {x.ext_id: x for x in lst}


def bands(u):
    return [(b.household, b.income_min, b.income_max) for b in u.bands]


def test_every_card_captured(listings):
    assert len(listings) == 33
    v = [listing_verdict(x).verdict for x in listings.values()]
    assert (v.count("eligible"), v.count("excluded")) == (10, 23)  # 9 Bushwick 1BR/studios on 2025 limits


def test_2025_chart_caps_judged_on_2026_limits(listings):
    for ext in ("972-bushwick-avenue-1a", "962-bushwick-avenue-2a"):
        lst = listings[ext]
        (u,) = lst.units
        assert lst.borough == "Brooklyn" and (u.size, u.rent, u.ami) == ("1br", 2999.0, 130)
        assert bands(u) == [(1, 112218, 147420), (2, 112218, 168480), (3, 112218, 189540)] and u.chart_year == 2025
        # $150,000 is over the printed $147,420 but within 2026's $154,440 at 130%
        reason = listing_verdict(lst).units[0][2]
        assert reason == "eligible on 2026 limits (ad prints 2025 limits: max $147,420)"
        assert "2025 HUD chart" in lst.notes
    assert bands(listings["972-bushwick-avenue-7a"].units[0])[0] == (1, 104812, 147420)


def test_2026_range_split_per_household(listings):
    u = listings["69-amboy-street-4b"].units[0]  # studio printing a 3-person cap
    assert (u.size, u.unit_no, u.rent) == ("studio", "4B", 2954.0)
    assert bands(u) == [(1, 101280, 154440), (2, 101280, 176410), (3, 101280, 198510)]
    assert listing_verdict(listings["69-amboy-street-4b"]).verdict == "eligible"
    milford = listings["22-milford-street-2g"]  # 122,130 = 90% for 2 -> 1 person capped at 106,920
    assert milford.units[0].ami == 90 and bands(milford.units[0])[0] == (1, 79422, 106920)
    assert listing_verdict(milford).verdict == "excluded"


def test_bronx_and_mislabelled_boroughs(listings):
    assert listings["2187-ryer-avenue-3c"].borough == "Bronx"
    assert listings["2775-reservoir-ave-3d"].borough == "Bronx"  # labelled Brooklyn on the page
    assert listings["2710-decatur-ave-ph2"].borough == "Bronx"
    assert all(
        listing_verdict(listings[k]).reason == "in Bronx" for k in ("2187-ryer-avenue-3c", "2710-decatur-ave-ph2")
    )


def test_dead_form_closes_card(listings):
    lst = listings["2187-ryer-avenue-6d"]
    assert lst.status == "closed" and lst.units[0].available is False
    assert listings["2187-ryer-avenue-3c"].notes.startswith("Apply: ClickUp form https://forms.clickup.com/")


def test_bands_for_unknown_cap_falls_back():
    b, pct, year = bands_for("1br", "$90,000 - $200,000")
    assert (pct, year) == (None, None) and [x.household for x in b] == [1, 2, 3]


class _Resp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text


def test_fetch_checks_forms_and_caches(monkeypatch):
    calls = []

    def get(url, headers=None):
        calls.append(url)
        if "dhev6-8873" in url:
            return _Resp(403, (F / "form_inactive.json").read_text())
        return _Resp(200, (F / "form_active.json").read_text())

    monkeypatch.setattr(taxace.fetch, "html", lambda url: HTML)
    monkeypatch.setattr(taxace.fetch._client, "get", get)
    monkeypatch.setattr(taxace.fetch, "_polite", lambda url: None)
    a = Taxace()
    res = a.fetch()
    assert len(calls) == 33 and len(res.listings) == 33
    assert [x.ext_id for x in res.listings if x.status == "closed"] == ["2187-ryer-avenue-6d"]
    Taxace(a.cache).fetch()
    assert len(calls) == 33  # second run served from cache


def test_form_api_down_raises(monkeypatch):
    monkeypatch.setattr(taxace.fetch, "html", lambda url: HTML)
    monkeypatch.setattr(taxace.fetch._client, "get", lambda url, headers=None: _Resp(500, "oops"))
    monkeypatch.setattr(taxace.fetch, "_polite", lambda url: None)
    with pytest.raises(RuntimeError):
        Taxace().fetch()
