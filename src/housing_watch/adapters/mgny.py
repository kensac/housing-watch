"""MGNY Consulting: ~25 WordPress "listing" posts, all in one REST call (the ACF fields carry the unit table).

Two table shapes: per-household rows ("Studio | $3,543 | 2 | 1 | $126,412 | $154,440" followed by
"2 | $126,412 | $176,410" continuation rows) and one row per size spanning several households
("1 Bedroom | $3,358 | 1 | 1-3 people | $118,869 - $198,510", AMI in a "130% AMI Units" header row or an
AMI column). A spanning row's top cap may be on an older HUD chart (27-15 Astoria Blvd: 1-3 people up to
$189,540 = 2025, 1 person $147,420); ami.bands matches it to the right year.
MGNY drops posts once leased, so every listing is open; applying is the interest form on each page.
"""

from __future__ import annotations

import html as _html
import re

from .. import ami, fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

API = "https://mgnyconsulting.com/wp-json/wp/v2/listing?per_page=100&_fields=id,slug,link,modified,title,class_list,acf"
_MONEY = re.compile(r"\$\s*\d[\d,]*(?:\.\d+)?")
_SET_ASIDE = re.compile(r"\b(mobility|hearing|visual|h/v|senior|62\+|mi)\b", re.I)


def _rents(cell: str) -> list[float]:
    return [parse.money(m) for m in _MONEY.findall(cell)]


def _infer_ami(bands: list[Band]) -> int | None:
    """130 when the 1-person cap is $154,440 (or 2025's $147,420), etc.: only exact HUD caps count."""
    for b in bands:
        if b.income_max and (m := ami.match(b.income_max, b.household, sizes=[b.household])):
            return m[2]
    return None


def _split_bands(hh: str, lo: str, hi: str, a: int | None) -> list[Band]:
    """Separate min/max columns; a household cell is normally one size but may be a range."""
    return parse.bands_from_row(hh, f"{parse.whole(lo) or ''} - {parse.whole(hi) or ''}", a)


def parse_table(desc: str) -> list[Unit]:
    units: list[Unit] = []
    ami_hdr: int | None = None
    cols: dict[str, int] | None = None
    cur: Unit | None = None
    pending_rents: list[float] = []
    for cells in parse.table_rows(desc):
        low = [c.lower() for c in cells]
        if len(cells) == 1:
            if parse.ami(cells[0]) and "ami" in low[0]:
                ami_hdr = parse.ami(cells[0])
            continue
        if any("household" in c for c in low) and not any("$" in c for c in cells):
            cols = {}
            for i, c in enumerate(x.replace(" ", "") for x in low):  # "Annual Household Incom e"
                if "income" in c:
                    cols["min" if c.startswith("min") else "max" if c.startswith("max") else "income"] = i
                elif "household" in c:
                    cols["hh"] = i
                elif "rent" in c:
                    cols["rent"] = i
                elif c == "ami":
                    cols["ami"] = i
            cols["n"] = len(cells)
            continue
        if not cols:
            continue
        split = "min" in cols and "max" in cols
        if len(cells) == cols["n"] and parse.size(cells[0]) != "unknown":
            a = parse.ami(cells[cols["ami"]]) if "ami" in cols else ami_hdr
            rents = _rents(cells[cols["rent"]]) if "rent" in cols else []
            pending_rents = []
            if split:
                bands = _split_bands(cells[cols["hh"]], cells[cols["min"]], cells[cols["max"]], a)
                if len(rents) > 1 and parse.household_range(cells[cols["hh"]]):
                    pending_rents = rents[1:]  # one row per unit (Jasper: "$3,300.00 $3,500.00")
            else:
                bands = parse.bands_from_row(cells[cols["hh"]], cells[cols["income"]], a)
            cur = Unit(
                size=parse.size(cells[0]),
                rent=(rents[0] if pending_rents else min(rents)) if rents else None,
                ami=a,
                bands=bands,
                set_aside="disability/senior" if _SET_ASIDE.search(" ".join(cells)) else None,
            )
            units.append(cur)
        elif split and cur and len(cells) == 3 and parse.household_range(cells[0]):
            new = _split_bands(cells[0], cells[1], cells[2], cur.ami)
            if any(x.household == b.household for x in cur.bands for b in new):
                # same household size again: the next unit of a multi-rent row
                rent = pending_rents.pop(0) if pending_rents else cur.rent
                cur = Unit(size=cur.size, rent=rent, ami=cur.ami, bands=new, set_aside=cur.set_aside)
                units.append(cur)
            else:
                cur.bands.extend(new)
    for u in units:
        if u.ami is None:
            u.ami = _infer_ami(u.bands)
    ami.stamp(units)
    return units


def parse_record(rec: dict) -> Listing:
    acf = rec.get("acf") or {}
    desc = acf.get("property_description") or ""
    title = _html.unescape(rec["title"]["rendered"]).strip()
    addr = (acf.get("property_address") or "").strip() or None
    tax = " ".join(c.removeprefix("borough-").replace("-", " ") for c in rec.get("class_list", []) if "borough-" in c)
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+\w", desc)), key=str.lower)
    notes = f"Apply: interest form at {rec['link']} (first come, agent follows up)"
    if emails:
        notes += "; questions: " + ", ".join(emails)
    flat = parse.text(desc) if desc else ""
    return Listing(
        source="mgny",
        ext_id=rec["slug"],
        title=title,
        url=rec["link"],
        address=addr,
        borough=parse.borough(addr) or parse.borough(tax),
        status="open",
        kind="re-rental",
        units=parse_table(desc),
        senior_only=bool(re.search(r"\bsenior (housing|building|residence)|62 (years|and older)", flat, re.I)),
        notes=notes,
    )


class MGNY(Adapter):
    name = "mgny"
    title = "MGNY Consulting"
    url = "https://mgnyconsulting.com/listings/"
    every_hours = 4  # first come, first served
    min_listings = 8

    def fetch(self) -> Result:
        data = fetch.json(API)
        if not isinstance(data, list):
            raise RuntimeError(f"unexpected MGNY API response: {str(data)[:200]}")
        return Result(listings=[parse_record(r) for r in data])
