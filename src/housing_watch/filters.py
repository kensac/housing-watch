"""Generic filters: a field registry read off orm.py, a typed filter AST, its URL grammar, and SQL for it.

    ?f=borough.in.Brooklyn,Queens&f=units.rent.le.3000&f=last_changed.after.2026-10-01

A filter is `<field>.<op>[.<values>]`, values comma-separated (a literal comma is `\\,`). Fields come from
the listings/units/bands (and events) columns, so a new column in orm.py is filterable with no other
change; LABELS and HIDDEN only rename or hide. Unit fields match a listing when one unit matches all
unit conditions together; band conditions must hold on one band of that same unit. Computed fields
(verdict, reason, unit counts) are judged at read time, so queries.py applies them in Python.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Table, and_, exists, func, not_, or_, select

from . import tracker as T
from .orm import BandRow, EventRow, ListingRow, UnitRow

NY = ZoneInfo("America/New_York")

OPS = {
    "text": ("in", "nin", "contains", "ncontains", "empty", "notempty"),
    "number": ("eq", "ne", "lt", "le", "gt", "ge", "between", "empty", "notempty"),
    "time": ("after", "before", "between", "last", "empty", "notempty"),
    "bool": ("is", "empty", "notempty"),
}
NO_VALUE = {"empty", "notempty"}
SYMBOL = {"eq": "=", "ne": "≠", "lt": "<", "le": "≤", "gt": ">", "ge": "≥"}
OP_LABEL = {
    "in": "is", "nin": "is not", "contains": "contains", "ncontains": "does not contain", "empty": "is empty",
    "notempty": "is not empty", "between": "between", "after": "after", "before": "before",
    "last": "in the last", "is": "is", **SYMBOL,
}  # fmt: skip

# Internal columns nobody filters on, and labels that read better than the column name.
HIDDEN = {
    "listings": {"key", "fingerprint", "ext_id", "url"},
    "units": {"id", "listing_key", "pos"},
    "bands": {"id", "unit_id"},
    "events": {"id", "key", "detail"},
}
LABELS = {
    "gone_since": "Removed", "last_changed": "Last changed", "senior_only": "Senior only",
    "units.size": "Unit size", "units.rent": "Rent", "units.ami": "AMI %", "units.unit_no": "Unit #",
    "units.set_aside": "Set-aside", "units.available": "Available", "bands.household": "Household size",
    "bands.income_min": "Income min", "bands.income_max": "Income max", "at": "Time",
    "notified": "Mailed", "summary": "Summary", "first_run": "First run", "units.chart_year": "HUD chart year",
}  # fmt: skip
MONEY = {"units.rent", "bands.income_min", "bands.income_max"}
GROUPS = {"listings": "Listing", "units": "Unit", "bands": "Income band", "events": "Event", "computed": "Verdict"}


class FilterError(ValueError):
    pass


@dataclass(frozen=True, eq=False)  # compared by identity: columns overload ==
class Field:
    name: str  # as it appears in the URL
    label: str
    group: str
    type: str  # text, number, time or bool: which operators apply
    scope: str  # listing | unit | band | event | computed
    column: Any = None  # SQL column or expression; None for computed fields
    choices: tuple[str, ...] = ()  # fixed values (computed enums)
    order: tuple[str, ...] = ()  # known values in display order, where other values may occur too

    @property
    def money(self) -> bool:
        return self.name.removeprefix("listing.") in MONEY


def _type(col) -> str | None:
    try:
        py = col.type.python_type
    except NotImplementedError:
        return None
    if py is bool:
        return "bool"
    if py in (int, float):
        return "number"
    if py is datetime:
        return "time"
    return "text" if py is str else None  # JSONB, arrays: not filterable


def _columns(table: Table, prefix: str, scope: str, hidden: set[str]) -> list[Field]:
    out = []
    for col in table.columns:
        t = _type(col)
        if t is None or col.name in hidden:
            continue
        name = prefix + col.name
        label = LABELS.get(name.removeprefix("listing."), col.name.replace("_", " ").capitalize())
        out.append(Field(name, label, GROUPS[table.name], t, scope, col))
    return out


COMPUTED = [
    Field("verdict", "Verdict", "Verdict", "text", "computed", choices=("eligible", "possible", "excluded")),
    Field("reason", "Verdict reason", "Verdict", "text", "computed"),
    Field("eligible_units", "Eligible units", "Verdict", "number", "computed"),
    Field("unit_count", "Units", "Verdict", "number", "computed"),
    Field("commute", "Commute (min)", "Commute", "number", "computed"),  # transit to HW_COMMUTE_TO, eligible only
    # the tracker sheet's Status as a stage: legacy statuses mapped, unknown ones as written
    Field("application", "Application stage", "Tracker", "text", "computed", order=tuple(T.STAGES)),
    Field("application_group", "Whose move", "Tracker", "text", "computed", choices=T.GROUPS),
    Field("application_notes", "Tracker notes", "Tracker", "text", "computed"),
]


def listing_fields(listings: Table = ListingRow.__table__, units=UnitRow.__table__, bands=BandRow.__table__,
                   prefix: str = "") -> dict[str, Field]:  # fmt: skip
    fs = _columns(listings, prefix, "listing", HIDDEN["listings"])
    fs += _columns(units, "units.", "unit", HIDDEN["units"]) + _columns(bands, "bands.", "band", HIDDEN["bands"])
    return {f.name: f for f in fs + COMPUTED}


def event_fields(events: Table = EventRow.__table__) -> dict[str, Field]:
    first_run = func.coalesce(events.c.detail["baseline"].astext, "false") == "true"
    fs = _columns(events, "", "event", HIDDEN["events"]) + [
        Field("first_run", "First run", "Event", "bool", "event", first_run)
    ]
    return {f.name: f for f in fs} | listing_fields(prefix="listing.")


LISTING_FIELDS = listing_fields()
EVENT_FIELDS = event_fields()


# --- the AST and its URL form --------------------------------------------------------------------------


@dataclass(frozen=True)
class Cond:
    field: Field
    op: str
    raw: tuple[str, ...] = ()  # canonical string values, as in the URL
    values: tuple = ()  # typed

    def url(self) -> str:
        vals = ",".join(v.replace(",", "\\,") for v in self.raw)
        return f"{self.field.name}.{self.op}" + (f".{vals}" if self.op not in NO_VALUE else "")

    def to_json(self) -> dict:
        return {"field": self.field.name, "op": self.op, "values": list(self.raw)}

    def label(self) -> str:
        f, op, v = self.field, self.op, self.values
        if op in NO_VALUE:
            return f"{f.label} {OP_LABEL[op]}"
        if op in ("in", "nin"):
            return f"{f.label} {OP_LABEL[op]} {', '.join(x or '(empty)' for x in v)}"
        if op in ("contains", "ncontains"):
            return f"{f.label} {OP_LABEL[op]} “{v[0]}”"
        if op == "is":
            return f"{f.label}: {'yes' if v[0] else 'no'}"
        if op == "last":
            n, unit = int(self.raw[0][:-1]), {"m": "minute", "h": "hour", "d": "day"}[self.raw[0][-1]]
            return f"{f.label} in the last {n} {unit}{'s' if n != 1 else ''}"
        show = [_show(f, x, raw) for x, raw in zip(v, self.raw, strict=True)]
        if op == "between":
            return f"{f.label} between {show[0]} and {show[1]}"
        return f"{f.label} {OP_LABEL[op]} {show[0]}"


def _show(f: Field, v, raw: str) -> str:
    if f.type == "number":
        return f"${v:,.0f}" if f.money else f"{v:,g}"
    return datetime.fromisoformat(raw).strftime("%b %-d, %Y" + (", %-I:%M %p" if "T" in raw else ""))


def _split(s: str) -> list[str]:
    return [p.replace("\\,", ",") for p in re.split(r"(?<!\\),", s)]


def _value(f: Field, op: str, s: str):
    s = s.strip()
    if f.type == "number":
        try:
            return float(s)
        except ValueError:
            raise FilterError(f"{f.label}: “{s}” is not a number") from None
    if f.type == "bool":
        if s.lower() in ("yes", "true", "1"):
            return True
        if s.lower() in ("no", "false", "0"):
            return False
        raise FilterError(f"{f.label}: use yes or no, not “{s}”")
    if f.type == "time":
        if op == "last":
            if not re.fullmatch(r"\d+[mhd]", s):
                raise FilterError(f"{f.label}: “{s}” should look like 24h or 7d")
            return s
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            raise FilterError(f"{f.label}: “{s}” is not a date (use YYYY-MM-DD)") from None
        return d if d.tzinfo else d.replace(tzinfo=NY)
    if f.choices and op in ("in", "nin") and s not in f.choices:
        raise FilterError(f"{f.label}: “{s}” is not one of {', '.join(f.choices)}")
    return s


def cond(fields: dict[str, Field], name: str, op: str, raw: list[str] | tuple[str, ...] = ()) -> Cond:
    f = fields.get(name)
    if f is None:
        raise FilterError(f"Unknown filter field “{name}”")
    if op not in OPS[f.type]:
        raise FilterError(f"{f.label} can't be filtered with “{op}” (use {', '.join(OPS[f.type])})")
    raw = () if op in NO_VALUE else tuple(raw)
    want = {"between": 2, "in": None, "nin": None}.get(op, 1) if op not in NO_VALUE else 0
    if (want is None and not raw) or (want is not None and len(raw) != want):
        raise FilterError(f"{f.label} {OP_LABEL[op]}: expected {want or 'one or more'} value(s)")
    values = tuple(_value(f, op, x) for x in raw)
    canon = tuple(
        (f"{v:g}" if f.type == "number" else "yes" if v is True else "no" if v is False else r.strip())
        for v, r in zip(values, raw, strict=True)
    )
    return Cond(f, op, canon, values)


def parse_one(s: str, fields: dict[str, Field]) -> Cond:
    name = max((n for n in fields if s.startswith(n + ".")), key=len, default=None)
    if name is None:
        raise FilterError(f"Unknown filter field in “{s}”")
    op, _, rest = s[len(name) + 1 :].partition(".")
    return cond(fields, name, op, _split(rest) if rest else [])


def parse(params: list[str], fields: dict[str, Field]) -> list[Cond]:
    return [parse_one(p, fields) for p in params if p]


def from_json(items: list[dict], fields: dict[str, Field]) -> tuple[list[Cond], list[str]]:
    """Saved-view filters, validated against today's fields: what no longer applies becomes a warning."""
    conds, warnings = [], []
    for it in items:
        try:
            conds.append(cond(fields, it.get("field", ""), it.get("op", ""), it.get("values", [])))
        except FilterError as e:
            warnings.append(f"{e} — this filter was dropped.")
    return conds, warnings


# --- evaluation ----------------------------------------------------------------------------------------


def _since(raw: str) -> datetime:
    n, u = int(raw[:-1]), raw[-1]
    return datetime.now(UTC) - timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[u]: n})


def _bounds(c: Cond) -> tuple[datetime | None, datetime | None]:
    """[lo, hi) for a time condition; a bare date covers its whole (New York) day."""

    def end(v: datetime, raw: str) -> datetime:
        return v + timedelta(days=1) if "T" not in raw else v

    if c.op == "last":
        return _since(c.raw[0]), None
    if c.op == "after":
        return end(c.values[0], c.raw[0]), None
    if c.op == "before":
        return None, c.values[0]
    return c.values[0], end(c.values[1], c.raw[1])


def sql(c: Cond):
    """One condition as a SQL clause on its own column."""
    col, op, v = c.field.column, c.op, c.values
    empty = col.is_(None) | (col == "") if c.field.type == "text" else col.is_(None)
    if op == "empty":
        return empty
    if op == "notempty":
        return not_(empty)
    if op == "in":
        return col.in_(v)
    if op == "nin":
        return or_(col.notin_(v), col.is_(None))
    if op in ("contains", "ncontains"):
        like = col.ilike("%" + v[0].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        return like if op == "contains" else or_(not_(like), col.is_(None))
    if op == "is":
        return col.is_(v[0])
    if c.field.type == "time":
        lo, hi = _bounds(c)
        return and_(*([col >= lo] if lo else []), *([col < hi] if hi else []))
    if op == "between":
        return col.between(min(v), max(v))
    if op == "ne":
        return or_(col != v[0], col.is_(None))
    return {"eq": col.__eq__, "lt": col.__lt__, "le": col.__le__, "gt": col.__gt__, "ge": col.__ge__}[op](v[0])


def listing_where(conds: list[Cond], listings=ListingRow.__table__, units=UnitRow.__table__,
                  bands=BandRow.__table__) -> list:  # fmt: skip
    """SQL clauses on `listings` for the listing, unit and band conditions (computed ones are skipped)."""
    out = [sql(c) for c in conds if c.field.scope == "listing"]
    uc = [sql(c) for c in conds if c.field.scope == "unit"]
    bc = [sql(c) for c in conds if c.field.scope == "band"]
    if bc:
        uc.append(exists(select(1).where(bands.c.unit_id == units.c.id, *bc)))
    if uc:
        out.append(exists(select(1).where(units.c.listing_key == listings.c.key, *uc)))
    return out


def unit_where(conds: list[Cond], listings=ListingRow.__table__, units=UnitRow.__table__,
               bands=BandRow.__table__) -> list:  # fmt: skip
    """SQL clauses on `units` joined to `listings`: the unit conditions hold on that unit itself, and the
    band conditions on one of its bands."""
    out = [sql(c) for c in conds if c.field.scope in ("listing", "unit")]
    bc = [sql(c) for c in conds if c.field.scope == "band"]
    if bc:
        out.append(exists(select(1).where(bands.c.unit_id == units.c.id, *bc)))
    return out


def match(c: Cond, value) -> bool:
    """A condition against one computed value, in Python, with the same meaning as sql()."""
    op, v = c.op, c.values
    if op in NO_VALUE:
        return (value in (None, "")) == (op == "empty")
    if op == "in":
        return value in v
    if op == "nin":
        return value not in v
    if op in ("contains", "ncontains"):
        hit = value is not None and v[0].lower() in str(value).lower()
        return hit == (op == "contains")
    if value is None:
        return op == "ne"
    if op == "is":
        return value is v[0]
    if op == "between":
        return min(v) <= value <= max(v)
    return {"eq": value == v[0], "ne": value != v[0], "lt": value < v[0], "le": value <= v[0],
            "gt": value > v[0], "ge": value >= v[0]}[op]  # fmt: skip
