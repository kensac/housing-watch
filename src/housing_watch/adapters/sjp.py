"""SJP Tax Consultants: Squarespace blog; each available re-rental is a post under /apartment-listings/.

The collection's `?format=json` lists every post (title, body HTML, tags). A placeholder post ("There are
currently no affordable re-rental apartments available") is styled like a listing and is skipped. A post
marked "Rented", or whose Apply button is disabled/missing its link, is closed. No general waitlist.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from ._page import PageAdapter

BASE = "https://www.sjpny.com"
INDEX = f"{BASE}/affordable-rerentals"
COLLECTION = f"{BASE}/apartment-listings?format=json"
OFFICE = "SJP Tax Consultants, 149-41 14th Ave, Whitestone, NY 11357"
PLACEHOLDER = re.compile(r"no (affordable )?re-?rental apartments? (are )?(currently )?available", re.I)
RENTED = re.compile(r"\b(rented|leased|no longer available|applications? (are )?closed)\b", re.I)
SET_ASIDE = [
    (r"\bmobility\b", "mobility"),
    (r"\bhearing\b|\bvisual\b|\bh/v\b", "hearing/visual"),
    (r"\bsenior\b|\b62\s*\+|62 (years|and older)", "senior"),
    (r"\bmi\b|mobility[- ]impaired", "mobility"),
]


def _set_aside(t: str) -> str | None:
    return next((name for pat, name in SET_ASIDE if re.search(pat, t, re.I)), None)


def _bands_from_text(t: str, ami_pct: int | None) -> list[Band]:
    out: list[Band] = []
    for m in re.finditer(
        r"(\d\s*(?:-|–|to)?\s*\d?)\s*(?:persons?|people|ppl)\b[^$\n]{0,40}(\$[\d,]+(?:\.\d+)?\s*(?:-|–|to)\s*\$[\d,]+)",
        t,
        re.I,
    ):
        out += parse.bands_from_row(m.group(1), m.group(2), ami_pct)
    return out


def _table_units(s, set_aside_all: str | None) -> list[Unit]:
    units: list[Unit] = []
    cur: Unit | None = None
    for cells in parse.table_rows(s):
        joined = " ".join(cells)
        sz = parse.size(cells[0])
        hh_i = next((k for k, c in enumerate(cells) if re.search(r"person|people|^\d(\s*-\s*\d)?$", c, re.I)), None)
        if sz != "unknown":
            rent = next((parse.money(c) for c in cells[1:] if "$" in c and not re.search(r"\d\s*[-–]\s*\$", c)), None)
            cur = Unit(size=sz, rent=rent, ami=parse.ami(joined), set_aside=_set_aside(joined) or set_aside_all)
            units.append(cur)
        if cur is not None and hh_i is not None and re.search(r"\$[\d,]+.*\$[\d,]+", cells[-1]):
            cur.bands += parse.bands_from_row(cells[hh_i], cells[-1], cur.ami)
    return units


def parse_item(item: dict) -> Listing | None:
    """One collection post -> Listing; None for the 'nothing available' placeholder."""
    title = re.sub(r"\s+", " ", item.get("title") or "").strip()
    s = parse.soup(item.get("body") or "")
    body = parse.text(s)
    if PLACEHOLDER.search(title) or (PLACEHOLDER.search(body) and not re.search(r"\$\d", body)):
        return None
    url = urljoin(BASE, item.get("fullUrl") or "")
    tags = " ".join((item.get("tags") or []) + (item.get("categories") or []))
    apply = [a for a in s.find_all("a") if re.search(r"\bapply\b", a.get_text(" "), re.I)]
    apply_href = next((a.get("href") for a in apply if a.get("href") and a.get("href") not in ("#", "")), None)
    disabled = bool(apply) and (
        apply_href is None or any("disabled" in " ".join(a.get("class") or []) + str(a.attrs) for a in apply)
    )
    # "Rented" badge: title/tags or a short line of its own (not boilerplate sentences that mention it)
    badge = any(len(ln) < 40 and RENTED.search(ln) for ln in body.splitlines())
    closed = bool(RENTED.search(f"{title} {tags}") or badge or disabled)
    flat = f"{title}\n{body}"
    all_aside = _set_aside(title)
    units = _table_units(s, all_aside)
    if not units:
        ami_pct = parse.ami(flat)
        rent_m = re.search(r"rent[^$\n]{0,30}\$\s*([\d,]+(?:\.\d+)?)", flat, re.I) or re.search(
            r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:/|per)\s*(?:month|mo)\b", flat, re.I
        )
        sz = parse.size(flat)
        if sz != "unknown" or rent_m or ami_pct:
            units = [
                Unit(
                    size=sz,
                    rent=parse.money(rent_m.group(1)) if rent_m else None,
                    ami=ami_pct,
                    bands=_bands_from_text(flat, ami_pct),
                    set_aside=_set_aside(flat),
                )
            ]
    unit_no = re.search(r"\b(?:unit|apt\.?|apartment)\s*#?\s*([0-9]{1,4}[A-Z]?|[A-Z][0-9]{0,3})\b", flat, re.I)
    if unit_no and len(units) == 1:
        units[0].unit_no = unit_no.group(1).upper()
    for u in units:
        if closed:
            u.available = False
    addr_m = re.search(
        r"\b\d{1,5}(?:-\d{1,3})?\s+[\w .]+?(?:Street|St|Avenue|Ave|Road|Rd|Place|Pl|Boulevard|Blvd)\b", flat
    )
    loc = item.get("location") or {}
    loc_addr = ", ".join(x for x in (loc.get("addressLine1"), loc.get("addressLine2")) if x)
    when = item.get("publishOn") or item.get("addedOn")
    posted = datetime.fromtimestamp(when / 1000, UTC).date().isoformat() if when else None
    notes = f"Apply: Apply Now button on the post {apply_href}" if apply_href and not closed else "Apply: via the post"
    notes += f" (first come, first served; no general waitlist; office {OFFICE})"
    if posted:
        notes += f"; posted {posted}"
    return Listing(
        source="sjp",
        ext_id=(item.get("urlId") or url.rstrip("/").split("/")[-1]),
        title=title,
        url=url,
        address=loc_addr or (addr_m.group(0) if addr_m else None),
        borough=parse.borough(loc_addr)
        or parse.borough(re.sub(r"149-41 14th Ave\w*,?\s*Whitestone,?\s*NY\s*11357", "", flat)),
        status="closed" if closed else "open",
        units=units,
        senior_only=bool(re.search(r"\bsenior\b|62 (years|and older)|\b62\s*\+", flat, re.I)),
        notes=notes,
    )


def parse_collection(data: dict) -> list[Listing]:
    if "items" not in data:
        raise RuntimeError("apartment-listings JSON has no items key")
    return [lst for it in data["items"] if (lst := parse_item(it))]


class Sjp(PageAdapter):
    name = "sjp"
    title = "SJP Tax Consultants"
    url = INDEX
    pages = [INDEX]
    link_re = re.compile(r"/apartment-listings/")
    every_hours = 8
    min_listings = 1

    def fetch(self) -> Result:
        snaps = super().fetch().snapshots
        return Result(listings=parse_collection(fetch.json(COLLECTION)), snapshots=snaps)
