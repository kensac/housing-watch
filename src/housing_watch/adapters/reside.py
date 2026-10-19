"""Reside New York: ~2,100 WordPress property pages, listed with lastmod in two sitemaps.

Only pages whose lastmod moved are re-fetched (cache: url -> {lastmod, listing}). Status is the label
right before the page title ("Open Market" / "Upcoming Project" / "Closed Project"); two of those also
sit in the site menu of every page, so they are never grepped page-wide. Applying is one shared
Airtable form.
"""

from __future__ import annotations

import re
from dataclasses import asdict

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

SITEMAPS = [f"https://residenewyork.com/wp-sitemap-posts-property-{i}.xml" for i in (1, 2)]
APPLY = "https://airtable.com/appsseXTOVx59HC0W/pagcVengefPFQvMZC/form"


def parse_page(url: str, html: str) -> Listing:
    s = parse.soup(html)
    title = (s.find("meta", property="og:title") or {}).get("content") or (s.title.string if s.title else url)
    title = re.sub(r"\s*[-|–]\s*Reside New York.*$", "", title or "").strip()
    flat = re.sub(r"\s+", " ", parse.text(s))
    i = flat.find("Per Month")
    head = flat[max(0, i - 200) : i] if i >= 0 else ""
    # the page's own label sits right before its title; the site menu also lists two of the labels
    lab = re.search(r"(Open Market|Upcoming Project|Closed Project)\s+" + re.escape(title[:25]), flat)
    label = lab.group(1) if lab else head
    if "Open Market" in label:
        status = "open"
    elif "Upcoming Project" in label:
        status = "upcoming"  # header shows only a size/income range; units post later
    elif "Closed" in label:
        status = "closed"
    else:
        status = "open"
    units: list[Unit] = []
    for cells in parse.table_rows(s):
        if len(cells) < 4 or not re.search(r"studio|bed|br\b", cells[0], re.I):
            continue
        hh_i = next((k for k, c in enumerate(cells) if re.search(r"person|people", c, re.I)), None)
        inc = cells[-1]
        a = next((parse.ami(c) for c in cells if parse.ami(c)), None)
        rent = next((parse.money(c) for c in cells[1:] if "$" in c), None)
        bands = parse.bands_from_row(cells[hh_i], inc, a) if hh_i is not None else []
        set_aside = None
        if re.search(r"\b(mi|mobility|hearing|visual|h/v|senior|62\+)\b", " ".join(cells), re.I):
            set_aside = "disability/senior"
        units.append(Unit(size=parse.size(cells[0]), rent=rent, ami=a, bands=bands, set_aside=set_aside))
    m = re.search(r"unit[-\s]*(?:apt[-\s]*)?([0-9]+[a-z]?|[a-z][0-9]+)\b", url.rstrip("/").split("/")[-1], re.I)
    if m and len(units) == 1:
        units[0].unit_no = m.group(1).upper()
    addr = re.sub(r",?\s*(Apartment|Apt\.?|Unit)\b.*$", "", title, flags=re.I)
    return Listing(
        source="reside",
        ext_id=url.rstrip("/").split("/")[-1],
        title=title,
        url=url,
        address=addr,
        # the borough is printed between the title and the rent; the footer's office address is Manhattan
        borough=parse.borough(re.split(r"Open Market|Closed Project|Upcoming Project", head)[-1])
        or parse.borough(title),
        status=status,
        senior_only=bool(re.search(r"\bsenior\b|62 (years|and older)", flat[:4000], re.I)),
        units=units,
        notes=f"Apply: Airtable form {APPLY} (one unit per submission)",
    )


def _from_dict(d: dict) -> Listing:
    units = [Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]}) for u in d["units"]]
    return Listing(**{**d, "units": units})


class Reside(Adapter):
    name = "reside"
    title = "Reside New York"
    url = "https://residenewyork.com/property-status/open-market/"
    every_hours = 3
    min_listings = 1000

    def fetch(self) -> Result:
        entries = []
        for sm in SITEMAPS:
            xml = fetch.html(sm)
            entries += re.findall(r"<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?", xml)
        if len(entries) < self.min_listings:
            raise RuntimeError(f"sitemaps list only {len(entries)} properties")
        cache: dict = self.cache.setdefault("pages", {})
        out = []
        for url, lastmod in entries:
            hit = cache.get(url)
            if hit and hit.get("lastmod") == lastmod and lastmod:
                out.append(_from_dict(hit["listing"]))
                continue
            lst = parse_page(url, fetch.html(url))
            cache[url] = {"lastmod": lastmod, "listing": asdict(lst)}
            out.append(lst)
        live = {u for u, _ in entries}
        for u in [u for u in cache if u not in live]:
            del cache[u]
        return Result(listings=out)
