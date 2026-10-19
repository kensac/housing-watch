"""SANEL (sanel.nyc): an aggregator of ~27 NYC sources, used here as the safety net.

Every listing page is in the sitemap as /affordable-housing/<borough>/<slug>. The sitemap has no
lastmod, so pages are cached by url and re-read on a rolling schedule: new urls at once, then the
stalest pages, so each page is refreshed at least every MAX_AGE_H. A page carries JSON-LD
RealEstateListing (name, official source in `isBasedOn`, address), a facts block (Rent, Bedrooms, AMI,
Household, Deadline) and, when the source publishes one, a tier table with one row per household size.
Applying is never through SANEL: notes point at the official source.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict
from datetime import datetime

from .. import fetch, parse
from ..fetch import FetchError
from ..models import Band, Listing, Result, Unit
from . import Adapter

log = logging.getLogger("housing_watch")
SITEMAP = "https://sanel.nyc/sitemap.xml"
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
GAP_S = 1.0  # SANEL asks nothing, but it is a small site: 1 request per second
MAX_AGE_H = 48  # every cached page is re-read at least this often
REFRESH_SHARE = 8  # each run re-reads the stalest 1/8 of known pages (every_hours=6 -> all within 48h)
MAX_FAILED = 20  # more fetch failures than this in one run = something is wrong with the site
FACT_LABELS = ("Rent", "Bedrooms", "AMI", "Household", "Income", "Available units", "Deadline", "Source")
SENIOR_RE = re.compile(r"\bseniors?\b|62\s*(\+|years|and older)|aged? 62|elderly", re.I)
SENIOR_PREF_RE = re.compile(r"senior\s+prefere?n?ce?\s*\d*\+?", re.I)  # a preference, not a restriction
SET_ASIDE_RE = re.compile(r"\b(mobility|hearing|visual|h/v|senior|62\+|mi)\b", re.I)


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


def _limits_ami(hh_text: str, income_text: str, a: int | None) -> int | None:
    """AMI % to derive per-size caps from. A multi-size row whose top cap is not this year's cap at the
    printed AMI (SVEN: '130%, 1-2 people, up to $168,480' = 2025 limits) gets None, so the % is inferred
    from that cap instead and the 1-person cap lands on the older limit ($147,420, not $154,440)."""
    sizes = parse.household_range(hh_text)
    hi = parse.money_range(income_text)[1]
    if len(sizes) < 2 or not hi or not a:
        return a
    cap = parse.ami_max(a, sizes[-1])
    return a if cap and abs(cap - hi) <= cap * 0.01 else None


def _tiers(s) -> list[Unit]:
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
        for b in parse.bands_from_row(cells[3], cells[4], _limits_ami(cells[3], cells[4], a)):
            if all(x.household != b.household for x in u.bands):
                u.bands.append(b)
    for u in units.values():
        u.bands.sort(key=lambda b: b.household)
    return list(units.values())


def _from_facts(f: dict[str, str]) -> list[Unit]:
    """No tier table: one Unit per published size, with what the facts block allows."""
    sizes = [parse.size(x) for x in f.get("Bedrooms", "").split("·")]
    sizes = [x for x in dict.fromkeys(sizes) if x != "unknown"] or ["unknown"]
    amis = [int(x) for x in re.findall(r"(\d{2,3})\s*%", f.get("AMI", ""))]
    rents = [parse.money(x) for x in re.findall(r"\$[\d,]+(?:\.\d+)?", f.get("Rent", ""))]
    hh = parse.household_range(f.get("Household", ""))
    # the facts block spans every tier: it can only prove a household minimum, never a 1-person cap
    bands = [Band(h, None, None) for h in hh] if hh and min(hh) > 1 else []
    return [
        Unit(
            size=z,
            rent=rents[0] if len(rents) == 1 and len(sizes) == 1 else None,
            ami=max(amis) if amis else None,
            bands=list(bands),
        )
        for z in sizes
    ]


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
    units = _tiers(s) or _from_facts(f)
    src = f.get("Source")
    official = ld.get("isBasedOn")
    notes = f"Apply via the official source ({src or 'unknown'}): {official or url}"
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
    )


def _get(url: str) -> str | None:
    """Page html, or None when the page is a 404 (no retries for those: they are permanent)."""
    try:
        return fetch.get(url, retries=0).text
    except FetchError as e:
        if "404 Not Found" in str(e):
            return None
    return fetch.get(url).text


def _from_dict(d: dict) -> Listing:
    units = [Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]}) for u in d["units"]]
    return Listing(**{**d, "units": units})


class Sanel(Adapter):
    name = "sanel"
    title = "SANEL (aggregator)"
    url = "https://sanel.nyc/listings"
    every_hours = 6
    min_listings = 150

    def fetch(self) -> Result:
        xml = fetch.html(SITEMAP)
        urls = [u for u in dict.fromkeys(re.findall(r"<loc>([^<]+)</loc>", xml)) if PAGE_RE.match(u)]
        if len(urls) < self.min_listings:
            raise RuntimeError(f"sitemap lists only {len(urls)} listing pages")
        cache: dict = self.cache.setdefault("pages", {})
        now = time.time()
        # new pages first, then anything older than MAX_AGE_H, then the stalest share (spreads the load)
        missing = [u for u in urls if u not in cache]
        cached = sorted((u for u in urls if u in cache), key=lambda u: cache[u]["fetched"])
        stale = [u for u in cached if now - cache[u]["fetched"] > MAX_AGE_H * 3600]
        budget = max(len(urls) // REFRESH_SHARE, len(stale))
        todo = set(missing) | set(cached[:budget])
        last = 0.0
        out, failed = [], []
        for url in urls:
            hit = cache.get(url)
            if url not in todo:
                if hit.get("listing"):
                    out.append(_from_dict(hit["listing"]))
                continue
            time.sleep(max(0.0, last + GAP_S - time.monotonic()))
            last = time.monotonic()
            try:
                body = _get(url)
                lst = parse_page(url, body) if body is not None else None
            except (FetchError, ValueError) as e:
                log.warning("sanel: %s", e)
                failed.append(url)
                if hit and hit.get("listing"):  # keep the last good copy; a new page waits for the next run
                    out.append(_from_dict(hit["listing"]))
                continue
            if lst is None:  # the sitemap lists some pages that 404
                cache[url] = {"fetched": now, "listing": None}
                continue
            cache[url] = {"fetched": now, "listing": asdict(lst)}
            out.append(lst)
        if len(failed) > MAX_FAILED:
            raise RuntimeError(f"{len(failed)} SANEL pages failed to fetch, e.g. {failed[:3]}")
        live = set(urls)
        for u in [u for u in cache if u not in live]:
            del cache[u]
        return Result(listings=out)
