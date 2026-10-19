from pathlib import Path

from housing_watch.adapters.sterling23 import parse_page
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "sterling23"
HTML = (F / "affordable.html").read_text()


def test_ogden_6d():
    (lst,) = parse_page(HTML)
    assert lst.address == "1182 Ogden Ave, Bronx, NY 10452" and lst.borough == "Bronx" and lst.status == "open"
    (u,) = lst.units
    assert (u.size, u.unit_no, u.rent, u.ami, u.set_aside) == ("1br", "6D", 2308.54, 130, None)
    assert [(b.household, b.income_min, b.income_max) for b in u.bands] == [
        (1, 79150, 154440),
        (2, 79150, 176410),
        (3, 79150, 198510),
    ]
    assert "docs.realty.mx" in lst.notes and "info@sterling23.com" in lst.notes
    assert listing_verdict(lst).reason == "in Bronx"


def test_rented_badge_closes():
    html = HTML.replace(">Available</div>", ">Rented</div>", 1)
    (lst,) = parse_page(html)
    assert lst.status == "closed" and lst.units[0].available is False
