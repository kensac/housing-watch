"""Pronto Housing: open re-rental units and general building waitlists, from Pronto's public JSON API.

The search endpoint lists ~3,200 properties nationwide (mostly PadSplit); only `source == "pronto"` in NYC
counts. Each building's detail gives named units (rent, AMI, availability) and, for waitlists, income
bands per household size with no rent or size ("unit information subject to change"). A tier's size is
taken from a named unit with the same minimum income, else from a household range only one HPD unit type
allows (1-3 = 1BR, 2-5 = 2BR, 3-7 = 3BR; 1-2 fits a studio or a 1BR, so it stays "unknown"). Some
waitlists carry placeholder bands ("$100-$150"), dropped; some publish no tiers at all (said in notes).
Details are re-read only when the building's search record changes, or once a day.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

SEARCH = "https://prontohousing.io/api/listings/search/?searchType=property"
DETAIL = "https://prontohousing.io/api/listings/properties/{}/"
PAGE = "https://prontohousingrentals.com/listings/property/{}"
MAX_AGE = 24 * 3600
_VOLATILE = {"image", "images", "imageUrl", "imageUrls", "createdAt"}  # signed S3 urls change every day


def _pct(v) -> int | None:
    """0.6 / 1.3 (fraction) or 60 / 130 (percent) -> 60 / 130."""
    if v in (None, ""):
        return None
    v = float(v)
    return int(round(v * 100)) if v < 5 else int(round(v))


def _bands(ranges: list[dict]) -> list[Band]:
    out = {}
    for r in ranges:
        hh = int(r.get("household_size") or r.get("householdSize") or 0)
        lo = parse.whole(r.get("min_income", r.get("minIncome")))
        hi = parse.whole(r.get("max_income", r.get("maxIncome")))
        if hh and hh not in out:
            out[hh] = Band(hh, lo, hi)
    return [out[h] for h in sorted(out)]


def _real(r: dict) -> bool:
    hi = r.get("max_income", r.get("maxIncome")) or 0
    return float(hi) >= 10_000  # Aura / 8 Court Square carry "$100-$150" placeholder rows


OCCUPANCY = {(1, 3): "1br", (2, 5): "2br", (3, 7): "3br"}  # HPD household ranges unique to one unit type


def _waitlist_tiers(ranges: list[dict], named: list[Unit] = ()) -> list[Unit]:
    """Waitlist bands -> one Unit per (AMI, min income) tier, sized by a named unit or the household range."""
    tiers: dict[tuple, list[dict]] = {}
    for r in ranges:
        if _real(r):
            tiers.setdefault((_pct(r.get("ami_percentage")), parse.whole(r.get("min_income"))), []).append(r)
    by_min = {(u.ami, b.income_min): u.size for u in named for b in u.bands if b.income_min}
    out = []
    for (a, lo), rs in tiers.items():
        bands = _bands(rs)
        hh = (bands[0].household, bands[-1].household) if bands else None
        out.append(Unit(size=by_min.get((a, lo)) or OCCUPANCY.get(hh, "unknown"), ami=a, bands=bands))
    return out


def parse_property(detail: dict) -> Listing | None:
    """One `/properties/<id>/` response -> Listing, or None when the building is outside NYC."""
    p = detail["property"]
    addr = p.get("address") or ""
    boro = parse.borough(addr)
    if boro is None:  # Westchester (Yonkers, New Rochelle, Tarrytown) is also state NY
        return None
    offerings = p.get("available_offerings") or []
    units: list[Unit] = []
    seen: set[str] = set()
    for u in detail.get("units") or []:
        size = parse.size(str(u.get("bedrooms")))
        a = _pct(u.get("ami_percentage"))
        rent = parse.money(u.get("rent"))
        off = next(
            (
                o
                for o in offerings
                if o.get("bedrooms") == u.get("bedrooms")
                and _pct(o.get("ami_pct")) == a
                and (rent is None or (o.get("rent_min") or 0) <= rent <= (o.get("rent_max") or rent))
            ),
            None,
        )
        if off and off.get("income_ranges"):
            bands = _bands(off["income_ranges"])
        else:
            lo_hh, hi_hh = u.get("min_household_size"), u.get("max_household_size")
            hh = f"{lo_hh}-{hi_hh}" if lo_hh and hi_hh and hi_hh != lo_hh else str(lo_hh or "")
            bands = parse.bands_from_row(hh, f"${u.get('min_income') or 0:,.0f} - ${u.get('max_income') or 0:,.0f}", a)
        unit = Unit(
            size=size,
            rent=rent,
            ami=a,
            unit_no=(u.get("name") or "").strip() or None,
            bands=bands,
            set_aside="disability" if u.get("accessibility_features") else None,
            available=bool(u.get("is_available", True)),
        )
        if unit.key() not in seen:  # Eastchester lists the same unit number twice in one tier
            seen.add(unit.key())
            units.append(unit)
    if not units:  # offerings without named units: one Unit per tier
        for o in offerings:
            units.append(
                Unit(
                    size=parse.size(str(o.get("bedrooms"))),
                    rent=parse.money(o.get("rent_min")),
                    ami=_pct(o.get("ami_pct")),
                    bands=_bands(o.get("income_ranges") or []),
                )
            )
    waitlist = bool(p.get("is_waitlist") or p.get("waitlist_only"))
    if waitlist:
        units += _waitlist_tiers(p.get("property_income_ranges") or [], units)
    open_units = int(p.get("available_units") or 0) > 0
    status = "open" if open_units else ("waitlist" if waitlist else "closed")
    link = p.get("application_link") or p.get("waitlist_link") or ""
    how = []
    if link:
        how.append(f"Apply online ({'join waitlist' if status == 'waitlist' else 'apply'}): {link} (Pronto account)")
    if p.get("paper_application_url"):
        how.append(f"paper application: {p['paper_application_url']}")
    if p.get("contact_email"):
        how.append(f"questions: {p['contact_email']}")
    if not units:
        how.append("Pronto publishes no unit sizes, rents or income limits for this building")
    blob = f"{p.get('name')} {p.get('description') or ''} {p.get('disclaimer') or ''}"
    return Listing(
        source="pronto",
        ext_id=str(p["id"]),
        title=p.get("name") or addr,
        url=PAGE.format(p["id"]),
        address=addr,
        borough=boro,
        status=status,
        kind="waitlist" if p.get("waitlist_only") else "re-rental",
        units=units,
        deadline=(p.get("application_deadline") or None),
        senior_only=bool(re.search(r"\bsenior|62\s*(\+|years|and older)", blob, re.I)),
        notes="; ".join(how) or None,
    )


def _from_dict(d: dict) -> Listing:
    units = [Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]}) for u in d["units"]]
    return Listing(**{**d, "units": units})


def _fingerprint(rec: dict) -> str:
    stable = {k: v for k, v in rec.items() if k not in _VOLATILE}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()


def ny_records(search: dict) -> list[dict]:
    return [x for x in search.get("listings") or [] if x.get("source") == "pronto" and x.get("state") == "NY"]


class Pronto(Adapter):
    name = "pronto"
    title = "Pronto Housing"
    url = "https://prontohousingrentals.com/listings"
    every_hours = 6
    min_listings = 10  # ~24 NYC buildings on 2026-10-19

    def fetch(self) -> Result:
        recs = ny_records(fetch.json(SEARCH))
        if len(recs) < self.min_listings:
            raise RuntimeError(f"search lists only {len(recs)} Pronto NY properties")
        cache: dict = self.cache.setdefault("props", {})
        now = time.time()
        out = []
        for rec in recs:
            pid = str(rec["id"])
            fp = _fingerprint(rec)
            hit = cache.get(pid)
            if hit and hit.get("fp") == fp and now - hit.get("at", 0) < MAX_AGE:
                if hit["listing"]:
                    out.append(_from_dict(hit["listing"]))
                continue
            lst = parse_property(fetch.json(DETAIL.format(pid)))
            cache[pid] = {"fp": fp, "at": now, "listing": asdict(lst) if lst else None}
            if lst:
                out.append(lst)
        live = {str(r["id"]) for r in recs}
        for k in [k for k in cache if k not in live]:
            del cache[k]
        return Result(listings=out)
