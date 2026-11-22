"""LARRIS Marketing & Compliance: GoDaddy re-rentals page whose postings are custom-HTML embeds.

Each posting is an <iframe srcdoc> (GoDaddy's HTML block), so the page's own visible text is boilerplate
only. In an embed, a block holding `property-detail` items is one apartment: the building name (h1), the
address (`*-address`), "Studio Apartment — Apartment 2C" (h2), UNIT TYPE / AMI / MONTHLY RENT, and
`income-row`s of household size and maximum income. Applying is the page's inquiry form.
"""

from __future__ import annotations

import re

from bs4 import Tag

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from ._pagekit import GODADDY_IGNORE, GoDaddyPage, snapshot, wsimg_images

URL = "https://larrismarketing.com/re-rentals"


def _txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def _block(details: Tag) -> Tag:
    """The largest ancestor of a posting's `property-details` that holds no other posting's: its name, address
    and income card are siblings of the details, not children."""
    node = details
    while node.parent is not None and len(node.parent.find_all(class_="property-details")) == 1:
        node = node.parent
    return node


def parse_posting(url: str, block: Tag) -> Listing:
    name = _txt(block.find("h1"))
    addr = re.sub(r"^⌖\s*", "", _txt(block.find(class_=re.compile(r"address$"))))
    head = _txt(block.find("h2"))
    details = {
        _txt(d.find(class_="detail-label")).upper(): _txt(d.find(class_="detail-value"))
        for d in block.find_all(class_="property-detail")
    }
    m = re.search(r"\b(?:apartment|apt\.?|unit)\s*#?\s*([\w-]+)\s*$", head, re.I)
    if not (name and addr and m and "MONTHLY RENT" in details):
        raise RuntimeError(f"{url}: posting {name or head!r} lacks a name, address, unit or rent")
    unit_no = m.group(1).upper()
    bands = []
    for row in block.find_all(class_="income-row"):
        hh = parse.household_range(_txt(row.find("span")))
        top = parse.whole(_txt(row.find("strong")))
        if len(hh) != 1 or top is None:
            raise RuntimeError(f"{url}: unreadable income row {_txt(row)!r}")
        bands.append(Band(hh[0], None, top))
    unit = Unit(
        size=parse.size(details.get("UNIT TYPE") or head),
        rent=parse.money(details["MONTHLY RENT"]),
        ami=parse.ami(details.get("AMI")),
        unit_no=unit_no,
        bands=bands,
    )
    return Listing(
        source="larris",
        ext_id=parse.slug(f"{name} {unit_no}"),
        title=f"{name} #{unit_no}",
        url=url,
        address=addr,
        borough=parse.borough(addr),
        units=[unit],
        notes="Apply: submit the inquiry form on the re-rentals page",
    )


def parse_page(url: str, html: str) -> list[Listing]:
    out = []
    for frame in parse.soup(html).find_all("iframe", srcdoc=True):
        doc = parse.soup(frame["srcdoc"])
        blocks = doc.find_all(class_="property-details")
        if not blocks and re.search(r"monthly rent", parse.text(doc), re.I):
            raise RuntimeError(f"{url}: an embed shows a rent but no property-details block (layout changed?)")
        out += [parse_posting(url, _block(d)) for d in blocks]
    return out


class Larris(GoDaddyPage):
    name = "larris"
    title = "LARRIS Marketing & Compliance"
    url = URL
    pages = [URL]
    ignore = GODADDY_IGNORE
    min_text = 600  # the page's own boilerplate alone is ~1,400 chars

    def fetch(self) -> Result:
        body = fetch.html(URL)
        snap = snapshot(self, URL, body, extra_links=wsimg_images(body))
        return Result(listings=parse_page(URL, body), snapshots=[snap])
