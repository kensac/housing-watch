from pathlib import Path

import pytest

from housing_watch import parse
from housing_watch.adapters import sanel, st_nicks
from housing_watch.adapters.st_nicks import (
    StNicks,
    mail_address,
    multifamily_apps,
    openings,
    parse_flyer,
    parse_waitlist,
    senior_apps,
)
from housing_watch.eligibility import listing_verdict

F = Path(__file__).parent / "fixtures" / "st_nicks"
FLYER = (
    "https://stnicksalliance.org/wp-content/uploads/2026/07/07.02.2026-2481-Crotona-Ave-Bx-Re-rental-Flyer-LD-Edit.pdf"
)
APP = "https://stnicksalliance.org/wp-content/uploads/2026/05/2481-Crotona-Ave-Application.pdf"
UP = "https://stnicksalliance.org/wp-content/uploads/2023/11/"
# application url -> its page-1 fixture
APPS = {
    UP + "St-Nicks-General-waiting-list-Application-3.pdf": "general-wait-list",
    UP + "270-Pulaski-Application-revision-2023-1.pdf": "270-pulaski",
    UP + "Jennings-Hall-Application-3.pdf": "jennings-hall",
    UP + "Msgr-Vetro-Application-2023-3.pdf": "msgr-vetro",
    UP + "Metro-Houses-Application-2023-3.pdf": "metro-houses",
    UP + "CABS-Senior-Housing-Application-Final-4.pdf": "cabs-senior",
    UP + "ELLERY-COURT-Application-3.pdf": "ellery-court",
}


def app_text(url: str) -> str:
    return (F / f"application-{APPS[url]}-p1.txt").read_text()


def run(monkeypatch, html: str):
    """StNicks().fetch() on a page, with every PDF answered from the fixtures."""
    monkeypatch.setattr(st_nicks.fetch, "html", lambda u: html)
    flyer = {"text": (F / "flyer-2481-crotona.txt").read_text(), "links": [APP]}
    crotona = {FLYER: flyer, APP: {"text": (F / "application-2481-crotona-p1.txt").read_text(), "links": []}}
    monkeypatch.setattr(st_nicks, "pdf_with_links", lambda c, u: crotona.get(u) or {"text": app_text(u), "links": []})
    return StNicks().fetch()


def test_waitlists_on_the_page():
    html = (F / "index.html").read_text()
    assert [(n, u) for n, u, _ in multifamily_apps(html)] == [
        ("General Wait List", UP + "St-Nicks-General-waiting-list-Application-3.pdf"),
        ("270 Pulaski Street Wait List", UP + "270-Pulaski-Application-revision-2023-1.pdf"),
    ]
    assert [n for n, _ in senior_apps(html)] == [
        "Jennings Hall",
        "Monsignor Vetro Apts.",
        "Metro Houses",
        "CABS Senior",
        "Ellery Court",
    ]


def test_every_listing(monkeypatch):
    res = run(monkeypatch, (F / "index.html").read_text())
    got = {x.ext_id: (x.address, x.borough, x.status, x.kind, x.senior_only, x.units) for x in res.listings}
    wl = ("Brooklyn", "waitlist", "waitlist")
    assert got.pop("07.02.2026-2481-Crotona-Ave-Bx-Re-rental-Flyer-LD-Edit")[:3] == (
        "2481 Crotona Avenue",
        "Bronx",
        "open",
    )
    assert got == {
        "general-wait-list": (None, *wl, False, []),
        "270-pulaski-street-wait-list": ("270 Pulaski Street, Brooklyn, NY 11206", *wl, False, []),
        "jennings-hall": ("260 Powers Street, Brooklyn, NY 11211", *wl, True, []),
        "monsignor-vetro-apts": ("320 Devoe Street, Brooklyn, NY 11211", *wl, True, []),
        "metro-houses": ("609 Metropolitan Avenue, Brooklyn, NY 11211", *wl, True, []),
        "cabs-senior": ("590 DeKalb Avenue, Brooklyn, NY 11206", *wl, True, []),
        "ellery-court": ("339 Ellery Street, Brooklyn, NY 11206", *wl, True, []),  # its mail-to block names it
    }
    by = {x.ext_id: x for x in res.listings}
    assert by["jennings-hall"].url == UP + "Jennings-Hall-Application-3.pdf"
    assert by["general-wait-list"].title == "St. Nicks Alliance General Wait List"
    assert "more than 35 multi-family properties" in (by["general-wait-list"].notes or "")
    assert "HUD Section 202 housing" in (by["cabs-senior"].notes or "")
    assert "offers sizes: STUDIO, 1." in (by["jennings-hall"].notes or "")
    pulaski = by["270-pulaski-street-wait-list"]
    assert "regular mail only to 270 Pulaski Street, LP, C/o St. Nicks Alliance, 260 Powers Street" in (
        pulaski.notes or ""
    )
    assert "publishes no unit list, rents or income limits" in (pulaski.notes or "")
    assert listing_verdict(pulaski).reason == "no unit details published"
    assert listing_verdict(by["ellery-court"]).reason == "senior only"


def test_sanel_copies_share_an_address(monkeypatch):
    """SANEL's copies link to these listings by site + street address (queries._judging._with_copies)."""
    res = run(monkeypatch, (F / "index.html").read_text())
    ours = {k for x in res.listings for k in parse.addr_keys(x.address)}
    S = Path(__file__).parent / "fixtures" / "sanel"
    for name in ("brooklyn__270-pulaski-street-u12rpt", "brooklyn__cabs-senior-45ors", "brooklyn__ellery-court-eosufd"):
        copy = sanel.parse_page(
            "https://sanel.nyc/affordable-housing/" + name.replace("__", "/"), (S / f"{name}.html").read_text()
        )
        assert parse.addr_keys(" ".join(filter(None, (copy.address, copy.title)))) & ours, name


def test_unaddressed_building_application_fails():
    with pytest.raises(RuntimeError, match="prints no building address"):
        parse_waitlist("Metro Houses", UP + "x.pdf", "MAIL TO: St Nicks Alliance\n 2 Kingsland Avenue\n", True)


def test_page_without_waitlists_fails(monkeypatch):
    html = (F / "index.html").read_text()
    with pytest.raises(RuntimeError, match="Option 3"):
        run(monkeypatch, html.replace("Option 3", "Option three"))
    no_seniors = html.replace("St. Nicks Alliance Senior Housing</h3>", "Elder Care</h3>")
    with pytest.raises(RuntimeError, match="Senior Housing"):
        run(monkeypatch, no_seniors)


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
    st_nicks.pdf_with_links(cache, FLYER)
    st_nicks.pdf_with_links(cache, FLYER)
    assert len(calls) == 1
    body["v"] = b"%PDF-2"
    st_nicks.pdf_with_links(cache, FLYER)
    assert len(calls) == 2
