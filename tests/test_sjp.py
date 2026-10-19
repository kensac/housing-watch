import json
from pathlib import Path

from housing_watch.adapters.sjp import parse_collection, parse_item
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "sjp"

# No live SJP listing exists (Oct 2026); these posts are synthetic, shaped like Squarespace collection items.
OPEN_TABLE = {
    "urlId": "31-10-whitestone-expy-unit-4b",
    "fullUrl": "/apartment-listings/31-10-whitestone-expy-unit-4b",
    "title": "31-10 Whitestone Expressway, Unit 4B, Flushing NY 11354",
    "tags": ["available"],
    "categories": ["available"],
    "publishOn": 1792000000000,
    "location": {"addressLine1": "", "addressLine2": ""},
    "body": (
        "<p>Affordable re-rental, 130% AMI. First come, first served.</p>"
        "<table><tr><th>Unit</th><th>AMI</th><th>Rent</th><th>Household Size</th><th>Income</th></tr>"
        "<tr><td>1 Bedroom</td><td>130%</td><td>$2,450</td><td>1 person</td><td>$84,000 - $154,440</td></tr>"
        "<tr><td></td><td></td><td></td><td>2 people</td><td>$84,000 - $176,410</td></tr></table>"
        '<a class="sqs-block-button-element" href="https://forms.example.com/apply-4b">Apply Now</a>'
    ),
}
RENTED_TEXT = {
    "urlId": "150-20-14th-ave-apt-2a",
    "fullUrl": "/apartment-listings/150-20-14th-ave-apt-2a",
    "title": "150-20 14th Avenue Apt 2A",
    "tags": ["rented"],
    "categories": [],
    "location": {"addressLine1": "150-20 14th Avenue", "addressLine2": "Whitestone, NY 11357"},
    "body": (
        "<p>RENTED</p><p>Studio. Rent: $1,980 per month. 100% AMI.</p>"
        "<p>1 person: $68,000 - $118,800</p>"
        '<a class="sqs-block-button-element sqs-button-element--disabled">Apply Now</a>'
    ),
}


def test_live_placeholder_is_not_a_listing():
    data = json.loads((F / "apartment-listings-empty.json").read_text())
    assert len(data["items"]) == 1
    assert parse_collection(data) == []


def test_open_post_with_table():
    lst = parse_item(OPEN_TABLE)
    assert (lst.status, lst.borough, lst.ext_id) == ("open", "Queens", "31-10-whitestone-expy-unit-4b")
    (u,) = lst.units
    assert (u.size, u.rent, u.ami, u.unit_no, u.available) == ("1br", 2450.0, 130, "4B", None)
    assert [(b.household, b.income_min, b.income_max) for b in u.bands] == [(1, 84000, 154440), (2, 84000, 176410)]
    assert "https://forms.example.com/apply-4b" in lst.notes
    assert listing_verdict(lst).verdict == "eligible"


def test_rented_post_is_closed():
    lst = parse_item(RENTED_TEXT)
    assert (
        lst.status == "closed" and lst.borough == "Queens" and lst.address == "150-20 14th Avenue, Whitestone, NY 11357"
    )
    (u,) = lst.units
    assert (u.size, u.rent, u.ami, u.unit_no, u.available) == ("studio", 1980.0, 100, "2A", False)
    assert [(b.household, b.income_min, b.income_max) for b in u.bands] == [(1, 68000, 118800)]
    assert listing_verdict(lst).verdict == "excluded"
