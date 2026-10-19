from pathlib import Path

from housing_watch.adapters.avalonbay import parse_list, parse_page, tier_status
from housing_watch.criteria import listing_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "avalonbay"
NYC = "https://www.avaloncommunities.com/new-york/new-york-city-apartments/"


def page(slug, html=None):
    return parse_page(f"{NYC}{slug}-affordable-program/", html or (F / f"{slug}.html").read_text())


def test_list_includes_commented_out_morningside():
    urls = parse_list((F / "community-list.html").read_text())
    assert [u.removeprefix(NYC) for u in urls] == [
        "ava-high-line-affordable-program/",
        "avalon-bowery-place-affordable-program/",
        "avalon-clinton-affordable-program/",
        "avalon-midtown-west-affordable-program/",
        "avalon-morningside-park-affordable-program/",
        "avalon-west-chelsea-affordable-program/",
    ]  # Harrison, Somers, Westbury etc. are outside NYC


def test_high_line_three_closed_tiers():
    lst = page("ava-high-line")
    assert (lst.title, lst.address, lst.borough, lst.status, lst.kind) == (
        "AVA High Line",
        "525 West 28th Street, New York, NY 10001",
        "Manhattan",
        "closed",
        "waitlist",
    )
    assert [(u.size, u.rent, u.ami) for u in lst.units] == [
        ("studio", 1038, 40),
        ("studio", 1321, 50),
        ("1br", 1409, 50),
        ("studio", 1605, 60),
        ("1br", 1713, 60),
        ("2br", 2043, 60),
    ]
    assert lst.units[5].bands == [Band(2, None, 77760), Band(3, None, 87480), Band(4, None, 97200)]
    assert all(u.available is False for u in lst.units)
    assert listing_verdict(lst).verdict == "excluded"


def test_midtown_west_130_tier_has_ranges_and_old_limits():
    lst = page("avalon-midtown-west")
    tier = [u for u in lst.units if u.ami == 130]
    assert [(u.size, u.rent) for u in tier] == [("studio", 3306), ("1br", 3840), ("2br", 4596)]
    assert tier[1].bands == [Band(1, 126926, 147420), Band(2, 126926, 168480)]
    # if the waitlist opened, the 2025 cap ($147,420) would still exclude a $150k single
    opened = page("avalon-midtown-west", (F / "avalon-midtown-west.html").read_text().replace("Closed", "Open"))
    assert opened.status == "waitlist" and all(u.available is None for u in opened.units)
    v = listing_verdict(opened)
    assert v.verdict == "excluded" and v.units[4][2] == "over income (max $147,420)"


def test_morningside_three_bedroom_and_chelsea_typo():
    ms = page("avalon-morningside-park")
    assert ms.address == "1 Morningside Drive, New York, NY 10025"
    assert [u.size for u in ms.units] == ["studio", "1br", "2br", "3br"]
    assert ms.units[3].bands[-1] == Band(6, None, 75200)
    wc = page("avalon-west-chelsea")
    assert wc.units[0].bands == [Band(1, None, 56700), Band(2, None, 64800)]  # "2 personal household"


def test_tier_status_labels():
    assert tier_status("Waitlist Closed") == "closed"
    assert tier_status("Join Waitlist") == "waitlist"
    assert tier_status("Apply Now") == "open"
