"""Evaluation: a Cond as a SQL clause, or matched against one computed value in Python."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, exists, not_, or_, select

from ..orm import BandRow, ListingRow, UnitRow
from .tree import NY, Cond


def _start(d: date) -> datetime:
    """The first instant of a New York day."""
    return datetime.combine(d, time(), NY)


def _bounds(c: Cond) -> tuple[datetime | None, datetime | None]:
    """[lo, hi) for a time condition; a date covers its whole (New York) day."""
    v = c.value
    return {
        "after": lambda: (_start(v + timedelta(days=1)), None),
        "before": lambda: (None, _start(v)),
        "between": lambda: (_start(v.start), _start(v.end + timedelta(days=1))),
        "since": lambda: (v, None),
        "until": lambda: (None, v),
        "last": lambda: (datetime.now(UTC) - v.delta(), None),
    }[c.op]()


def sql(c: Cond):
    """One condition as a SQL clause on its own column."""
    col, op, v = c.field.column, c.op, c.value
    if op == "empty":
        empty = or_(col.is_(None), col == "") if c.field.type == "text" else col.is_(None)
        return empty if v else not_(empty)
    if op == "in":
        return col.in_(v)
    if op == "not_in":
        return or_(col.notin_(v), col.is_(None))
    if op in ("contains", "not_contains"):
        like = col.ilike("%" + v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        return like if op == "contains" else or_(not_(like), col.is_(None))
    if op == "is":
        return col.is_(v)
    if c.field.type == "time":
        lo, hi = _bounds(c)
        return and_(*([col >= lo] if lo else []), *([col < hi] if hi else []))
    if op == "between":
        return col.between(v.min, v.max)
    if op == "ne":
        return or_(col != v, col.is_(None))
    return {"eq": col.__eq__, "lt": col.__lt__, "le": col.__le__, "gt": col.__gt__, "ge": col.__ge__}[op](v)


def listing_where(conds: list[Cond]) -> list:
    """SQL clauses on `listings` for the listing, unit and band conditions (computed ones are skipped)."""
    out = [sql(c) for c in conds if c.field.scope == "listing"]
    uc = [sql(c) for c in conds if c.field.scope == "unit"]
    bc = [sql(c) for c in conds if c.field.scope == "band"]
    if bc:
        uc.append(exists(select(1).where(BandRow.unit_id == UnitRow.id, *bc)))
    if uc:
        out.append(exists(select(1).where(UnitRow.listing_key == ListingRow.key, *uc)))
    return out


def unit_where(conds: list[Cond]) -> list:
    """SQL clauses on `units` joined to `listings`: the unit conditions hold on that unit itself, and the
    band conditions on one of its bands."""
    out = [sql(c) for c in conds if c.field.scope in ("listing", "unit")]
    bc = [sql(c) for c in conds if c.field.scope == "band"]
    if bc:
        out.append(exists(select(1).where(BandRow.unit_id == UnitRow.id, *bc)))
    return out


def match(c: Cond, value) -> bool:
    """A condition against one computed value, in Python, with the same meaning as sql()."""
    op, v = c.op, c.value
    if op == "empty":
        return (value in (None, "")) == v
    if op == "in":
        return value in v
    if op == "not_in":
        return value not in v
    if op in ("contains", "not_contains"):
        hit = value is not None and v.lower() in str(value).lower()
        return hit == (op == "contains")
    if value is None:
        return op == "ne"
    if op == "is":
        return value is v
    if c.field.type == "time":
        lo, hi = _bounds(c)
        return (lo is None or value >= lo) and (hi is None or value < hi)
    if op == "between":
        return v.min <= value <= v.max
    return {"eq": value == v, "ne": value != v, "lt": value < v, "le": value <= v, "gt": value > v,
            "ge": value >= v}[op]  # fmt: skip
