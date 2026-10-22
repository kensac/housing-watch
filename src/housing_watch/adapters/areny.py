"""About Real Estate NY (ARENY): a Squarespace page with one "Looking for an affordable apartment?" block.

With nothing to rent the block says "We don't have any apartments available right now, but complete our
Housing Interest Form ..." (2026-10-19). The page (main content only; the nav repeats three times) is
snapshotted. When that sentence is gone, the block becomes one Listing (ext_id "availability") carrying
whatever sizes/rents it prints, so the change is a "new" event, not just a page diff. The rest of the
page is a grid of past projects (image links), which are not listings.

The runner only records listings when a result has some or has no snapshots, so the run right after
listings disappear returns no snapshot: that run marks the listing gone; the next one snapshots again.
?format=json is disallowed by robots.txt, so the HTML is parsed.
"""

from __future__ import annotations

import re

from .. import fetch, parse
from ..models import Listing, Result, Unit
from ._page import PageAdapter
from ._pagekit import snapshot

URL = "https://www.aboutrealestateny.com/affordable-housing-marketing"
INTEREST_FORM = "https://airtable.com/appe3rWk63kuOODnt/pagagqn6gz04jzjAu/edit"
_MARKER = re.compile(r"looking for an affordable apartment", re.I)
_NONE = re.compile(r"don.t have any (apartments|units|listings) available", re.I)
_RENT_LINE = re.compile(r"(studio|\d\s*-?\s*(?:br|bd|bed(?:room)?s?))\b.*?\$\s*([\d,]{3,})", re.I)


def availability_block(html: str) -> str:
    """Visible text of the page section that holds the availability message."""
    s = parse.soup(html)
    hit = s.find(string=_MARKER)
    if hit is None:
        raise RuntimeError(f"{URL}: 'Looking for an affordable apartment?' block not found (layout changed?)")
    sec = hit.find_parent("section") or hit.find_parent("div", class_="sqs-html-content")
    return parse.text(str(sec))


def parse_availability(html: str) -> list[Listing]:
    block = availability_block(html)
    if _NONE.search(block):
        return []
    txt = block[block.lower().find("looking for an affordable apartment") :]
    units = []
    for line in txt.splitlines():
        hits = list(_RENT_LINE.finditer(line))
        for i, m in enumerate(hits):  # "Studio $2,100 (130% AMI), 1 Bedroom $2,450": AMI from each segment
            seg = line[m.start() : hits[i + 1].start() if i + 1 < len(hits) else len(line)]
            units.append(Unit(size=parse.size(m.group(1)), rent=parse.money(m.group(2)), ami=parse.ami(seg)))
    return [
        Listing(
            source="areny",
            ext_id="availability",
            title="ARENY: affordable apartments posted",
            url=URL,
            borough=parse.borough(txt),
            status="open",
            kind="re-rental",
            units=units,
            notes=f"{' / '.join(txt.splitlines())[:1200]}. Interest form: {INTEREST_FORM}",
        )
    ]


class ARENY(PageAdapter):
    name = "areny"
    title = "About Real Estate NY (ARENY)"
    url = URL
    pages = [URL]
    min_text = 150
    min_listings = 0  # usually only the snapshot; the flush run (see module doc) returns nothing

    def fetch(self) -> Result:
        body = fetch.html(URL)  # fetch's default UA is a desktop Chrome
        listings = parse_availability(body)
        had = self.cache.get("had_listings", False)
        self.cache["had_listings"] = bool(listings)
        if had and not listings:
            return Result()  # let the runner mark the availability listing gone
        return Result(listings=listings, snapshots=[snapshot(self, URL, body, root="main")])
