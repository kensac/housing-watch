"""Affordable for New York (afny.org): re-rentals from the webaryco "mini_lotteries" API.

The same API serves iAfford NY (iaffordny.com, see iafford.py); the Origin header picks the tenant, and
without Origin/Referer the detail call answers a fake 404 "Project not found". The detail's `ami_grid`
({ami%: [tier]}) names every unit with its rent and a per-household income table, so each named unit
becomes one Unit. Applying is paper by standard mail to `mail_address` (G-1 PDF in `project_file`), or
the link in `project_file_link`. `end_date` is often years stale while units are still offered, so only
a future one is reported as a deadline.
"""

from __future__ import annotations

import datetime as dt
import json as _json
import re
from dataclasses import asdict

from .. import ami, fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

API = "https://api.iafford.webaryco.com/v2/projects/mini_lotteries/"


def _bands(households: list[dict], pct: int | None) -> list[Band]:
    out = []
    for h in households:
        size, lo, hi = int(h["size"]), h.get("income_min"), h.get("income_max")
        if lo and hi and hi < lo and pct:
            hi = ami.cap(pct, size)  # typo'd cap (22 Chapel St prints $15,440 for $154,440)
        out.append(Band(size, int(lo) if lo is not None else None, int(hi) if hi is not None else None))
    return out


def parse_detail(source: str, site: str, rec: dict, today: dt.date | None = None) -> Listing:
    units: list[Unit] = []
    for ami_key, tiers in (rec.get("ami_grid") or {}).items():
        a = parse.whole(ami_key)
        for t in tiers:
            size = parse.size(t.get("unit_size"))
            bands = _bands(t.get("households") or [], a)
            ids = t.get("unit_ids") or []
            if not ids:
                units.append(Unit(size=size, rent=t.get("monthly_rent"), ami=a, bands=bands))
            for u in ids:
                units.append(
                    Unit(
                        size=size,
                        rent=u.get("monthly_rent") or t.get("monthly_rent"),
                        ami=a,
                        unit_no=(u.get("name") or "").strip() or None,
                        bands=list(bands),
                    )
                )
    details = parse.text(rec.get("details") or "") if rec.get("details") else ""
    how = []
    mail = re.sub(r"\s*\n\s*", ", ", (rec.get("mail_address") or "").strip())
    if mail:
        how.append(f"standard mail only to: {mail}")
    if rec.get("project_file"):
        how.append(f"application PDF {rec['project_file']}")
    if rec.get("project_file_link"):
        how.append(f"apply link {rec['project_file_link']}")
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]*\w", details)))
    if emails:
        how.append("email " + ", ".join(emails))
    end = (rec.get("end_date") or "")[:10]
    today = today or dt.date.today()
    deadline = end if end and end >= today.isoformat() else None
    addr = re.sub(r"\s+", " ", rec.get("address") or "").strip() or None
    return Listing(
        source=source,
        ext_id=str(rec["id"]),
        title=re.sub(r"\s+", " ", rec.get("title") or "").strip(),
        url=f"{site}/re-rentals/{rec['id']}",
        address=addr,
        borough=parse.borough(addr) or parse.borough(rec.get("borough_title")),
        status="open",
        kind="re-rental",
        units=units,
        deadline=deadline,
        senior_only=bool(re.search(r"\bsenior (housing|building)|62 (years|and older)", details, re.I)),
        notes=("Apply: " + "; ".join(how)) if how else None,
    )


def _from_dict(d: dict) -> Listing:
    units = [Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]}) for u in d["units"]]
    return Listing(**{**d, "units": units})


class WebarycoAdapter(Adapter):
    """Shared by afny and iafford: list, then one detail call per listing (cached per list record per day)."""

    site = ""

    def _headers(self, path: str = "/re-rentals") -> dict:
        return {"Origin": self.site, "Referer": f"{self.site}{path}", "Accept": "application/json"}

    def fetch(self) -> Result:
        data = fetch.json(f"{API}?page_size=100&page=0", headers=self._headers())
        if not data.get("success"):
            raise RuntimeError(f"{self.name}: API error {str(data)[:200]}")
        records = data["data"]["records"]
        cache: dict = self.cache.setdefault("details", {})
        today = dt.date.today()
        out = []
        for r in records:
            rid = str(r["id"])
            stamp = today.isoformat() + _json.dumps(r, sort_keys=True)  # refetch when the card changes, or daily
            hit = cache.get(rid)
            if hit and hit.get("stamp") == stamp:
                out.append(_from_dict(hit["listing"]))
                continue
            d = fetch.json(f"{API}{rid}/", headers=self._headers(f"/re-rentals/{rid}"))
            rec = (d.get("data") or {}).get("record")
            if not d.get("success") or not rec:
                raise RuntimeError(f"{self.name}: detail {rid} failed: {str(d)[:200]}")
            lst = parse_detail(self.name, self.site, rec, today)
            cache[rid] = {"stamp": stamp, "listing": asdict(lst)}
            out.append(lst)
        live = {str(r["id"]) for r in records}
        for k in [k for k in cache if k not in live]:
            del cache[k]
        return Result(listings=out)


class AFNY(WebarycoAdapter):
    name = "afny"
    title = "Affordable for New York (AFNY)"
    url = "https://afny.org/re-rentals"
    site = "https://afny.org"
    every_hours = 4  # first come, first served
    min_listings = 1  # only a handful of listings at a time
