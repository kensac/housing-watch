"""The Bridge: re-rental cards on the Wix news page, each with a one-page flyer PDF and an application PDF.

A card is the Wix container holding a "The Bridge Affordable Housing Re-Rental" heading plus its links.
Flyers come in two shapes: a table (size, rent, units, "1 - 3 PERSONS", income range) or free text
("Monthly Rent: $1,687", "1 Person - $0.00 to $56,700"). Their address line is letter-spaced
("3 5 0 0   P A R K") and the AMI label is sometimes stored reversed ("EMOCNI NAIDEM AERA % 04").
"""

from __future__ import annotations

import re

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from ._page import PageAdapter
from .sois import _pdf_text, mail_to, prune_pdf_cache

URL = "https://www.thebridgeny.org/news-and-media"
CARD_RE = re.compile(r"Affordable Housing Re-Rental", re.I)


def parse_cards(html: str) -> list[dict]:
    """Cards: blurb text, flyer url, application url, mailto addresses."""
    s = parse.soup(html)
    out = []
    for n in s.find_all(string=CARD_RE):
        el = n.parent
        while el is not None and not any(re.search(r"\.pdf", a.get("href", "")) for a in el.find_all("a")):
            el = el.parent
        if el is None:
            continue
        flyer = app = None
        emails = []
        for a in el.find_all("a", href=True):
            href, label = a["href"], a.get_text(" ", strip=True).lower()
            if href.startswith("mailto:"):
                emails.append(href[7:].split("?")[0])
            elif ".pdf" in href and "application" in label:
                app = app or href
            elif ".pdf" in href:
                flyer = flyer or href
        out.append({"text": parse.text(str(el)), "flyer": flyer, "app": app, "emails": emails})
    return out


def despace(line: str) -> str:
    """'3 5 0 0      P A R K    A V E .' -> '3500 PARK AVE.' (letter-spaced words, 2+ spaces between words)."""
    chunks = [c for c in re.split(r"\s{2,}", line.strip()) if c]
    if chunks and all(re.fullmatch(r"(\S )+\S", c) or len(c) == 1 for c in chunks):
        return " ".join(c.replace(" ", "") for c in chunks)
    return line.strip()


def flyer_ami(text: str) -> int | None:
    a = parse.ami(text.replace("\n", " ")) if re.search(r"%\s*area median", text, re.I) else None
    if a:
        return a
    for ln in text.splitlines():  # mirrored label
        rev = ln.strip()[::-1]
        if re.search(r"%\s*area median", rev, re.I):
            m = re.search(r"(\d{1,3})\s*%", rev)
            if m:
                return int(m.group(1))
    return parse.ami(text)


def _size(t: str) -> str:
    return parse.size(re.sub(r"\bBDR?\b", "BR", t, flags=re.I))


_ROW = re.compile(
    r"^\s*(?P<size>studio|\d\s*bdr?\b[^$]*?|\d\s*bedroom[^$]*?)\s{2,}\$(?P<rent>[\d,]+(?:\.\d\d)?)\s+(?P<n>\d+)\s+"
    r"(?P<hh>\d+\s*(?:[-–]\s*\d+\s*)?persons?)\s+(?P<inc>\$[\d,.]+\s*[-–]\s*\$[\d,.]+)",
    re.I | re.M,
)
_BAND = re.compile(r"(\d)\s*persons?\s*[-–:]\s*\$\s?([\d,]+(?:\.\d\d)?)\s*(?:to|[-–])\s*\$\s?([\d,]+(?:\.\d\d)?)", re.I)


def parse_flyer(text: str) -> dict:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    address = despace(lines[0]) if lines else None
    ami = flyer_ami(text)
    senior = bool(re.search(r"\bsenior\b|62\s*\+|62 years", text, re.I))
    units: list[Unit] = []
    for m in _ROW.finditer(text):
        units.append(
            Unit(
                size=_size(m.group("size")),
                rent=parse.money(m.group("rent")),
                ami=ami,
                bands=parse.bands_from_row(m.group("hh"), m.group("inc"), ami),
            )
        )
    if not units:
        rent = re.search(r"monthly rent:?\s*\$\s?([\d,]+(?:\.\d\d)?)", text, re.I)
        bands = [Band(int(h), parse.whole(lo), parse.whole(hi)) for h, lo, hi in _BAND.findall(text)]
        size = next((s for s in (_size(ln) for ln in lines) if s != "unknown"), "unknown")
        if rent or bands:
            units.append(Unit(size=size, rent=parse.money(rent.group(1)) if rent else None, ami=ami, bands=bands))
    if senior:
        for u in units:
            u.set_aside = "senior"
    return {"address": address, "units": units, "senior": senior}


def build_listing(card: dict, flyer_text: str, app_text: str | None) -> Listing:
    f = parse_flyer(flyer_text or "")
    addr = f["address"] or None
    blurb = card["text"]
    blines = blurb.splitlines()
    title = addr or (blines[1] if len(blines) > 1 else blurb)
    notes = []
    mail = mail_to(app_text or "")
    if mail:
        only = " (standard mail only)" if re.search(r"standard mail only", app_text or "", re.I) else ""
        notes.append(f"Mail paper application{only} to {mail}")
    if card["app"]:
        notes.append(f"Application: {card['app']}")
    for e in card["emails"]:
        notes.append(f"Email {e}")
    notes.append("Phone 212-663-3000 x1402")
    slug = re.sub(r"[^a-z0-9]+", "-", (addr or card["flyer"] or blurb).lower()).strip("-")[:60]
    return Listing(
        source="the_bridge",
        ext_id=slug,
        title=title,
        url=card["flyer"] or URL,
        address=addr,
        borough=parse.borough(addr, blurb),
        status="open",
        kind="re-rental",
        units=f["units"],
        senior_only=f["senior"],
        notes="; ".join(notes),
    )


class TheBridge(PageAdapter):
    name = "the_bridge"
    title = "The Bridge (re-rentals)"
    url = URL
    pages = [URL]
    every_hours = 4
    min_listings = 1

    def fetch(self) -> Result:
        snaps = super().fetch().snapshots
        out, seen = [], set()
        for card in parse_cards(fetch.html(URL)):
            ft = at = None
            if card["flyer"]:
                seen.add(card["flyer"])
                ft = _pdf_text(self.cache, card["flyer"])
            if card["app"]:
                seen.add(card["app"])
                at = _pdf_text(self.cache, card["app"])
            out.append(build_listing(card, ft or "", at))
        prune_pdf_cache(self.cache, seen)
        return Result(listings=out, snapshots=snaps)
