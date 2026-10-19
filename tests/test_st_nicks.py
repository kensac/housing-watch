from pathlib import Path

from housing_watch.adapters.st_nicks import mail_address, openings, parse_flyer
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "st_nicks"
FLYER = (
    "https://stnicksalliance.org/wp-content/uploads/2026/07/07.02.2026-2481-Crotona-Ave-Bx-Re-rental-Flyer-LD-Edit.pdf"
)
APP = "https://stnicksalliance.org/wp-content/uploads/2026/05/2481-Crotona-Ave-Application.pdf"


def test_openings_section_only():
    assert openings((F / "index.html").read_text()) == [("2481 Crotona Avenue: Studio", FLYER)]


def test_mail_address():
    app = (F / "application-2481-crotona-p1.txt").read_text()
    assert mail_address(app) == "St Nicks Alliance, 260 Powers Street, Brooklyn NY 11211"


def test_crotona_flyer():
    app = (F / "application-2481-crotona-p1.txt").read_text()
    lst = parse_flyer("2481 Crotona Avenue: Studio", FLYER, (F / "flyer-2481-crotona.txt").read_text(), APP, app)
    assert (lst.address, lst.borough, lst.status) == ("2481 Crotona Avenue", "Bronx", "open")
    (u,) = lst.units
    assert (u.size, u.rent, u.ami, u.set_aside) == ("studio", 2325.0, 130, None)
    # "1-2 persons, Minimum $93,000-Maximum $154,444": the 1-person cap comes from 130% AMI, not the row max
    assert [(b.household, b.income_min, b.income_max) for b in u.bands] == [(1, 93000, 154440), (2, 93000, 154444)]
    assert APP in lst.notes and "regular mail only to St Nicks Alliance, 260 Powers Street" in lst.notes
    assert "housingcompliance@stnicksalliance.org" in lst.notes
    v = listing_verdict(lst)
    assert (v.verdict, v.reason) == ("excluded", "in Bronx")


def test_pdf_cache_skips_unchanged_bytes(monkeypatch):
    from types import SimpleNamespace

    from housing_watch.adapters import st_nicks

    calls = []
    body = {"v": b"%PDF-1"}
    monkeypatch.setattr(st_nicks.fetch, "get", lambda url: SimpleNamespace(content=body["v"]))
    monkeypatch.setattr(st_nicks, "_pdf_extract", lambda data: calls.append(data) or {"text": "t", "links": []})
    cache: dict = {}
    st_nicks.pdf_cached(cache, FLYER)
    st_nicks.pdf_cached(cache, FLYER)
    assert len(calls) == 1
    body["v"] = b"%PDF-2"
    st_nicks.pdf_cached(cache, FLYER)
    assert len(calls) == 2
