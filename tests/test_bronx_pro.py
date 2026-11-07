from pathlib import Path

import pytest

from housing_watch.adapters import bronx_pro
from housing_watch.adapters.bronx_pro import BronxPro, parse_page
from housing_watch.eligibility import listing_verdict
from housing_watch.models import Band

HTML = (Path(__file__).parent / "fixtures" / "bronx_pro" / "apartments.html").read_text()
APP = "https://bronxprogroup.com/s/General-Housing-Application.pdf"


@pytest.fixture(scope="module")
def listings():
    return {lst.ext_id: lst for lst in parse_page(HTML)}


def test_one_listing_per_offering(listings):
    assert list(listings) == ["general-housing", "1080-washington-ave", "1017-home-street"]
    assert {lst.url for lst in listings.values()} == {bronx_pro.URL}
    assert {lst.borough for lst in listings.values()} == {"Bronx"}


def test_general_waitlist(listings):
    g = listings["general-housing"]
    assert (g.status, g.kind, g.address, g.senior_only) == ("waitlist", "waitlist", None, False)
    assert [(u.size, u.ami, u.rent, u.bands) for u in g.units] == [
        ("studio", 120, None, []), ("1br", 120, None, []), ("2br", 120, None, []), ("3br", 120, None, []),
    ]  # fmt: skip
    assert "AMI 50-120%" in g.notes and APP in g.notes and "BPGApartments@bronxprogroup.com" in g.notes


def test_senior_buildings(listings):
    w, h = listings["1080-washington-ave"], listings["1017-home-street"]
    assert (w.title, w.address) == ("1080 Washington Ave", "1080 Washington Ave, Bronx, NY")
    assert (h.title, h.address) == ("Crotona Park East – 1017 Home Street", "1017 Home Street, Bronx, NY")
    for lst in (w, h):
        assert (lst.status, lst.kind, lst.senior_only) == ("open", "waitlist", True)
        assert [u.size for u in lst.units] == ["studio", "1br"]
        assert all(u.rent is None and u.ami is None for u in lst.units)
        assert lst.units[0].bands == [Band(1, 0, None), Band(2, 0, None)]
        assert "HPD PBV S8 included" in lst.notes and "30% of household income" in lst.notes
        assert listing_verdict(lst).verdict == "excluded"  # conftest excludes the Bronx; senior only too


def test_layout_change_is_loud():
    with pytest.raises(RuntimeError, match="waitlist"):
        parse_page(HTML.replace("Interested in joining our waitlist?", "Hello"))
    with pytest.raises(RuntimeError, match="General Housing"):
        parse_page(HTML.replace(">General Housing<", ">Housing<"))
    with pytest.raises(RuntimeError, match="AMI range"):
        parse_page(HTML.replace("AMI 50-120%", "Income restricted"))
    with pytest.raises(RuntimeError, match="street address"):
        parse_page(HTML.replace(">1080 Washington Ave</h2>", ">Washington</h2>"))


def test_fetch_lists_and_snapshots(monkeypatch):
    monkeypatch.setattr(bronx_pro.fetch, "html", lambda url: HTML)
    res = BronxPro().fetch()
    assert len(res.listings) == 3 and len(res.listings) >= BronxPro.min_listings
    (snap,) = res.snapshots
    assert snap.text.startswith("Apartments") and APP in snap.links
