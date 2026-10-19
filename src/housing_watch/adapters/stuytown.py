"""StuyTown Affordable Housing (Beam Living / HDC): individual StuyTown + Peter Cooper Village apartments.

The /apartments/ page is an Umbraco + React shell: its server-rendered props (`AHUnitsPage, {...}`) hold
only CMS text, and the browser loads the units from `units.stuytown.com/api/ah-units` (unitSearchBaseUrl
+ GET_AH_UNITS in common-js), which answers `{"count":0,"unitModels":[],"totalCount":0}` when nothing is
posted. Units go up between 7 and 10 AM ET and close at 7 PM or after 3 applications, so this runs on
every loop tick and returns one Listing per posted unit: a new unit is a "new" event, and an empty answer
marks the previous ones gone (hence no Snapshot here: with a snapshot, an empty listing set is not
recorded).

Eligibility is program-wide, from /eligibility/ (re-read at most daily, only while units are posted):
household sizes per bedroom count (1BR 1-3 people, 2BR 2-5, 3BR 3-7) and a max income per household
size (2026: 1 person $196,020 = 165% AMI). The FAQ sets the minimum: rent may not exceed 35% of income.
"""

from __future__ import annotations

import json
import re
import string
import time
from urllib.parse import quote

from .. import ami, fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

SITE = "https://affordable-housing.stuytown.com"
API = "https://units.stuytown.com/api/ah-units?page={page}&itemsOnPage=21"
ELIGIBILITY = f"{SITE}/eligibility/"
RENT_SHARE = 0.35  # FAQ: total rent must not exceed 35% of household income
_PROPS = re.compile(r"React\.createElement\(\s*\w+\s*,\s*")


def _props(html: str) -> dict:
    m = _PROPS.search(html)
    if not m:
        raise RuntimeError("no React props on page (layout changed?)")
    obj, _ = json.JSONDecoder().raw_decode(html[m.end() :])
    return obj


def _rich_texts(o) -> list[str]:
    if isinstance(o, dict):
        out = [o["richTextDesktop"]] if isinstance(o.get("richTextDesktop"), str) else []
        return out + [x for k, v in o.items() if k != "richTextDesktop" for x in _rich_texts(v)]
    if isinstance(o, list):
        return [x for v in o for x in _rich_texts(v)]
    return []


def parse_eligibility(html: str) -> dict:
    """{"households": {bedrooms: [min, max]}, "caps": {household: max income}} from /eligibility/.

    The desktop rich text is used: the mobile copy still prints 2024 limits.
    """
    for rt in _rich_texts(_props(html).get("mainContent")):
        households: dict[str, list[int]] = {}
        caps: dict[str, int] = {}
        for cells in parse.table_rows(rt):
            if len(cells) != 2:
                continue
            sz, hh = parse.size(cells[0]), parse.household_range(cells[1])
            if sz != "unknown" and hh and "$" not in cells[1]:
                beds = 0 if sz == "studio" else int(sz[0])
                households[str(beds)] = [hh[0], hh[-1]]
            elif re.fullmatch(r"\d", cells[0]) and "$" in cells[1]:
                caps[cells[0]] = parse.whole(cells[1])
        if households and caps:
            return {"households": households, "caps": caps}
    raise RuntimeError(f"{ELIGIBILITY}: no household/income tables (layout changed?)")


def _ami(caps: dict) -> int | None:
    """165 when the 1-person cap is $196,020 (exact HUD caps only)."""
    m = caps.get("1") and ami.match(caps["1"], 1, sizes=[1])
    return m[2] if m else None


def parse_unit(m: dict, elig: dict | None) -> Listing:
    spk = m["unitSpk"]
    beds = m.get("bedrooms")
    rent = m.get("price")
    size = parse.size(str(beds)) if beds is not None else "unknown"
    bands: list[Band] = []
    ami = None
    if elig:
        caps = elig["caps"]
        ami = _ami(caps)
        lo = int(round(rent * 12 / RENT_SHARE)) if rent else None
        hh = elig["households"].get(str(beds))
        if hh:
            bands = [Band(h, lo, caps.get(str(h))) for h in range(hh[0], hh[1] + 1)]
    b = m.get("building") or {}
    prop = (m.get("property") or {}).get("name") or "StuyTown"
    street = string.capwords(b.get("address") or "") or None
    name = m.get("name") or spk
    notes = [
        "Apply: 'Apply Now' on the unit page; applications close at 7 PM ET or after 3 (ranked #1-#3), "
        "then a detailed application within 24 h"
    ]
    if m.get("availableDate"):
        notes.append(f"available {m['availableDate'][:10]}")
    if m.get("incomeRequirement"):
        notes.append(f"site income requirement field: {m['incomeRequirement']}")
    if m.get("sqft"):
        notes.append(f"{m['sqft']} sq ft, {m.get('bathrooms')} bath")
    return Listing(
        source="stuytown",
        ext_id=spk,
        title=f"{prop}: {street} #{name}" if street else f"{prop} #{name}",
        url=f"{SITE}/apartments/units?unitSpk={quote(spk, safe='')}",
        address=f"{street}, New York, NY" if street else None,
        borough="Manhattan",
        status="open",
        kind="re-rental",
        units=[Unit(size=size, rent=float(rent) if rent else None, ami=ami, unit_no=name, bands=bands)],
        notes="; ".join(notes),
    )


def parse_api(pages: list[dict]) -> list[dict]:
    models: list[dict] = []
    for p in pages:
        if not isinstance(p, dict) or "unitModels" not in p or "totalCount" not in p:
            raise RuntimeError(f"unexpected ah-units response: {str(p)[:200]}")
        models += p["unitModels"] or []
    return models


class StuyTown(Adapter):
    name = "stuytown"
    title = "StuyTown Affordable Housing"
    url = f"{SITE}/apartments/"
    every_hours = 0.5  # every 15-minute tick: units post 7-10 AM ET and close after 3 applications
    min_listings = 0  # usually nothing is posted

    def fetch(self) -> Result:
        pages = [fetch.json(API.format(page=0))]
        while sum(len(p.get("unitModels") or []) for p in pages) < (pages[0].get("totalCount") or 0):
            if len(pages) >= 20:
                raise RuntimeError("ah-units pagination did not converge")
            pages.append(fetch.json(API.format(page=len(pages))))
        models = parse_api(pages)
        elig = None
        if models:
            hit = self.cache.get("eligibility")
            if not hit or time.time() - hit.get("at", 0) > 86400:
                hit = {"at": time.time(), **parse_eligibility(fetch.html(ELIGIBILITY))}
                self.cache["eligibility"] = hit
            elig = hit
        return Result(listings=[parse_unit(m, elig) for m in models])
