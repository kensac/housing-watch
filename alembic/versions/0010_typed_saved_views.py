"""saved_views.query: typed filter tree and sort pairs, replacing the `field.op.values` strings

Version 1: {"filters": [{"field", "op", "values": [str]}], "q", "sort": "x" | "-x" | "", "columns"}
Version 2: {"version": 2, "filter": {<field>: {<op>: <typed value>}}, "q", "sort": [{"field", "direction"}],
            "columns"}

Self-contained (it must not follow the app's code as that changes). Every v1 filter keeps its meaning: band fields
move under `units.bands`, ops are renamed (nin -> not_in, ncontains -> not_contains, empty/notempty -> empty:
true/false), values get their types, a time with a clock moves to since/until, and two filters on one field and
operator are combined into the one condition that means the same (two `in` lists intersect, two `le` keep the
lower, ...). A v1 filter the app no longer understood is carried over as it was, so loading warns about it just as
before. Only a pair with no single equivalent (two `contains` on one field, two different `eq`) stops the
migration, naming the view.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-23 12:00:00

"""

import json
import re
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NY = ZoneInfo("America/New_York")
NEWEST_FIRST = {"changed", "first_seen"}  # sorts whose v1 "x" ran descending
UNITS = {"m": "minutes", "h": "hours", "d": "days"}
OPS = {"nin": "not_in", "ncontains": "not_contains"}


class Unrepresentable(ValueError):
    pass


LISTING_COMPUTED = {"verdict", "reason", "eligible_units", "unit_count", "commute", "application", "application_group",
                    "application_notes"}  # fmt: skip


def path(name: str, page: str) -> list[str]:
    """A v1 field name as its v2 tree path: bands sit under units, and on the events page every listing field
    (v1 prefixed only the listing's own columns) under listing."""
    parts = name.split(".")
    if page == "events" and parts[0] != "listing" and (parts[0] in ("units", "bands") or name in LISTING_COMPUTED):
        parts.insert(0, "listing")
    i = 1 if parts[0] == "listing" else 0
    if parts[i : i + 1] == ["bands"]:
        parts.insert(i, "units")
    return parts


def _num(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def _when(s: str) -> date | datetime:
    d = datetime.fromisoformat(s.strip())
    if "T" not in s:
        return d.date()
    return d if d.tzinfo else d.replace(tzinfo=NY)


def _iso(d: date | datetime) -> str:
    return d.isoformat()


def condition(op_: str, vals: list[str]) -> tuple[str, object]:
    """One v1 filter as (v2 op, JSON value). A value the app would not have accepted stays a list of strings,
    so loading the view reports it as before."""
    vals = [v.strip() for v in vals]
    if op_ in ("empty", "notempty"):
        return "empty", op_ == "empty"
    if op_ in ("in", "nin"):
        return OPS.get(op_, op_), vals
    if op_ in ("contains", "ncontains") and len(vals) == 1:
        return OPS.get(op_, op_), vals[0]
    if op_ == "is" and len(vals) == 1 and vals[0].lower() in ("yes", "true", "1", "no", "false", "0"):
        return "is", vals[0].lower() in ("yes", "true", "1")
    if op_ in ("eq", "ne", "lt", "le", "gt", "ge") and len(vals) == 1 and _num(vals[0]) is not None:
        return op_, _num(vals[0])
    if op_ == "last" and len(vals) == 1 and (m := re.fullmatch(r"(\d+)([mhd])", vals[0])):
        return "last", {"amount": int(m[1]), "unit": UNITS[m[2]]}
    try:
        if op_ in ("after", "before") and len(vals) == 1:
            d = _when(vals[0])
            if isinstance(d, datetime):  # v1: a time means from (after) or up to (before) that instant
                return ("since" if op_ == "after" else "until"), _iso(d)
            return op_, _iso(d)
        if op_ == "between" and len(vals) == 2:
            if all(_num(v) is not None for v in vals):
                lo, hi = sorted(_num(v) for v in vals)
                return "between", {"min": lo, "max": hi}
            a, b = _when(vals[0]), _when(vals[1])
            if not isinstance(a, datetime) and not isinstance(b, datetime):
                a, b = sorted((a, b))
                return "between", {"from": _iso(a), "to": _iso(b)}
            lo = a if isinstance(a, datetime) else datetime.combine(a, time(), NY)
            hi = b if isinstance(b, datetime) else datetime.combine(b + timedelta(days=1), time(), NY)
            return "range", (_iso(lo), _iso(hi))  # since + until, split by the caller
    except ValueError:
        pass
    return op_, vals


def _merge(op_: str, a, b):
    """Two conditions with one operator on one field, as the single condition both together mean."""
    if a == b:
        return a
    if op_ == "in" and isinstance(a, list) and isinstance(b, list):
        both = [v for v in a if v in b]
        if both:
            return both
    elif op_ == "not_in" and isinstance(a, list) and isinstance(b, list):
        return a + [v for v in b if v not in a]
    elif op_ in ("lt", "le", "gt", "ge", "after", "before", "since", "until") and type(a) is type(b) is not list:
        key = datetime.fromisoformat if op_ in ("since", "until") else None  # offsets differ across DST
        return (min if op_ in ("lt", "le", "before", "until") else max)(a, b, key=key)
    elif op_ == "last" and isinstance(a, dict) and isinstance(b, dict):
        mins = {"minutes": 1, "hours": 60, "days": 1440}
        return min(a, b, key=lambda d: d["amount"] * mins[d["unit"]])
    elif op_ == "between" and isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
        lo_k, hi_k = ("min", "max") if "min" in a else ("from", "to")
        lo, hi = max(a[lo_k], b[lo_k]), min(a[hi_k], b[hi_k])
        if lo <= hi:
            return {lo_k: lo, hi_k: hi}
    raise Unrepresentable(f"two `{op_}` filters on one field ({a!r} and {b!r}) have no single equivalent")


def upgrade_query(q: dict, page: str = "listings") -> dict:
    if q.get("version") == 2:
        return q
    tree: dict = {}

    def put(p: list[str], op_: str, v) -> None:
        node = tree
        for k in p:
            node = node.setdefault(k, {})
        node[op_] = _merge(op_, node[op_], v) if op_ in node else v

    for f in q.get("filters") or []:
        p, (op_, v) = path(f.get("field", ""), page), condition(f.get("op", ""), list(f.get("values") or []))
        if op_ == "range":
            put(p, "since", v[0])
            put(p, "until", v[1])
        else:
            put(p, op_, v)
    s = q.get("sort") or ""
    name, flip = s.lstrip("-"), s.startswith("-")
    sort = [{"field": name, "direction": "desc" if (name in NEWEST_FIRST) != flip else "asc"}] if name else []
    return {"version": 2, "filter": tree, "q": q.get("q") or "", "sort": sort, "columns": list(q.get("columns") or [])}


def _v1_values(op_: str, v) -> tuple[str, list[str]]:
    if op_ == "empty":
        return ("empty" if v else "notempty"), []
    if op_ == "is":
        return "is", ["yes" if v else "no"]
    if op_ == "last":
        return "last", [f"{v['amount']}{v['unit'][0]}"]
    if op_ == "between":
        lo, hi = (v["min"], v["max"]) if "min" in v else (v["from"], v["to"])
        return "between", [f"{lo:g}" if isinstance(lo, float) else lo, f"{hi:g}" if isinstance(hi, float) else hi]
    if op_ in ("since", "until"):
        return ("after" if op_ == "since" else "before"), [v]
    vals = v if isinstance(v, list) else [f"{v:g}" if isinstance(v, float) else str(v)]
    return {"not_in": "nin", "not_contains": "ncontains"}.get(op_, op_), vals


def downgrade_query(q: dict, page: str = "listings") -> dict:
    if q.get("version") != 2:
        return q
    filters = []

    def walk(node: dict, prefix: list[str]) -> None:
        for k, v in node.items():
            if k in ("listing", "units", "bands") and isinstance(v, dict):  # no field has these names
                walk(v, prefix + [k])
                continue
            name = ".".join(prefix + [k])
            name = re.sub(r"(^|\.)units\.bands\.", r"\1bands.", name)
            if (
                page == "events"
                and name.startswith("listing.")
                and (name.split(".")[1] in ("units", "bands") or name[8:] in LISTING_COMPUTED)
            ):
                name = name[8:]
            for op_, x in v.items():
                o, vals = _v1_values(op_, x)
                filters.append({"field": name, "op": o, "values": vals})

    walk(q.get("filter") or {}, [])
    sort = q.get("sort") or []
    s = ""
    if sort:
        name, d = sort[0]["field"], sort[0]["direction"]
        s = name if (d == "desc") == (name in NEWEST_FIRST) else f"-{name}"
    return {"filters": filters, "q": q.get("q") or "", "sort": s, "columns": list(q.get("columns") or [])}


def _rewrite(fn) -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, page, name, query FROM saved_views ORDER BY id")).all()
    for vid, page, name, q in rows:
        try:
            new = fn(q or {}, page)
        except Unrepresentable as e:
            raise RuntimeError(f"saved view {vid} “{name}”: {e}; edit or delete it, then migrate again") from None
        if new != q:
            conn.execute(sa.text("UPDATE saved_views SET query = CAST(:q AS jsonb) WHERE id = :id"),
                         {"q": json.dumps(new), "id": vid})  # fmt: skip


def upgrade() -> None:
    _rewrite(upgrade_query)


def downgrade() -> None:
    _rewrite(downgrade_query)
