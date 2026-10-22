"""ACJ International Realty: a few Next.js (server-rendered) re-rental pages.

The index (/affordable-re-rentals) shows "N Listing(s) available" and one card per listing linking to
/affordable-re-rentals/<slug>. Each detail page has a <dl> (Borough / Unit / Unit type / Rent) and an
"Income requirements (130% AMI)" table: Unit Type | Monthly Rent | Household Size | Annual Household
Income, where the first row of each unit type carries size and rent (rowspan) and the following rows
carry only household size and income. Rented units are simply removed from the index.

Both pages send an ETag, so a run is one 304 when nothing changed (cache: validators + parsed listings).
Applying: a paper form (/files/listings/<slug>/applicationForm.pdf) by email or mail, kept in notes.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from urllib.parse import unquote, urljoin

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

BASE = "https://www.acjinternationalrealty.com"
INDEX = f"{BASE}/affordable-re-rentals"
_DETAIL = re.compile(r"^/affordable-re-rentals/([a-z0-9-]+)/?$")


def parse_index(html: str) -> list[str]:
    """Detail URLs on the index, checked against its "N Listings available" header."""
    s = parse.soup(html)
    head = next(
        (h.get_text(" ", strip=True) for h in s.find_all(["h1", "h2", "h3"]) if "available" in h.get_text().lower()),
        None,
    )
    m = re.search(r"(\d+|no)\s+listings?\s+available", head or "", re.I)
    if not m:
        raise RuntimeError(f"{INDEX}: no 'N Listings available' header (layout changed?)")
    urls = list(dict.fromkeys(urljoin(BASE, a["href"]) for a in s.find_all("a", href=True) if _DETAIL.match(a["href"])))
    want = 0 if m.group(1).lower() == "no" else int(m.group(1))
    if len(urls) != want:
        raise RuntimeError(f"{INDEX}: header says {want} listings, found {len(urls)} links")
    return urls


def _dl(s) -> dict[str, str]:
    out = {}
    for dt in s.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd is not None:
            out[dt.get_text(" ", strip=True).lower()] = dd.get_text(" ", strip=True)
    return out


def parse_units(s, ami: int | None) -> list[Unit]:
    units: list[Unit] = []
    for cells in parse.table_rows(s):
        if len(cells) >= 4 and parse.size(cells[0]) != "unknown":
            units.append(
                Unit(
                    size=parse.size(cells[0]),
                    rent=parse.money(cells[1]),
                    ami=ami,
                    bands=parse.bands_from_row(cells[2], cells[3], ami),
                )
            )
        elif len(cells) == 2 and units and parse.household_range(cells[0]):
            units[-1].bands.extend(parse.bands_from_row(cells[0], cells[1], ami))
    return units


def parse_detail(url: str, html: str) -> Listing:
    s = parse.soup(html)
    h1 = s.find("h1")
    if h1 is None:
        raise RuntimeError(f"{url}: no <h1> (layout changed?)")
    title = h1.get_text(" ", strip=True)
    sub = h1.find_next_sibling("p")
    address = sub.get_text(" ", strip=True) if sub else title
    info = _dl(s)
    hdr = next(
        (h.get_text("", strip=True) for h in s.find_all(["h2", "h3"]) if "income requirement" in h.get_text().lower()),
        "",
    )
    ami = parse.ami(hdr)
    units = parse_units(s, ami)
    if not units and info.get("unit type"):
        units = [Unit(size=parse.size(info["unit type"]), rent=parse.money(info.get("rent")), ami=ami)]
    if len(units) == 1 and info.get("unit"):
        units[0].unit_no = info["unit"].upper()
    flat = parse.text(s)
    pdf = next(
        (urljoin(BASE, a["href"]) for a in s.find_all("a", href=True) if a["href"].lower().endswith(".pdf")), None
    )
    mail = next((a["href"] for a in s.find_all("a", href=True) if "subject=" in a["href"].lower()), None)
    notes = []
    if pdf:
        notes.append(f"Apply: signed paper application {pdf}")
    if mail:
        to, _, q = mail.removeprefix("mailto:").partition("?")
        subject = unquote(q.split("subject=", 1)[1].split("&")[0]) if "subject=" in q else ""
        notes.append(f'email it to {to} with subject "{subject}"')
    m = re.search(r"By mail\n(.+?)\nBy email", flat, re.S)
    if m:
        notes.append("or mail to " + " ".join(m.group(1).split()))
    notes.append("first come, first served; one application per listing")
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return Listing(
        source="acj",
        ext_id=slug,
        title=f"{title}, Unit {units[0].unit_no}" if len(units) == 1 and units[0].unit_no else title,
        url=url,
        address=address,
        borough=parse.borough(address) or parse.borough(info.get("borough")),
        status="open",
        kind="re-rental",
        units=units,
        senior_only=bool(re.search(r"\bsenior (housing|building|residence)|62 (years|and older)", flat, re.I)),
        notes="; ".join(notes),
    )


def _from_dict(d: dict) -> Listing:
    units = [Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]}) for u in d["units"]]
    return Listing(**{**d, "units": units})


class ACJ(Adapter):
    name = "acj"
    title = "ACJ International Realty"
    url = INDEX
    min_listings = 0  # the index is often empty between re-rentals (1 listing on 2026-10-19)

    def fetch(self) -> Result:
        idx = self.cache.setdefault("index", {})
        pages: dict = self.cache.setdefault("pages", {})
        body, changed = fetch.conditional(INDEX, idx)
        if not changed and "urls" in idx:
            return Result(listings=[_from_dict(pages[u]["listing"]) for u in idx["urls"]])
        urls = parse_index(body.decode("utf-8", "replace"))
        out = []
        for u in urls:
            hit = pages.setdefault(u, {})
            raw, fresh = fetch.conditional(u, hit)
            if not fresh and "listing" in hit:
                out.append(_from_dict(hit["listing"]))
                continue
            lst = parse_detail(u, raw.decode("utf-8", "replace"))
            hit["listing"] = asdict(lst)
            out.append(lst)
        idx["urls"] = urls
        for u in [u for u in pages if u not in urls]:
            del pages[u]
        return Result(listings=out)
