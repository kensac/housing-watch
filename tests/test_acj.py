from pathlib import Path

import pytest

from housing_watch.adapters.acj import parse_detail, parse_index
from housing_watch.criteria import listing_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "acj"
URL = "https://www.acjinternationalrealty.com/affordable-re-rentals/36-23-172nd-street"


def test_index_links_match_header():
    assert parse_index((F / "index.html").read_text()) == [URL]


def test_index_count_mismatch_is_loud():
    html = (F / "index.html").read_text().replace("1 Listing available", "2 Listings available")
    with pytest.raises(RuntimeError, match="says 2"):
        parse_index(html)


def test_empty_index():
    assert parse_index('<h2 class="mb-6">0 Listings available</h2>') == []
    with pytest.raises(RuntimeError, match="header"):
        parse_index("<h2>Coming soon</h2>")


def test_172nd_street_one_bedroom_is_eligible():
    lst = parse_detail(URL, (F / "36-23-172nd-street.html").read_text())
    assert (lst.title, lst.address, lst.borough, lst.status) == (
        "36-23 172nd Street, Unit 3B",
        "36-23 172nd Street, Queens, NY 11358",
        "Queens",
        "open",
    )
    (u,) = lst.units
    assert (u.size, u.rent, u.ami, u.unit_no) == ("1br", 1670, 130, "3B")
    assert u.bands == [Band(1, 66800, 154440), Band(2, 66800, 176410), Band(3, 66800, 198510)]
    v = listing_verdict(lst)
    assert v.verdict == "eligible" and v.units[0][2] == "$66,800-$154,440 for 1"
    assert "/files/listings/36-23-172nd-street/applicationForm.pdf" in lst.notes
    assert 'affordablehousing@acjinternationalrealty.com with subject "Application: 36-23 172nd Street"' in lst.notes
    assert (
        "36-23 172ND STREET UNIT 3B C/O ACJ INTERNATIONAL REALTY 154-17A RIVERSIDE DR, WHITESTONE, NY 11357"
        in lst.notes
    )
