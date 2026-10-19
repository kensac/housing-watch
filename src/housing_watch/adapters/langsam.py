"""Langsam Property Services: one listing shown inline as an HTML table, others only as "Downloadable Documents".

Inline listing: address heading + HTML income table. Other buildings: an "Attachment" PDF (HPD ad, Attachment
F-2) read with sois.parse_hpd_ad. Each building's "Application" PDF carries the postmark deadline and the
mailing address; listings stay on the page long after the deadline, so a past deadline means closed.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from ._page import PageAdapter
from .sois import _pdf_text, deadline_iso, mail_to, parse_hpd_ad, past, prune_pdf_cache

URL = "https://www.langsampropertyservices.com/affordable-rental-opportunities"
_HH = re.compile(r"^(\d)\s*(?:persons?|people)$", re.I)


def _slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def _key(t: str) -> str:
    """Building key from a document label or address: '603 Pontiac Place Application' -> '603 pontiac place'."""
    t = re.sub(r"\b(application|applicaion|attachment|f-2)\b.*$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip().lower()


def parse_page(html: str) -> dict:
    """Inline listing (address, units) and the document links grouped by building."""
    s = parse.soup(html)
    txt = parse.text(s)
    m = re.search(r"Affordable housing for rent:?\n(.+)", txt, re.I)
    address = m.group(1).strip() if m else None
    units: list[Unit] = []
    ami = None
    cur: Unit | None = None
    for cells in parse.table_rows(s):
        joined = " ".join(cells)
        if re.search(r"%\s*AREA", joined, re.I):
            ami = parse.ami(joined)
            continue
        size_cell = next((c for c in cells if parse.size(c) != "unknown" and "$" not in c), None)
        if size_cell and any("$" in c for c in cells):
            rent = next(parse.money(c) for c in cells if "$" in c)
            cur = Unit(size=parse.size(size_cell), rent=rent, ami=ami)
            units.append(cur)
        if cur is None:
            continue
        for i, c in enumerate(cells):
            hm = _HH.match(c.strip())
            if hm and i + 1 < len(cells):
                lo, hi = parse.money_range(cells[i + 1])
                cur.bands.append(Band(int(hm.group(1)), lo, hi))
    docs: dict[str, dict] = {}
    for a in s.find_all("a", href=True):
        label = a.get_text(" ", strip=True)
        href = urljoin(URL, a["href"])
        if ".pdf" not in href.lower() or not re.search(r"application|applicaion|attachment", label, re.I):
            continue
        kind = "app" if re.search(r"applica", label, re.I) else "ad"
        docs.setdefault(_key(label), {})[kind] = href
    emails = sorted(set(re.findall(r"[\w.+-]+@langsamprop\.com", txt)))
    return {"address": address, "units": units, "docs": docs, "emails": emails}


def _notes(app_text: str | None, app_url: str | None, emails: list[str]) -> str:
    out = []
    mail = mail_to(app_text or "")
    if mail:
        only = " (standard mail only)" if re.search(r"standard mail only", app_text or "", re.I) else ""
        out.append(f"Mail paper application{only} to {mail}")
    if app_url:
        out.append(f"Application: {app_url}")
    if emails:
        out.append("Questions: " + ", ".join(emails) + ", 718-518-8000 x274")
    return "; ".join(out)


def build_listings(page: dict, pdfs: dict[str, str], today: date | None = None) -> list[Listing]:
    """pdfs: url -> extracted text for every document link."""
    out = []
    inline_key = _key(page["address"].split(",")[0]) if page["address"] else None
    for key, d in page["docs"].items():
        app_t = pdfs.get(d.get("app", ""))
        ad_t = pdfs.get(d.get("ad", ""))
        dl = deadline_iso(app_t or "") or deadline_iso(ad_t or "")
        if inline_key and key == inline_key:
            units, address = page["units"], page["address"]
        else:
            units = parse_hpd_ad(ad_t) if ad_t else []
            m = re.search(r"units? at (.+?)\n", ad_t or "", re.I)
            address = m.group(1).strip().title() if m else key.title()
        out.append(
            Listing(
                source="langsam",
                ext_id=_slug(key),
                title=key.title(),
                url=URL,
                address=address,
                borough=parse.borough(address, (ad_t or "")[:500]),
                status="closed" if past(dl, today) else "open",
                kind="lottery",
                units=units,
                deadline=dl,
                senior_only=bool(re.search(r"\b62\s*(\+|years|or older)", ad_t or "", re.I)),
                notes=_notes(app_t, d.get("app"), page["emails"]),
            )
        )
    if inline_key and inline_key not in page["docs"]:
        out.append(
            Listing(
                source="langsam",
                ext_id=_slug(inline_key),
                title=page["address"],
                url=URL,
                address=page["address"],
                borough=parse.borough(page["address"]),
                kind="lottery",
                units=page["units"],
                notes=_notes(None, None, page["emails"]),
            )
        )
    return out


class Langsam(PageAdapter):
    name = "langsam"
    title = "Langsam Property Services"
    url = URL
    pages = [URL]
    every_hours = 4
    min_listings = 1

    def fetch(self) -> Result:
        snaps = super().fetch().snapshots
        page = parse_page(fetch.html(URL))
        urls = {u for d in page["docs"].values() for u in d.values()}
        pdfs = {u: _pdf_text(self.cache, u) for u in sorted(urls)}
        prune_pdf_cache(self.cache, urls)
        return Result(listings=build_listings(page, pdfs), snapshots=snaps)
