from pathlib import Path

import pytest

from housing_watch.adapters import elh
from housing_watch.adapters.elh import ELH, parse_page, parse_sitemap, status_of
from housing_watch.eligibility import listing_verdict

F = Path(__file__).parent / "fixtures" / "elh"
SITE = "https://www.elhrerentals.com"
HALSEY = f"{SITE}/brooklyn-properties/halsey"
HARLEM = f"{SITE}/manhattan-properties/greater-harlem"
RISE = f"{SITE}/brooklyn-properties/stanley-commons-bbc24"
LA_CENTRAL = f"{SITE}/the-bronx-properties/la-central-a-b"

# the 16 ELH buildings SANEL republishes (official_url on its copies, 2026-11-07)
SANEL_COPIES = [
    "the-bronx-properties/1414-walton", "brooklyn-properties/27-bedstuy", "brooklyn-properties/517-st-marks",
    "manhattan-properties/amsterdam-135", "manhattan-properties/broadway-151", "brooklyn-properties/cg-trio",
    "the-bronx-properties/evergreen-tiebout", "brooklyn-properties/flatbush-and-rogers", "brooklyn-properties/halsey",
    "manhattan-properties/mount-morris", "brooklyn-properties/nia-jv-llc", "brooklyn-properties/nostrand-2-monroe",
    "brooklyn-properties/pacific-village", "brooklyn-properties/prospect-equities-operating",
    "brooklyn-properties/rutland-corners", "brooklyn-properties/sterling-washington",
]  # fmt: skip


def test_sitemap_lists_every_building_page_only():
    urls = parse_sitemap((F / "sitemap.xml").read_text())
    assert len(urls) == 21
    assert {f"{SITE}/{p}" for p in SANEL_COPIES} <= set(urls)
    assert {RISE, HARLEM, LA_CENTRAL, f"{SITE}/brooklyn-properties/coney-island-commons"} <= set(urls)
    assert not any("/category/" in u or "/tag/" in u or u.endswith("-properties") for u in urls)


def test_sitemap_without_properties_or_with_a_new_collection_is_loud():
    with pytest.raises(RuntimeError, match="no property pages"):
        parse_sitemap(f"<urlset><url><loc>{SITE}/faq</loc></url></urlset>")
    with pytest.raises(RuntimeError, match="unknown property collection"):
        parse_sitemap(f"<urlset><url><loc>{SITE}/westchester-properties/x</loc></url></urlset>")


def test_open_waitlist_without_unit_details():
    lst = parse_page(HALSEY, (F / "halsey.html").read_text())
    assert (lst.ext_id, lst.title, lst.address, lst.borough, lst.status, lst.kind) == (
        "halsey",
        "Halsey",
        "1311 Halsey Street",
        "Brooklyn",
        "waitlist",
        "waitlist",
    )
    assert lst.url == HALSEY and lst.units == [] and lst.deadline is None and not lst.senior_only
    assert lst.notes.startswith("Status: Waitlist Open; availability: Replenishing Waitlist")
    assert f"{SITE}/online-application" in lst.notes and "applications@elhmgmt.com" in lst.notes
    v = listing_verdict(lst)
    assert (v.verdict, v.reason) == ("possible", "no unit details published")


def test_closed_waitlist_is_kept_as_closed():
    lst = parse_page(HARLEM, (F / "greater-harlem.html").read_text())
    assert (lst.title, lst.address, lst.borough, lst.status, lst.kind) == (
        "Greater Harlem",
        "233 West 135 Street",
        "Manhattan",
        "closed",
        "waitlist",
    )
    assert "No Units Available" in lst.notes and "online-application" not in lst.notes
    assert listing_verdict(lst).verdict == "excluded"

    lst = parse_page(LA_CENTRAL, (F / "la-central-a-b.html").read_text())
    assert (lst.title, lst.address, lst.borough, lst.status) == (
        "La Central A & B",
        "556 Bergen Avenue",
        "Bronx",
        "closed",
    )


def test_development_with_its_own_application_site():
    lst = parse_page(RISE, (F / "stanley-commons-bbc24.html").read_text())
    assert (lst.ext_id, lst.title, lst.address, lst.status) == (
        "stanley-commons-bbc24",
        "The Rise",
        "1830 Strauss Street",
        "waitlist",
    )
    assert "Apply via https://www.theriseapartment.com/" in lst.notes and "online-application" not in lst.notes


def test_status_wording():
    assert status_of("Waitlist Open", "Replenishing Waitlist") == "waitlist"
    assert status_of("Waitlist Closed", "No Available Units") == "closed"
    assert status_of("Waitlist Open", "2 Units Available") == "open"
    assert status_of("Waitlist Closed", "No Units Available") == "closed"
    with pytest.raises(RuntimeError, match="unrecognised status"):
        status_of("Coming Soon", None)


def test_malformed_page_is_loud():
    html = (F / "halsey.html").read_text()
    with pytest.raises(RuntimeError, match="Status"):
        parse_page(HALSEY, html.replace("<strong>Status:</strong>", "<strong>State</strong>"))
    with pytest.raises(RuntimeError, match="unrecognised status"):
        parse_page(HALSEY, html.replace("</strong> Waitlist Open</p>", "</strong> Lottery</p>"))
    with pytest.raises(RuntimeError, match="title or body"):
        parse_page(HALSEY, "<html><body><h1>Halsey</h1></body></html>")
    with pytest.raises(RuntimeError, match="not a property page"):
        parse_page(f"{SITE}/faq", html)


def test_fetch_reuses_cached_parse_on_304(monkeypatch):
    sitemap = (
        f"<urlset><url><loc>{SITE}/brooklyn-properties</loc></url><url><loc>{HALSEY}</loc></url>"
        f"<url><loc>{HARLEM}</loc></url></urlset>"
    )
    pages = {HALSEY: (F / "halsey.html").read_bytes(), HARLEM: (F / "greater-harlem.html").read_bytes()}
    calls = []

    def conditional(url, ent):
        calls.append((url, ent.get("etag")))
        if ent.get("etag") == "e1":
            return None, False
        ent["etag"] = "e1"
        return pages[url], True

    monkeypatch.setattr(elh.fetch, "html", lambda url: sitemap)
    monkeypatch.setattr(elh.fetch, "conditional", conditional)
    a = ELH()
    a.cache["pages"] = {f"{SITE}/brooklyn-properties/gone": {"etag": "x", "listing": {}}}
    first = a.fetch().listings
    assert [x.ext_id for x in first] == ["halsey", "greater-harlem"]
    assert set(a.cache["pages"]) == {HALSEY, HARLEM}  # the page the sitemap dropped is pruned
    second = ELH(a.cache).fetch().listings
    assert calls[2:] == [(HALSEY, "e1"), (HARLEM, "e1")]
    assert [(x.ext_id, x.status, x.address) for x in second] == [(x.ext_id, x.status, x.address) for x in first]
