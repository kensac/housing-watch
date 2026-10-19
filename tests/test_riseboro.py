from pathlib import Path

from housing_watch.adapters.riseboro import build_listing, parse_building
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "riseboro"
WL = "https://riseboro.org/housing/woodlawn-senior-living/"


def test_open_senior_waitlist():
    b = parse_building(WL, "Woodlawn Senior Living", (F / "woodlawn-senior-living.html").read_text())
    assert (b["neighborhood"], b["status"]) == ("Woodlawn Heights, Bronx", "Operating")
    assert b["app"] == "https://riseboro.org/wp-content/uploads/Woodlawn-Application_4.15.26.pdf"
    lst = build_listing(b, (F / "woodlawn-app.txt").read_text())
    assert (lst.ext_id, lst.status, lst.kind, lst.borough, lst.senior_only) == (
        "woodlawn-senior-living",
        "waitlist",
        "waitlist",
        "Bronx",
        True,
    )
    assert lst.units == [] and lst.deadline is None
    assert "first come, first served" in lst.notes and "217 Wyckoff Avenue" in lst.notes
    assert listing_verdict(lst).verdict == "excluded"


def test_building_without_application_is_closed():
    url = "https://riseboro.org/housing/326-rockaway/"
    b = parse_building(url, "326 Rockaway", (F / "326-rockaway.html").read_text())
    assert (b["neighborhood"], b["app"]) == ("East New York", None)
    lst = build_listing(b, None)
    assert (lst.status, lst.borough, lst.senior_only) == ("closed", "Brooklyn", False)
