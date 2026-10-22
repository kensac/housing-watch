"""RiseBoro Community Partnership: building pages listed by the WordPress JSON API (`/wp-json/wp/v2/housing`).

Each page has "Neighborhood:", "Status:" (Operating/Upcoming) and, only while a waiting list is open, a
"Waiting List Application: Click here" link to a PDF. No page publishes rents or income bands, so every
listing has units=[]; buildings without an application link are returned as closed so an opening shows up
as a reopened listing. The application PDF (cached by content hash) says whether it is seniors-only.
"""

from __future__ import annotations

import html as _html
import re
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Listing, Result
from ._page import PageAdapter
from .sois import _pdf_text, deadline_iso, prune_pdf_cache

API = "https://riseboro.org/wp-json/wp/v2/housing?per_page=100&_fields=id,link,title,slug,modified"
URL = "https://riseboro.org/housing/woodlawn-senior-living/"
_HOODS = {"east new york": "Brooklyn", "bushwick": "Brooklyn", "brownsville": "Brooklyn", "ridgewood": "Queens"}
SENIOR_RE = re.compile(r"\b62\s*\+|62 years|seniors? (?:only|who)|must be a senior", re.I)


def _field(txt: str, label: str) -> str | None:
    m = re.search(rf"^{label}:\s*\n?(.+)$", txt, re.I | re.M)
    return m.group(1).strip() if m else None


def parse_building(link: str, title: str, html: str) -> dict:
    s = parse.soup(html)
    txt = parse.text(s)
    app = None
    for a in s.find_all("a", href=True):
        prev = a.find_previous(string=re.compile(r"Application", re.I))
        if ".pdf" in a["href"].lower() and prev and re.search(r"wait(ing)? ?list application|application:", prev, re.I):
            app = urljoin(link, a["href"])
            break
    return {
        "link": link,
        "title": _html.unescape(title),
        "neighborhood": _field(txt, "Neighborhood"),
        "status": _field(txt, "Status"),
        "app": app,
    }


def build_listing(b: dict, app_text: str | None) -> Listing:
    is_senior = bool(re.search(r"\bsenior\b", b["title"], re.I)) or bool(SENIOR_RE.search(app_text or ""))
    if b["app"]:
        notes = f"Waiting list paper application: {b['app']}"
        if re.search(r"first come,? first.serve", app_text or "", re.I):
            notes += " (first come, first served)"
        if re.search(r"217 Wyckoff Avenue", app_text or "", re.I):
            notes += "; RiseBoro, 217 Wyckoff Avenue, Brooklyn NY 11237, 718-366-3800"
    else:
        notes = f"No open application ({b['status'] or 'status unknown'}); lotteries go through Housing Connect"
    dl = deadline_iso(app_text or "")
    return Listing(
        source="riseboro",
        ext_id=b["link"].rstrip("/").split("/")[-1],
        title=b["title"],
        url=b["link"],
        address=None,
        borough=parse.borough(b["neighborhood"])
        or next((v for k, v in _HOODS.items() if k in (b["neighborhood"] or "").lower()), None),
        status="waitlist" if b["app"] else "closed",
        kind="waitlist",
        units=[],
        deadline=dl,
        senior_only=is_senior,
        notes=notes,
    )


class RiseBoro(PageAdapter):
    name = "riseboro"
    title = "RiseBoro Community Partnership"
    url = URL
    pages = [URL]
    min_listings = 1

    def fetch(self) -> Result:
        snaps = super().fetch().snapshots
        items = fetch.json(API)
        if len(items) < 5:
            raise RuntimeError(f"housing API lists only {len(items)} buildings")
        out, seen = [], set()
        for it in items:
            b = parse_building(it["link"], it["title"]["rendered"], fetch.html(it["link"]))
            app_text = None
            if b["app"]:
                seen.add(b["app"])
                app_text = _pdf_text(self.cache, b["app"])
            out.append(build_listing(b, app_text))
        prune_pdf_cache(self.cache, seen)
        return Result(listings=out, snapshots=snaps)
