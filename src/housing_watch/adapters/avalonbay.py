"""AvalonBay: affordable-program pages of its New York City buildings.

The community list (/affordable-housing/community-list/) links every program page; the NYC ones live
under /new-york/<city>-apartments/<building>-affordable-program (six in Manhattan on 2026-10-19). A page
has one block per AMI tier (section.marketing-home-page-side-by-side-blocks, heading "40% PROGRAM"):

    STUDIO: $1,038 PER MONTH*
    1 person household $45,360              <- max income only (low tiers)
    1 person household $109,269 - $147,420  <- min - max (130% tiers)
    [Waitlist Closed]                       <- button: the tier's status

One Listing per building with one Unit per (tier, size); a closed tier's units are available=False and
the building is open/waitlist/closed from its best tier. The address comes from the page's JSON-LD.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

BASE = "https://www.avaloncommunities.com"
LIST = f"{BASE}/affordable-housing/community-list/"
_NYC = re.compile(
    r"/new-york/(new-york-city|brooklyn|queens|long-island-city|bronx|staten-island)-apartments/"
    r"([a-z0-9-]+)-affordable-program/?$"
)
_CITY_BOROUGH = {
    "new-york-city": "Manhattan",
    "brooklyn": "Brooklyn",
    "queens": "Queens",
    "long-island-city": "Queens",
    "bronx": "Bronx",
    "staten-island": "Staten Island",
}
_SIZE_LINE = re.compile(r"^([A-Z][A-Z -]+?):\s*\$([\d,]+(?:\.\d+)?)\s*PER MONTH", re.I)
_HH_LINE = re.compile(r"^(\d+)\s+persona?l?\s+household\s+(.+)$", re.I)  # "2 personal household" typo seen


_ANY_URL = re.compile(
    r"(?:https://www\.avaloncommunities\.com)?/new-york/[a-z-]+-apartments/[a-z0-9-]+-affordable-program/?"
)


def parse_list(html: str) -> list[str]:
    """NYC program URLs, read from the raw HTML: the list is also embedded as an escaped JSON string, and
    some links are commented out there (Avalon Morningside Park on 2026-10-19) while the page is live."""
    urls = {}
    for m in _ANY_URL.finditer(html.replace("\\/", "/")):
        href = urljoin(BASE, m.group(0))
        if _NYC.search(href):
            urls[href.rstrip("/") + "/"] = None
    return list(urls)


def tier_status(text: str) -> str:
    t = text.lower()
    if "closed" in t:
        return "closed"
    if re.search(r"wait\s*-?\s*list|waiting list", t):
        return "waitlist"
    return "open"


def parse_tier(sec) -> tuple[int | None, str, list[Unit], str]:
    """(ami, status, units, status text) for one tier block."""
    h3 = sec.find("h3")
    ami = parse.ami(h3.get_text(" ", strip=True)) if h3 else None
    desc = sec.select_one("#mkhp-sbsb-content-data-desc") or sec
    buttons = [b.get_text(" ", strip=True) for b in desc.find_all("button")]
    label = " ".join(buttons[0].split()) if buttons else ""
    status = tier_status(label) if label else "open"
    units: list[Unit] = []
    for line in parse.text(str(desc)).splitlines():
        line = line.strip()
        m = _SIZE_LINE.match(line)
        if m:
            units.append(
                Unit(
                    size=parse.size(m.group(1).replace("-", " ")),
                    rent=parse.money(m.group(2)),
                    ami=ami,
                    available=False if status == "closed" else None,
                )
            )
            continue
        m = _HH_LINE.match(line)
        if m and units:
            lo, hi = parse.money_range(m.group(2))
            if hi is None:  # a single figure is the maximum
                lo, hi = None, lo
            units[-1].bands.append(Band(int(m.group(1)), lo, hi))
    return ami, status, units, label


def _address(s) -> str | None:
    for tag in s.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except ValueError:
            continue
        for d in data if isinstance(data, list) else [data]:
            a = d.get("address") if isinstance(d, dict) else None
            if isinstance(a, dict) and a.get("streetAddress"):
                parts = [a.get("streetAddress"), a.get("addressLocality"), a.get("addressRegion")]
                return ", ".join(p for p in parts if p) + (f" {a['postalCode']}" if a.get("postalCode") else "")
    return None


def parse_page(url: str, html: str) -> Listing:
    s = parse.soup(html)
    m = _NYC.search(url)
    if not m:
        raise ValueError(f"not an NYC program url: {url}")
    city, slug = m.groups()
    raw_title = s.title.get_text(strip=True) if s.title else ""
    name = re.sub(r"^Affordable Housing Program at\s+", "", raw_title.split("|")[0].strip(), flags=re.I)
    if not name or name.lower() == "affordable housing program":
        name = slug.replace("-", " ").title().replace("Ava ", "AVA ")
    units: list[Unit] = []
    statuses: list[str] = []
    labels: list[str] = []
    for sec in s.select("section.marketing-home-page-side-by-side-blocks"):
        h3 = sec.find("h3")
        if not h3 or "program" not in h3.get_text().lower():
            continue
        ami, status, tier_units, label = parse_tier(sec)
        units += tier_units
        statuses.append(status)
        labels.append(f"{ami}%: {label or 'no status shown'}")
    if not statuses:
        raise RuntimeError(f"{url}: no '<n>% PROGRAM' blocks (layout changed?)")
    status = "open" if "open" in statuses else "waitlist" if "waitlist" in statuses else "closed"
    addr = _address(s)
    return Listing(
        source="avalonbay",
        ext_id=slug,
        title=name,
        url=url,
        address=addr,
        borough=parse.borough(addr) or _CITY_BOROUGH[city],
        status=status,
        kind="waitlist",
        units=units,
        notes="Tiers: " + "; ".join(labels) + ". Apply through the program page when a waitlist opens.",
    )


class AvalonBay(Adapter):
    name = "avalonbay"
    title = "AvalonBay NYC affordable programs"
    url = LIST
    every_hours = 4
    min_listings = 4  # 6 Manhattan buildings on 2026-10-19

    def fetch(self) -> Result:
        urls = parse_list(fetch.html(LIST))
        if len(urls) < self.min_listings:
            raise RuntimeError(f"{LIST}: only {len(urls)} NYC program links (layout changed?)")
        return Result(listings=[parse_page(u, fetch.html(u)) for u in urls])
