"""The owner's listing corrections: a patch of published fields and/or a replacement unit list.

A patch is `{"fields": {name: value}, "units": [unit] | None}`. `fields` sets any of FIELDS (null clears a
nullable one); `units`, when given, replaces the source's units outright. db.record_listings applies the patch to
every scrape of the listing, so a run never undoes it, and stores the source's own values beside it.
"""

from __future__ import annotations

import dataclasses
from datetime import date

from .models import Band, Borough, Kind, Listing, PublishedStatus, Refused, Result, Size, Unit, values


def _text(v, name: str, nullable: bool = True):
    if v is None and nullable:
        return None
    if not isinstance(v, str) or not v.strip():
        raise Refused(f"{name}: expected {'text or null' if nullable else 'non-empty text'}")
    return v.strip()


def _one_of(alias, nullable: bool = False):
    def check(v, name: str):
        if v is None and nullable:
            return None
        if v not in values(alias):
            raise Refused(f"{name}: {v!r} is not one of {', '.join(values(alias))}")
        return v

    return check


def _deadline(v, name: str):
    if v is None:
        return None
    try:
        return date.fromisoformat(v).isoformat()
    except (TypeError, ValueError):
        raise Refused(f"{name}: expected an ISO date (YYYY-MM-DD) or null") from None


def _bool(v, name: str):
    if not isinstance(v, bool):
        raise Refused(f"{name}: expected true or false")
    return v


FIELDS = {
    "title": lambda v, n: _text(v, n, nullable=False),
    "url": lambda v, n: _text(v, n, nullable=False),
    "address": _text,
    "borough": _one_of(Borough, nullable=True),
    "status": _one_of(PublishedStatus),
    "kind": _one_of(Kind),
    "deadline": _deadline,
    "senior_only": _bool,
    "notes": _text,
}


def _num(v, name: str, kind=float, nullable: bool = True):
    if v is None and nullable:
        return None
    if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
        raise Refused(f"{name}: expected a non-negative number{' or null' if nullable else ''}")
    if kind is int and v != int(v):
        raise Refused(f"{name}: expected a whole number")
    return kind(v)


def _unit(u, i: int) -> dict:
    at = f"units[{i}]"
    if not isinstance(u, dict):
        raise Refused(f"{at}: expected an object")
    if extra := set(u) - {f.name for f in dataclasses.fields(Unit)}:
        raise Refused(f"{at}: unknown field(s) {', '.join(sorted(extra))}")
    bands = u.get("bands") or []
    if not isinstance(bands, list):
        raise Refused(f"{at}.bands: expected a list")
    out_bands = []
    for j, b in enumerate(bands):
        if not isinstance(b, dict) or set(b) - {"household", "income_min", "income_max"} or "household" not in b:
            raise Refused(f"{at}.bands[{j}]: expected {{household, income_min, income_max}}")
        hh = _num(b["household"], f"{at}.bands[{j}].household", int, nullable=False)
        if hh is None or hh < 1:
            raise Refused(f"{at}.bands[{j}].household: at least 1")
        lo = _num(b.get("income_min"), f"{at}.bands[{j}].income_min", int)
        hi = _num(b.get("income_max"), f"{at}.bands[{j}].income_max", int)
        if lo is not None and hi is not None and lo > hi:
            raise Refused(f"{at}.bands[{j}]: income_min above income_max")
        out_bands.append({"household": hh, "income_min": lo, "income_max": hi})
    available = u.get("available")
    if available is not None:
        _bool(available, f"{at}.available")
    return {
        "size": _one_of(Size)(u.get("size"), f"{at}.size"),
        "rent": _num(u.get("rent"), f"{at}.rent"),
        "ami": _num(u.get("ami"), f"{at}.ami", int),
        "unit_no": _text(u.get("unit_no"), f"{at}.unit_no"),
        "bands": out_bands,
        "set_aside": _text(u.get("set_aside"), f"{at}.set_aside"),
        "available": available,
        "chart_year": _num(u.get("chart_year"), f"{at}.chart_year", int),
    }


def validate(fields: dict | None, units: list | None) -> dict:
    """The stored form of a patch, normalized; Refused names the first bad value."""
    fields = fields or {}
    if bad := set(fields) - set(FIELDS):
        raise Refused(f"cannot override {', '.join(sorted(bad))}; fields are {', '.join(FIELDS)}")
    if not fields and units is None:
        raise Refused("an override needs at least one field or a unit list")
    if units is not None and not isinstance(units, list):
        raise Refused("units: expected a list")
    return {
        "fields": {k: FIELDS[k](v, k) for k, v in fields.items()},
        "units": [_unit(u, i) for i, u in enumerate(units)] if units is not None else None,
    }


def from_json(d: dict) -> Listing:
    """A Listing stored as dataclasses.asdict JSON."""
    return Result.from_json({"listings": [d]}).listings[0]


def to_json(lst: Listing) -> dict:
    return dataclasses.asdict(lst)


def apply(lst: Listing, patch: dict) -> Listing:
    """`lst` with the patch over it; source and ext_id never change."""
    units = lst.units
    if patch.get("units") is not None:
        units = [Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]}) for u in patch["units"]]
    return dataclasses.replace(lst, **patch.get("fields", {}), units=units)
