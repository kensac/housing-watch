from pathlib import Path

from housing_watch.adapters.reside import parse_page
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "reside"


def page(name):
    return parse_page(f"https://residenewyork.com/property/{name}/", (F / f"{name}.html").read_text())


def test_open_studio_unit():
    lst = page("115-stanwix-st-apartment-unit-218")
    assert lst.status == "open" and lst.borough == "Brooklyn"
    (u,) = lst.units
    assert (u.size, u.unit_no, u.rent, u.ami) == ("studio", "218", 2407.89, 130)
    assert (u.bands[0].household, u.bands[0].income_min, u.bands[0].income_max) == (1, 82557, 154440)
    assert listing_verdict(lst).verdict == "eligible"


def test_multi_unit_page():
    lst = page("25-01-queens-plaza-north-units-619-325-and-503")
    assert lst.borough == "Queens"
    assert [u.size for u in lst.units] == ["studio", "1br", "2br"]
    assert listing_verdict(lst).verdict == "eligible"


def test_closed_project():
    lst = page("closed")
    assert lst.status == "closed" and listing_verdict(lst).verdict == "excluded"


def test_upcoming_project():
    lst = page("1-9-putnam-avenue")
    assert lst.status == "upcoming" and lst.borough == "Brooklyn"


def test_lxml_namespace_crash_falls_back():
    # this page makes bs4's lxml builder raise ValueError in _getNsTag
    assert page("100-freedom-place-apartment-unit-4p").title.startswith("100 Freedom Place")
