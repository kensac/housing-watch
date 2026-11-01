from pathlib import Path
from urllib.parse import urlparse

import httpx

from housing_watch import fetch
from housing_watch.adapters import related_ahg
from housing_watch.adapters.related_ahg import RelatedAHG, home_entries, parse_listing
from housing_watch.eligibility import listing_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "related_ahg"
WESTPORT_APP = "http://www.ahgleasing.com/westportapp607.pdf"


def westport():
    title, href, section = home_entries((F / "home.html").read_text())[0]
    return parse_listing(title, href, href, (F / "westportad.txt").read_text(), [WESTPORT_APP], section)


def test_home_entries():
    e = home_entries((F / "home.html").read_text())
    assert [x[1] for x in e] == [
        "https://www.ahgleasing.com/westportad.pdf",
        "https://ahgleasing.com/accessibility-units/",
    ]
    assert "WAITING LIST" in e[1][2]


def test_westport_607():
    lst = westport()
    assert (lst.title, lst.borough, lst.status, lst.kind) == ("The Westport", "Manhattan", "open", "re-rental")
    assert lst.address == "500 W. 56th Street, New York, NY 10019"
    (u,) = lst.units
    assert (u.size, u.unit_no, u.rent, u.ami, u.set_aside) == ("1br", "607", 3702.0, 130, None)
    assert u.bands == [Band(1, 130629, 154440), Band(2, 130629, 176410), Band(3, 130629, 198510)]
    assert WESTPORT_APP in lst.notes and "AHGApplications@related.com" in lst.notes
    assert listing_verdict(lst).verdict == "eligible"


def test_accessibility_waitlist_is_set_aside():
    _, href, section = home_entries((F / "home.html").read_text())[1]
    lst = parse_listing(
        "553W30 at 553 W. 30th Street, New York, NY 10001",
        href,
        "https://www.ahgleasing.com/553accessibilityunitsadweb.pdf",
        (F / "553accessibilityunitsadweb.txt").read_text(),
        [],
        section,
    )
    assert (lst.borough, lst.status, lst.kind) == ("Manhattan", "waitlist", "waitlist")
    assert [(u.size, u.ami, u.rent) for u in lst.units] == [
        ("2br", 50, 1766.0),
        ("studio", 60, 1686.0),
        ("1br", 60, 1800.0),
        ("2br", 60, 2148.0),
    ]
    studio = lst.units[1]
    assert studio.bands == [Band(1, 57794, 71280), Band(2, 57794, 81420)]
    assert studio.set_aside == "mobility/visual/hearing" and lst.units[0].set_aside == "mobility"
    assert listing_verdict(lst).verdict == "excluded"


def test_fetch_caches_pdfs_and_snapshots_empty_home(monkeypatch):
    """PDFs go through fetch.get: one request each time, under the host lock, 304 reuses the parse."""
    home = (F / "home.html").read_text()
    pdf = {"etag": '"v1"', "body": b"%PDF v1"}
    calls, reads = [], []

    def server(url, headers=None):  # stands in for the network under fetch.get
        calls.append(fetch._host_locks[urlparse(url).netloc].locked())
        req = httpx.Request("GET", url)
        if (headers or {}).get("If-None-Match") == pdf["etag"]:
            return httpx.Response(304, request=req)
        return httpx.Response(200, content=pdf["body"], headers={"etag": pdf["etag"]}, request=req)

    monkeypatch.setattr(fetch, "_MIN_GAP", 0)
    monkeypatch.setattr(fetch._client, "get", server)
    monkeypatch.setattr(related_ahg.fetch, "html", lambda url: home if url == related_ahg.HOME else "")
    monkeypatch.setattr(
        related_ahg, "_pdftotext", lambda data: reads.append(data) or (F / "westportad.txt").read_text()
    )
    a = RelatedAHG()
    assert [x.ext_id for x in a.fetch().listings] == ["westportad"]
    assert len(calls) == 1 and len(reads) == 1
    assert RelatedAHG(a.cache).fetch().listings[0].units[0].rent == 3702.0
    assert len(calls) == 2 and len(reads) == 1  # 304: cached parse reused
    pdf.update(etag='"v2"', body=b"%PDF v2")
    RelatedAHG(a.cache).fetch()
    assert len(calls) == 3 and reads[-1] == b"%PDF v2"  # changed: one download, re-read
    assert all(calls)  # every request held the host's slot

    monkeypatch.setattr(related_ahg.fetch, "html", lambda url: "<p>Affordable Housing Group</p><p>No vacancies.</p>")
    res = RelatedAHG().fetch()
    assert not res.listings and res.snapshots[0].url == related_ahg.HOME
