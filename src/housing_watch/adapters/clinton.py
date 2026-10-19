"""Clinton Management (Douglaston Companies): static WordPress page of affordable properties.

The page has two sections of property cards: "Properties With Current Vacancies" (h2 id
affordable-current-vacancies) and "Properties With No Current Vacancies". Each card becomes a Listing,
open in the first section and closed in the second, so a property moving up is a "reopened" event. A
card's "Unit types" describe the building, not openings; rents, AMI and income limits are only behind
an open card's own links (kept in notes). The page itself is also snapshotted for anything else.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Listing, Result, Unit
from ._page import PageAdapter
from ._pagekit import snapshot

URL = "https://www.clintonmanagement.com/availabilities/affordable/"
CONTACT = "cm_leasing@cmnyc.com, (347) 287-3100"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_cards(html: str, url: str = URL) -> list[Listing]:
    s = parse.soup(html)
    out = []
    for sec in s.select("div.affordable-property-section"):
        h2 = sec.find("h2")
        if h2 is None:
            continue
        open_ = h2.get("id") == "affordable-current-vacancies" or (
            "no current" not in h2.get_text().lower() and "current vacancies" in h2.get_text().lower()
        )
        for card in sec.select("article.affordable-property-card"):
            t = card.select_one(".affordable-property-card__title")
            title = t.get_text(" ", strip=True) if t else card.get_text(" ", strip=True)[:80]
            a = card.select_one(".affordable-property-card__address")
            addr = a.get_text(" ", strip=True) if a else None
            ut = card.select_one(".affordable-property-card__unit-types")
            types = []
            if ut:
                raw = ut.get_text(" ", strip=True).removeprefix("Unit types").strip()
                types = [x.strip() for x in raw.split(",") if x.strip()]
            senior = any(re.search(r"62\+|senior", x, re.I) for x in types) or "senior" in title.lower()
            set_aside = "mobility" if any("mobility" in x.lower() for x in types) else None
            sizes = dict.fromkeys(parse.size(x) for x in types if parse.size(x) != "unknown")  # unique, in order
            units = [Unit(size=sz, set_aside=set_aside) for sz in sizes]
            if not units and set_aside:
                units = [Unit(size="unknown", set_aside=set_aside)]
            links = sorted({urljoin(url, x["href"]) for x in card.find_all("a", href=True)})
            notes = f"Unit types: {', '.join(types) or 'n/a'}. Contact {CONTACT}."
            if links:
                notes += " Links: " + " ".join(links)
            out.append(
                Listing(
                    source="clinton",
                    ext_id=slug(title),
                    title=title,
                    url=links[0] if open_ and links else url,
                    address=addr,
                    borough=parse.borough(addr, title),
                    status="open" if open_ else "closed",
                    units=units,
                    senior_only=senior,
                    notes=notes,
                )
            )
    return out


class Clinton(PageAdapter):
    name = "clinton"
    title = "Clinton Management"
    url = URL
    pages = [URL]
    every_hours = 12
    min_text = 800
    link_re = re.compile(r"clintonmanagement\.com/(properties|availabilities)/.+", re.I)
    min_listings = 3  # 7 property cards on 2026-10-18

    def fetch(self) -> Result:
        body = fetch.html(URL)
        listings = parse_cards(body)
        if len(listings) < self.min_listings:
            raise RuntimeError(f"{URL}: only {len(listings)} property cards (layout changed?)")
        return Result(listings=listings, snapshots=[snapshot(self, URL, body)])
