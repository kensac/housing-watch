from pathlib import Path

from housing_watch.adapters.mhany import building_urls, closed_section, parse_building
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "mhany"
CLOSED = closed_section((F / "av.html").read_text())


def building(slug):
    return parse_building(f"https://www.mutualhousingny.org/{slug}", (F / f"{slug}.html").read_text(), CLOSED)


def bands(u):
    return [(b.household, b.income_min, b.income_max) for b in u.bands]


def test_building_urls():
    assert building_urls((F / "rr.html").read_text()) == [
        "https://www.mutualhousingny.org/stella-heights-hdfc",
        "https://www.mutualhousingny.org/pe2--700-manida-street",
        "https://www.mutualhousingny.org/1921-cortelyou-road",
    ]


def test_stella_heights_sros_open():
    lst = building("stella-heights-hdfc")
    assert (lst.status, lst.borough) == ("open", "Manhattan")
    assert lst.address == "575 West 155th Street / 530 West 178th Street"
    assert [(u.size, u.rent, bands(u)) for u in lst.units] == [
        ("sro", None, [(1, 0, 59400)]),
        ("sro", None, [(1, 0, 35640)]),
        ("sro", 1114.0, [(1, 38195, 59400)]),
    ]
    assert "stellaheights@mutualhousingny.org" in lst.notes and "470 Vanderbilt Avenue" in lst.notes
    assert listing_verdict(lst).verdict == "excluded"


def test_manida_closed_bronx_senior_tables():
    lst = building("pe2--700-manida-street")
    assert (lst.status, lst.borough, lst.senior_only) == ("closed", "Bronx", False)
    studio, br1, br1_40, br2 = lst.units
    assert (studio.size, studio.rent, studio.ami, studio.set_aside) == ("studio", 625.0, 30, "senior")
    assert bands(studio) == [(1, 24720, 35640), (2, 24720, 40710)]
    assert (br1_40.rent, br1_40.ami, bands(br1_40)[0]) == (965.0, 40, (1, 36789, 47530))
    assert (br2.size, br2.set_aside, bands(br2)[0]) == ("2br", None, (2, 55612, 67850))
    assert "700Manida@mutualhousingny.org" in lst.notes


def test_cortelyou_closed_brooklyn():
    lst = building("1921-cortelyou-road")
    assert (lst.status, lst.borough) == ("closed", "Brooklyn")
    assert [(u.size, u.rent, u.ami) for u in lst.units] == [("1br", 680.0, 30), ("2br", 1740.0, 60)]
    assert listing_verdict(lst).verdict == "excluded"
