from datetime import date
from pathlib import Path

from housing_watch.adapters.langsam import build_listings, parse_page
from housing_watch.criteria import listing_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "langsam"
DOC = "http://admin.clientsolution.com/docs/LangsamPropertyServices/doc/"


def run(today):
    page = parse_page((F / "affordable-rental-opportunities.html").read_text())
    pdfs = {
        DOC + "603%20Pontiac%20Place%20Application.pdf": (F / "pontiac-app.txt").read_text(),
        DOC + "Steinway%20Estates%20Applicaion%201.20.26.pdf": (F / "steinway-app.txt").read_text(),
        DOC + "Steinway%20Estates%20ATTACHMENT%20F-2%20%201.14.26.pdf": (F / "steinway-ad.txt").read_text(),
    }
    return page, build_listings(page, pdfs, today)


def test_inline_listing_and_docs():
    page, (pontiac, steinway) = run(date(2026, 10, 18))
    assert set(page["docs"]) == {"603 pontiac place", "steinway estates"}
    assert (pontiac.address, pontiac.borough, pontiac.deadline, pontiac.status) == (
        "603 Pontiac Place, Bronx NY 10455",
        "Bronx",
        "2026-03-20",
        "closed",
    )
    (u,) = pontiac.units
    assert (u.size, u.rent, u.ami) == ("1br", 1362.0, 80)
    assert u.bands == [Band(1, 50434, 90720), Band(2, 50434, 103680), Band(3, 50434, 116640)]
    assert "1601 Bronxdale Ave, Suite 201, Bronx, NY 10462" in pontiac.notes and "standard mail only" in pontiac.notes
    assert listing_verdict(pontiac).verdict == "excluded"


def test_pdf_listing_two_ami_tiers():
    _, (_, steinway) = run(date(2026, 10, 18))
    assert (steinway.borough, steinway.address) == ("Queens", "19-80 Steinway Street, Astoria New York 11105")
    a, b = steinway.units
    assert (a.size, a.rent, a.ami, a.bands[0]) == ("1br", 1521.0, 80, Band(1, 52148, 90720))
    assert (b.size, b.rent, b.ami, b.bands[0]) == ("1br", 2135.0, 120, Band(1, 73200, 136080))


def test_open_before_deadline_but_over_income():
    _, (pontiac, steinway) = run(date(2026, 3, 1))
    assert pontiac.status == steinway.status == "open"
    assert listing_verdict(steinway).verdict == "excluded"  # 120% caps 1 person at $136,080
