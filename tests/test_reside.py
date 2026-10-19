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


def bands(u):
    return [(b.household, b.income_min, b.income_max) for b in u.bands]


def test_word_pasted_table_and_continuation_rows():
    # curly quotes close the cells' attributes; the rows under the unit carry its 2- and 3-person caps
    lst = page("210-w-150-street-apartment-unit-1e")
    (u,) = lst.units
    assert (u.size, u.unit_no, u.rent, u.ami) == ("1br", "1E", 2370.74, 150)
    assert bands(u) == [(1, 86152, 170100), (2, 86152, 194400), (3, 86152, 218700)]
    assert lst.borough == "Manhattan" and listing_verdict(lst).verdict == "eligible"


def test_multi_value_cells():
    (u,) = page("2-blue-slip-apartment-unit-7n").units
    assert u.size == "studio" and bands(u) == [(1, 83492, 147420), (2, 83492, 168480)]


def test_min_and_labeled_max_columns():
    (u,) = page("dutch-house-37-05-30th-street-apartment-unit-205").units
    assert bands(u) == [(1, 104442, 147420), (2, 104442, 168480), (3, 104442, 189540)]


def test_unlabeled_max_column_starts_at_the_matching_household():
    # $136,080 is 105% of the 2-person limit; the address zip beats the page's wrong "bronx" city term
    lst = page("dunbar-apartments-246-west-150th-street-apartment-unit-1e")
    assert lst.borough == "Manhattan"
    assert [b.household for b in lst.units[0].bands] == [2, 3, 4, 5]
    assert listing_verdict(lst).verdict == "excluded"


def test_header_range_without_table():
    # $189,540 is 130% of the 2025 3-person limit, so 1 person caps at 130% of the 2025 1-person limit
    lst = page("183-03-hillside-avenue-apartments")
    (u,) = lst.units
    assert lst.status == "upcoming" and lst.borough == "Queens"
    assert (u.size, u.rent) == ("unknown", 2722) and bands(u)[0] == (1, 99738, 147420)
    assert "rent $2,722-$2,823" in lst.notes
    assert listing_verdict(lst).reason.startswith("over income")


def test_header_range_with_floor_plan_sizes():
    lst = page("101-macombs-place-apartments")
    assert [u.size for u in lst.units] == ["1br", "2br"] and bands(lst.units[0])[0] == (1, 32868, 119030)


def test_rent_only_page():
    lst = page("1692-pacific-street-apartment")
    (u,) = lst.units
    assert (u.size, u.rent, u.bands) == ("unknown", 2406, []) and "only rent" in lst.notes


def test_placeholder_page_says_so():
    lst = page("2512-newkirk-ave-unit-4b")
    assert lst.units == [] and "publishes no unit sizes, rent or income" in lst.notes
    assert listing_verdict(lst).reason == "no unit details published"


def test_bronx_by_address_zip_over_wrong_city_term():
    lst = page("781-elsemere-place-ml-6a")
    assert lst.borough == "Bronx" and listing_verdict(lst).verdict == "excluded"


def test_status_from_article_terms():
    # no visible label; the property-status-closed-project term on the <article> decides
    assert page("379-harman-street-apartment-unit-4h").status == "closed"
