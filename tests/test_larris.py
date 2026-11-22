from pathlib import Path

import pytest

from helpers import bands
from housing_watch.adapters.larris import URL, parse_page
from housing_watch.eligibility import listing_verdict

PAGE = (Path(__file__).parent / "fixtures" / "larris" / "re-rentals.html").read_text()


def test_balton_commons_embed_is_a_listing():
    """The posting is an <iframe srcdoc> embed; the inquiry-form and notice embeds are not postings."""
    [lst] = parse_page(URL, PAGE)
    assert (lst.ext_id, lst.title, lst.url) == ("balton-commons-llc-2c", "Balton Commons LLC #2C", URL)
    assert (lst.address, lst.borough, lst.status) == ("263–267 West 126th Street, New York, NY", "Manhattan", "open")
    [u] = lst.units
    assert (u.size, u.unit_no, u.rent, u.ami) == ("studio", "2C", 970.0, 60)
    assert bands(u) == [(1, None, 71280), (2, None, 81420)]
    assert listing_verdict(lst).verdict == "excluded"


def test_no_embeds_lists_nothing():
    assert parse_page(URL, "<html><body><p>Available Re-Rentals</p></body></html>") == []


def test_changed_embed_layout_raises():
    page = PAGE.replace("property-details", "unit-facts")
    with pytest.raises(RuntimeError, match="no property-details"):
        parse_page(URL, page)


def test_posting_without_rent_raises():
    page = PAGE.replace("MONTHLY RENT", "PRICE")
    with pytest.raises(RuntimeError, match="lacks"):
        parse_page(URL, page)
