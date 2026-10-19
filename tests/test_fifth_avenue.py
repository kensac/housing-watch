from pathlib import Path

from housing_watch.adapters import fifth_avenue
from housing_watch.adapters.fifth_avenue import FifthAvenue, parse_page
from housing_watch.criteria import listing_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "fifth_avenue"


def listings():
    return {x.ext_id: x for x in parse_page((F / "page.html").read_text())}


def test_all_cards_captured():
    by = listings()
    assert list(by) == [
        "the-axel",
        "sunset-ridge-apartments",
        "sunset-park-apartments-and-library",
        "3-eleven",
        "551-warren-street-brooklyn-ny-upcoming",
    ]
    assert sum(len(x.units) for x in by.values()) == 16


def test_axel_6f_eligible():
    lst = listings()["the-axel"]
    assert (lst.title, lst.address, lst.borough) == ("The Axel", "539 Vanderbilt Avenue, Brooklyn NY", "Brooklyn")
    (u,) = lst.units
    assert (u.unit_no, u.size, u.rent, u.ami, u.set_aside) == ("6F", "studio", 2732.32, 130, None)
    # "1-2 people ... Max $176,410" is the 2-person cap; 1 person gets 130% of 1-person AMI
    assert u.bands == [Band(1, 97485, 154440), Band(2, 97485, 176410)]
    assert listing_verdict(lst).verdict == "eligible"
    assert "fifthave.my.site.com" in lst.notes


def test_sunset_ridge_senior_section8_excluded():
    lst = listings()["sunset-ridge-apartments"]
    assert lst.senior_only and lst.borough == "Brooklyn"
    assert [(u.unit_no, u.size, u.ami, u.set_aside) for u in lst.units] == [
        ("3J", "studio", 50, "senior"),
        ("8C", "studio", 50, "senior"),
        ("3C", "studio", 50, "senior"),
        ("1B", "1br", 50, "senior"),
    ]
    assert lst.units[0].bands == [Band(1, None, 59400), Band(2, None, 67850)]
    assert listing_verdict(lst).verdict == "excluded"


def test_section8_and_upcoming():
    by = listings()
    park = by["sunset-park-apartments-and-library"]
    assert {u.set_aside for u in park.units} == {"section 8"} and not park.senior_only
    warren = by["551-warren-street-brooklyn-ny-upcoming"]
    assert warren.notes.startswith("Upcoming") and warren.borough == "Brooklyn"
    assert warren.units[0].bands == [Band(1, 54960, 68040)] and warren.units[0].rent == 1374.0
    assert all(listing_verdict(x).verdict == "excluded" for k, x in by.items() if k != "the-axel")


def test_empty_page_snapshot(monkeypatch):
    monkeypatch.setattr(fifth_avenue.fetch, "html", lambda url: "<h1>Re-Rental Availabilities</h1><p>None now.</p>")
    res = FifthAvenue().fetch()
    assert not res.listings and res.snapshots[0].url == fifth_avenue.URL
