"""ELH Management: a Squarespace site with one blog post per building, in one collection per borough.

The sitemap lists every building page (`/<borough>-properties/<slug>`), so it is the index: `/available-units`
leaves some out (27 Bedstuy on 2026-11-07). A page carries the address, "Status:" (Waitlist Open / Waitlist
Closed) and "Availability:" (Replenishing Waitlist / No Units Available). No page publishes unit sizes, rents,
AMI levels or income limits: the FAQ's income and rent charts are HUD's per-AMI caps for every building,
not a building's own. So every listing has units=[], an open waitlist is status "waitlist", a closed one
"closed", and an availability that announces units makes it "open". One online application covers every ELH
development that is accepting applications. ?format=json and /api/ are disallowed by robots.txt, so the HTML is
parsed; pages are re-read with If-None-Match (Squarespace answers 304) and their parse is cached.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Borough, Listing, PublishedStatus, Result
from . import Adapter, cached_listing, prune

SITE = "https://www.elhrerentals.com"
SITEMAP = f"{SITE}/sitemap.xml"
APPLY = f"{SITE}/online-application"
CONTACT = "applications@elhmgmt.com, 718-855-5620"
BOROUGHS: dict[str, Borough] = {
    "brooklyn-properties": "Brooklyn",
    "the-bronx-properties": "Bronx",
    "manhattan-properties": "Manhattan",
    "queens-properties": "Queens",
    "staten-island-properties": "Staten Island",
}
_PAGE = re.compile(rf"^{re.escape(SITE)}/([a-z-]+-properties)/([A-Za-z0-9-]+)/?$")
_NO_UNITS = re.compile(r"\bno (available )?units\b|\bno units available\b|\bno (current )?vacanc", re.I)


def parse_sitemap(xml: str) -> list[str]:
    """Building page URLs in sitemap order (a collection's own page and its category/tag pages are skipped)."""
    out = []
    for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
        m = _PAGE.match(loc)
        if not m:
            continue
        if m.group(1) not in BOROUGHS:
            raise RuntimeError(f"ELH sitemap: unknown property collection {m.group(1)!r} ({loc})")
        out.append(loc.rstrip("/"))
    if not out:
        raise RuntimeError("ELH sitemap lists no property pages (layout changed?)")
    slugs = [u.rsplit("/", 1)[-1] for u in out]
    if len(set(slugs)) != len(slugs):
        raise RuntimeError(f"ELH sitemap: a property slug repeats across collections: {sorted(slugs)}")
    return out


def _field(body, label: str) -> str | None:
    """'Status: Waitlist Open' -> 'Waitlist Open', from the paragraph that starts with the label."""
    for p in body.find_all(["p", "h3", "h4", "li"]):
        t = re.sub(r"\s+", " ", p.get_text(" ")).strip()
        m = re.match(rf"{label}\s*:\s*(.+)$", t, re.I)
        if m:
            return m.group(1).strip()
    return None


def status_of(status: str, availability: str | None) -> PublishedStatus:
    """The page's Status and Availability lines -> a published status. Wording it does not know raises."""
    s, a = status.lower(), (availability or "").lower()
    if a and not _NO_UNITS.search(a) and re.search(r"\b(units?|apartments?|vacanc(y|ies)) available\b", a):
        return "open"
    if re.fullmatch(r"waitlist open|open|accepting applications", s):
        return "waitlist"
    if re.fullmatch(r"waitlist closed|closed", s):
        return "closed"
    raise RuntimeError(f"ELH: unrecognised status {status!r} / availability {availability!r}")


def parse_page(url: str, html: str) -> Listing:
    m = _PAGE.match(url)
    if not m:
        raise RuntimeError(f"ELH: not a property page URL: {url}")
    s = parse.soup(html)
    h1 = s.find("h1", class_="entry-title")
    body = s.find("div", class_="blog-item-content")
    if h1 is None or body is None:
        raise RuntimeError(f"{url}: no post title or body (layout changed?)")
    status = _field(body, "Status")
    if status is None:
        raise RuntimeError(f"{url}: no 'Status:' line")
    availability = _field(body, "Availability")
    h4 = body.find("h4")
    address = re.sub(r"\s+", " ", h4.get_text(" ")).strip() if h4 else None
    if not address:
        raise RuntimeError(f"{url}: no address heading")
    published = status_of(status, availability)

    links = []
    for a in body.find_all("a", href=True):
        href = urljoin(url, a["href"].strip())
        if href.startswith("http") and "squarespace" not in href and "elhrerentals.com" not in href:
            links.append(href.split("#")[0])
    applies_here = bool(body.find("a", string=re.compile(r"apply now", re.I))) or any(
        a["href"].rstrip("/").endswith("/online-application") for a in body.find_all("a", href=True)
    )
    notes = [f"Status: {status}" + (f"; availability: {availability}" if availability else "")]
    if links:
        notes.append("Apply via " + ", ".join(dict.fromkeys(links)))
    elif applies_here and published != "closed":
        notes.append(f"Apply: ELH's one online application ({APPLY}) adds you to the waitlist")
    notes.append(f"Questions: {CONTACT}")
    return Listing(
        source="elh",
        ext_id=m.group(2),
        title=re.sub(r"\s+", " ", h1.get_text(" ")).strip(),
        url=url,
        address=address,
        borough=BOROUGHS[m.group(1)],
        status=published,
        kind="re-rental" if published == "open" else "waitlist",
        units=[],
        notes="; ".join(notes),
    )


class ELH(Adapter):
    name = "elh"
    title = "ELH Management"
    url = f"{SITE}/available-units"
    min_listings = 12  # 21 building pages on 2026-11-07

    def fetch(self) -> Result:
        urls = parse_sitemap(fetch.html(SITEMAP))
        pages: dict = self.cache.setdefault("pages", {})
        out = []
        for u in urls:
            hit = pages.setdefault(u, {})
            if "listing" not in hit:
                hit.pop("etag", None)
                hit.pop("last_modified", None)
            raw, _ = fetch.conditional(u, hit)
            if raw is None:
                out.append(cached_listing(hit["listing"]))
                continue
            lst = parse_page(u, raw.decode("utf-8", "replace"))
            hit["listing"] = asdict(lst)
            out.append(lst)
        prune(pages, urls)
        return Result(listings=out)
