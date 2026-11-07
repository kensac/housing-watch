"""A SANEL listing page -> Listing: its JSON-LD, facts block, tier table and links to the official source."""

from __future__ import annotations

import json
import re
from datetime import datetime

from ... import ami, parse
from ...models import Band, Listing, Unit

PAGE_RE = re.compile(r"^https://sanel\.nyc/affordable-housing/([a-z-]+)/([^/]+)$")
URL_BOROUGH = {
    "manhattan": "Manhattan",
    "brooklyn": "Brooklyn",
    "queens": "Queens",
    "bronx": "Bronx",
    "staten-island": "Staten Island",
}
KINDS = {
    "lottery": "lottery",
    "re-rental": "re-rental",
    "rerental": "re-rental",
    "waitlist": "waitlist",
    "mitchell-lama": "waitlist",
    "homeownership": "homeownership",
    "sale": "homeownership",
}
FACT_LABELS = ("Rent", "Bedrooms", "AMI", "Household", "Income", "Available units", "Deadline", "Source")
SENIOR_RE = re.compile(r"\bseniors?\b|62\s*(\+|years|and older)|aged? 62|elderly", re.I)
SENIOR_PREF_RE = re.compile(r"senior\s+prefere?n?ce?\s*\d*\+?", re.I)  # a preference, not a restriction
SET_ASIDE_RE = re.compile(r"\b(mobility|hearing|visual|h/v|senior|62\+|mi)\b", re.I)
OLD_CHART = " Income limits are printed on HUD's {} chart ({} caps are higher; confirm which applies)."


def _ld(s) -> dict:
    for tag in s.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(tag.string or "")
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "RealEstateListing":
            return d
    return {}


def _facts(lines: list[str]) -> dict[str, str]:
    """Label/value pairs in the header block (everything before 'About this opportunity')."""
    out: dict[str, str] = {}
    for i, ln in enumerate(lines[:-1]):
        if ln.startswith("About this opportunity"):
            break
        if ln in FACT_LABELS and ln not in out:
            out[ln] = lines[i + 1]
    return out


def _date(text: str | None) -> str | None:
    """'Nov 10, 2026' / 'November 10, 2026' -> '2026-11-10'; 'Open until filled' -> None."""
    m = re.search(r"\b([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}), (\d{4})", text or "")
    if not m:
        return None
    try:
        return datetime.strptime(" ".join(m.groups()), "%b %d %Y").date().isoformat()
    except ValueError:
        return None


def old_chart_note(units: list[Unit]) -> str:
    old = sorted({u.chart_year for u in units if u.chart_year and u.chart_year < ami.CURRENT_YEAR})
    return OLD_CHART.format("/".join(map(str, old)), ami.CURRENT_YEAR) if old else ""


def tier_units(s) -> list[Unit]:
    """Tier table rows (size, rent, AMI, household, income [, notes]) grouped into one Unit per tier."""
    units: dict[tuple, Unit] = {}
    for cells in parse.table_rows(s):
        if len(cells) < 5 or "/mo" not in cells[1] and not re.search(r"\$|not published|—|-", cells[1]):
            continue
        if cells[0].lower() == "unit":
            continue
        size = parse.size(cells[0])
        rent = parse.money(cells[1]) if "$" in cells[1] else None
        a = parse.ami(cells[2])
        extra = " ".join([cells[0], *cells[5:]])
        set_aside = None
        if m := SET_ASIDE_RE.search(extra):
            set_aside = "mobility" if m.group(1).lower() == "mi" else m.group(1).lower()
        k = (size, rent, a, set_aside)
        u = units.get(k) or units.setdefault(k, Unit(size=size, rent=rent, ami=a, set_aside=set_aside))
        hh, (lo, hi) = parse.household_range(cells[3]), parse.money_range(cells[4])
        bands, year = ami.bands(hh, lo, hi, a) if hh else ([], None)
        u.chart_year = u.chart_year or year
        for b in bands:
            if all(x.household != b.household for x in u.bands):
                u.bands.append(b)
    for u in units.values():
        u.bands.sort(key=lambda b: b.household)
    return list(units.values())


def _from_facts(f: dict[str, str]) -> list[Unit]:
    """No tier table: one Unit per published size, with what the facts block allows.

    Income there runs from the lowest minimum to the highest cap over every tier. One size, rent and AMI
    make it a single tier, read exactly; otherwise the caps still bound every tier from above (each
    household's cap at the top AMI) but the minimum may be a cheaper tier's, so it is dropped."""
    sizes = [parse.size(x) for x in f.get("Bedrooms", "").split("·")]
    sizes = [x for x in dict.fromkeys(sizes) if x != "unknown"] or ["unknown"]
    amis = [int(x) for x in re.findall(r"(\d{2,3})\s*%", f.get("AMI", ""))]
    rents = [parse.money(x) for x in re.findall(r"\$[\d,]+(?:\.\d+)?", f.get("Rent", ""))]
    hh = parse.household_range(f.get("Household", ""))
    lo, hi = parse.money_range(f.get("Income", ""))
    single = len(sizes) == 1 and len(rents) <= 1 and len(amis) <= 1
    year = None
    if hi and hi > (lo or 0):
        bands, year = ami.bands(hh, lo if single else None, hi, max(amis) if amis else None)
    else:  # no income: the household range can only prove a minimum, never a 1-person cap
        bands = [Band(h, None, None) for h in hh] if hh and min(hh) > 1 else []
    return [
        Unit(
            size=z,
            rent=rents[0] if len(rents) == 1 and len(sizes) == 1 else None,
            ami=max(amis) if amis else None,
            bands=list(bands),
            chart_year=year,
        )
        for z in sizes
    ]


def doc_links(html: str) -> list[str]:
    """External links in the listing body (official page, flyer PDF), before the related listings."""
    art = parse.soup(html).find("article")
    out = []
    for a in art.find_all("a", href=True) if art else []:
        h2 = (a.find_parent("section") or a).find("h2")
        if h2 and "More nearby" in h2.get_text():
            break
        if a["href"].startswith("http") and "nyc.gov/site/buildings" not in a["href"]:
            out.append(a["href"])
    return list(dict.fromkeys(out))


def parse_page(url: str, html: str) -> Listing:
    s = parse.soup(html)
    ld = _ld(s)
    art = s.find("article")
    if art is None or not ld:
        raise ValueError(f"{url}: no listing article/JSON-LD (page layout changed?)")
    lines = parse.text(str(art)).splitlines()
    head = lines[:3]
    f = _facts(lines)
    desc = ld.get("description", "")
    kind = KINDS.get((head[0] if head else "").lower().replace(" ", "-"), "re-rental")  # the type badge
    badge = " ".join(head[:2]).lower()
    if "closed" in badge or "filled" in badge or "expired" in badge:
        status = "closed"
    elif kind == "waitlist":
        status = "waitlist"
    else:
        status = "open"
    pm = PAGE_RE.match(url.rstrip("/"))
    addr = ((ld.get("about") or {}).get("address") or {}).get("streetAddress")
    borough = URL_BOROUGH.get(pm.group(1)) if pm else None
    borough = borough or parse.borough(addr)
    flat = " ".join(
        lines[: lines.index("About this opportunity") + 3] if "About this opportunity" in lines else lines[:60]
    )
    units = tier_units(s) or _from_facts(f)
    src = f.get("Source")
    official = ld.get("isBasedOn")
    notes = f"Apply via the official source ({src or 'unknown'}): {official or url}." + old_chart_note(units)
    if "upcoming" in badge:
        notes = "Upcoming. " + notes
    return Listing(
        source="sanel",
        ext_id=(pm.group(2) if pm else url.rstrip("/").split("/")[-1]),
        title=ld.get("name") or (lines[2] if len(lines) > 2 else url),
        url=url,
        address=addr,
        borough=borough,
        status=status,
        kind=kind,
        units=units,
        deadline=_date(f.get("Deadline")),
        senior_only=bool(SENIOR_RE.search(SENIOR_PREF_RE.sub(" ", desc + " " + flat))),
        notes=notes,
        official_url=official or None,
    )
