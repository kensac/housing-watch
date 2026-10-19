from pathlib import Path

import pytest

from housing_watch.adapters import tax_solute
from housing_watch.adapters.tax_solute import TaxSolute, parse_page
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "tax_solute"


@pytest.fixture(scope="module")
def listings():
    lst, _ = parse_page((F / "home.html").read_text())
    return {x.ext_id: x for x in lst}


def bands(u):
    return [(b.household, b.income_min, b.income_max) for b in u.bands]


def test_every_card_captured(listings):
    assert len(listings) == 17
    assert sum(listing_verdict(x).verdict == "eligible" for x in listings.values()) == 10


def test_rockwell_place_studio_and_1br(listings):
    one = listings["19-rockwell-place-1"]
    studio = listings["19-rockwell-place-studio"]
    assert one.borough == studio.borough == "Brooklyn" and one.status == studio.status == "open"
    (u,) = one.units
    assert (u.size, u.rent, u.ami) == ("1br", 3661.0, 130)
    assert bands(u) == [(1, 131143, 154440), (2, 131143, 176410), (3, 131143, 198510)]
    (s,) = studio.units
    assert (s.size, s.rent) == ("studio", 3423.0)  # "$3, 423" split across spans
    assert bands(s)[0] == (1, 122298, 154440)  # "$1 22,298 .00" split across spans
    assert listing_verdict(one).verdict == listing_verdict(studio).verdict == "eligible"
    assert "670 Flushing Avenue, Suite 316" in one.notes and "export=download&id=" in one.notes


def test_queens_and_clarkson(listings):
    q = listings["214-33-41st-ave-1"]
    assert q.borough == "Queens" and q.units[0].rent == 3200.0 and listing_verdict(q).verdict == "eligible"
    assert listing_verdict(listings["214-33-41st-ave-2"]).verdict == "excluded"  # 2BR
    c = listings["50-clarkson-ave-1"]
    assert (c.units[0].rent, bands(c.units[0])[0]) == (2418.87, (1, 92532, 154440))
    assert listing_verdict(c).verdict == "eligible"


def test_tiers_holds_and_typos(listings):
    rocklyn = listings["the-rocklyn-0-2"]
    assert [(u.size, u.rent) for u in rocklyn.units] == [("2br", 4244.0), ("1br", 3595.0), ("studio", 3385.0)]
    assert "Initial lease-up" in rocklyn.notes and rocklyn.notes.count("drive.google.com") == 3
    fourth = listings["584-4th-ave-1"]  # "$154.440.00" typo; photo/floor-plan links are not applications
    assert fourth.status == "waitlist" and bands(fourth.units[0])[0] == (1, 119038, 154440)
    assert fourth.notes.count("drive.google.com") == 1
    assert listings["351-east-10th-st-1"].borough == "Manhattan"
    bronx = listings["1412-college-ave-0"]
    assert bronx.borough == "Bronx" and listing_verdict(bronx).verdict == "excluded"


def test_empty_page_snapshot(monkeypatch):
    html = "<html><body><p>Available Affordable Housing Units</p><p>" + "No units right now. " * 20 + "</p>"
    monkeypatch.setattr(tax_solute.fetch, "html", lambda url: html + "</body></html>")
    res = TaxSolute().fetch()
    assert not res.listings and len(res.snapshots) == 1


def test_blocked_page_raises(monkeypatch):
    monkeypatch.setattr(tax_solute.fetch, "html", lambda url: "<html><body>Access denied</body></html>")
    with pytest.raises(RuntimeError):
        TaxSolute().fetch()
