from pathlib import Path

from housing_watch.adapters.reclaim_hdfc import parse_page
from housing_watch.criteria import listing_verdict

HTML = (Path(__file__).parent / "fixtures" / "reclaim_hdfc" / "page.html").read_text()


def test_open_application_period():
    lst = parse_page(HTML)
    assert lst.status == "open" and lst.borough == "Bronx"
    assert lst.address.count("Bronx NY") == 10 and "2317-21 Tiebout Avenue" in lst.address
    assert "Applications Accepted" in lst.notes and "Not Started" in lst.notes
    assert listing_verdict(lst).verdict == "excluded"  # Bronx


def test_closed_application_period():
    lst = parse_page(HTML.replace("Applications Accepted", "Closed"))
    assert lst.status == "closed"
