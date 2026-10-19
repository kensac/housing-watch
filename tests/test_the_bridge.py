from pathlib import Path

from housing_watch.adapters.the_bridge import build_listing, despace, parse_cards, parse_flyer
from housing_watch.criteria import listing_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "the_bridge"


def cards():
    return parse_cards((F / "news-and-media.html").read_text())


def test_cards_link_flyer_and_application():
    c = cards()
    assert len(c) == 2
    assert c[0]["flyer"].endswith("0bbb42_d4418ed5b7674bd6a586d09a592d31a7.pdf")
    assert c[0]["app"].endswith("0bbb42_0e559c29858c48e2b5a6520d73c4ce48.pdf")
    assert c[0]["emails"] == ["housingapplication@thebridgeny.org"]
    assert c[1]["flyer"].endswith("0bbb42_1ac65ab5a496484c95328bf33ac5b887.pdf") and c[1]["emails"] == []


def test_despace_letter_spaced_address():
    assert despace("3 5 0 0      P A R K         A V E .   B R O N X ,") == "3500 PARK AVE. BRONX,"
    assert despace("STUDIO APARTMENT") == "STUDIO APARTMENT"


def test_senior_studio_free_text_flyer():
    lst = build_listing(cards()[0], (F / "flyer-3500-park.txt").read_text(), (F / "app-3500-park.txt").read_text())
    assert (lst.address, lst.borough, lst.senior_only) == ("3500 PARK AVE. BRONX, NY 10456", "Bronx", True)
    (u,) = lst.units
    assert (u.size, u.rent, u.ami, u.set_aside) == ("studio", 1687.0, 50, "senior")
    assert u.bands == [Band(1, 0, 56700), Band(2, 0, 64800)]
    assert "290 Lenox Avenue, 3rd Floor, New York, NY 10027" in lst.notes
    assert "housingapplication@thebridgeny.org" in lst.notes
    assert listing_verdict(lst).verdict == "excluded"


def test_table_flyer_reversed_ami_and_multi_household_row():
    lst = build_listing(cards()[1], (F / "flyer-203-newport.txt").read_text(), (F / "app-203-newport.txt").read_text())
    assert (lst.ext_id, lst.borough, lst.senior_only) == ("203-newport-st-brooklyn-ny-11212", "Brooklyn", False)
    one, three = lst.units
    assert (one.size, one.rent, one.ami) == ("1br", 980.0, 40)
    # "1 - 3 PERSONS $45,360 - $58,320": the 1-person cap is 40% AMI ($47,520), not the 3-person $58,320
    assert one.bands[0] == Band(1, 45360, 47520) and one.bands[-1] == Band(3, 45360, 58320)
    assert (three.size, three.rent) == ("3br", 1334.0)
    assert "standard mail only" in lst.notes and "Bridge Rockaway LP Waiting List" in lst.notes
    assert listing_verdict(lst).verdict == "excluded"


def test_empty_flyer_has_no_units():
    assert parse_flyer("")["units"] == []
