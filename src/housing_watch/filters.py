"""Generic filters: a field registry read off orm.py, typed conditions, their JSON tree, and SQL for them.

A filter is a tree with one key per field, holding that field's operators; every condition must hold:

    {"borough": {"in": ["Brooklyn", "Queens"]}, "units": {"rent": {"le": 3000}, "bands": {"household": {"eq": 1}}},
     "last_changed": {"last": {"amount": 7, "unit": "days"}}}

The same tree is the GraphQL input (web/filter_types.py derives `ListingFilterInput` from this registry), the MCP
tools' parameter (mcp_server.py derives pydantic models from it) and what a saved view stores. Fields come from the
listings/units/bands (and events) columns, so a new column in orm.py is filterable with no other change; a column
with `info={"values": <Literal alias>}` is an enum; LABELS and HIDDEN only rename or hide. Unit fields match a
listing when one unit matches all unit conditions together; band conditions (`units.bands`) must hold on one band
of that same unit. Computed fields (verdict, reason, unit counts) are judged at read time, so queries.py applies
them in Python.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import Table, and_, exists, func, not_, or_, select

from . import tracker as T
from .criteria import Verdict
from .models import values as values_of
from .orm import BandRow, EventRow, ListingRow, UnitRow

NY = ZoneInfo("America/New_York")

type FieldType = Literal["text", "enum", "number", "time", "bool"]
type Op = Literal["in", "not_in", "contains", "not_contains", "empty", "eq", "ne", "lt", "le", "gt", "ge", "between",
                  "after", "before", "since", "until", "last", "is"]  # fmt: skip
type DurationUnit = Literal["minutes", "hours", "days"]

# The operators each type of field takes; each is a key of that field's object in the tree.
OPS: dict[str, tuple[Op, ...]] = {
    "text": ("in", "not_in", "contains", "not_contains", "empty"),
    "enum": ("in", "not_in", "empty"),
    "number": ("eq", "ne", "lt", "le", "gt", "ge", "between", "empty"),
    "time": ("after", "before", "between", "since", "until", "last", "empty"),
    "bool": ("is", "empty"),
}
SYMBOL = {"eq": "=", "ne": "≠", "lt": "<", "le": "≤", "gt": ">", "ge": "≥"}
OP_LABEL: dict[Op, str] = {
    "in": "is", "not_in": "is not", "contains": "contains", "not_contains": "does not contain", "empty": "is empty",
    "between": "between", "after": "after", "before": "before", "since": "since", "until": "until",
    "last": "in the last", "is": "is", **SYMBOL,
}  # fmt: skip
OP_DOC: dict[Op, str] = {  # what each operator means, for the API docs
    "in": "Any of these values.", "not_in": "None of these values (an empty field matches).",
    "contains": "Contains this text, ignoring case.",
    "not_contains": "Does not contain this text (an empty field matches).",
    "empty": "true: has no value; false: has one.", "eq": "Equal to.", "ne": "Not equal to (an empty field matches).",
    "lt": "Less than.", "le": "At most.", "gt": "More than.", "ge": "At least.", "after": "After this New York day.",
    "before": "Before this New York day.", "since": "At or after this instant.", "until": "Before this instant.",
    "last": "Within this long before now.", "is": "Yes or no.", "between": "Within this range, both ends included.",
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
    "units.set_aside": "Set-aside", "units.available": "Available", "units.bands.household": "Household size",
    "units.bands.income_min": "Income min", "units.bands.income_max": "Income max", "at": "Time",
    "notified": "Mailed", "summary": "Summary", "first_run": "First run", "units.chart_year": "HUD chart year",
}  # fmt: skip
MONEY = {"units.rent", "units.bands.income_min", "units.bands.income_max"}
GROUPS = {"listings": "Listing", "units": "Unit", "bands": "Income band", "events": "Event", "computed": "Verdict"}
NESTED = ("listing", "units", "bands")  # tree keys that hold another level of fields, not operators


class FilterError(ValueError):
    pass


@dataclass(frozen=True, eq=False)  # compared by identity: columns overload ==
class Field:
    name: str  # its path in the filter tree, dot-joined ("units.rent")
    label: str
    group: str
    type: FieldType  # which operators apply
    scope: str  # listing | unit | band | event | computed
    column: Any = None  # SQL column or expression; None for computed fields
    values: Any = None  # an enum field's value set: a Literal alias
    order: tuple[str, ...] = ()  # known values in display order, where other values may occur too

    @property
    def money(self) -> bool:
        return self.name.removeprefix("listing.") in MONEY

    @property
    def path(self) -> tuple[str, ...]:
        return tuple(self.name.split("."))

    @property
    def choices(self) -> tuple[str, ...]:
        """An enum field's values, in order."""
        return values_of(self.values) if self.values else ()


@dataclass(frozen=True)
class Range:
    min: float
    max: float


@dataclass(frozen=True)
class Days:
    """Whole New York days, both ends included."""

    start: date
    end: date


@dataclass(frozen=True)
class Duration:
    amount: int
    unit: DurationUnit

    def delta(self) -> timedelta:
        return timedelta(**{self.unit: self.amount})


def _type(col) -> FieldType | None:
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
    if py is str:
        return "enum" if col.info.get("values") else "text"
    return None  # JSONB, arrays: not filterable


def _columns(table: Table, prefix: str, scope: str, hidden: set[str]) -> list[Field]:
    out = []
    for col in table.columns:
        t = _type(col)
        if t is None or col.name in hidden:
            continue
        name = prefix + col.name
        label = LABELS.get(name.removeprefix("listing."), col.name.replace("_", " ").capitalize())
        out.append(Field(name, label, GROUPS[table.name], t, scope, col, col.info.get("values")))
    return out


COMPUTED = [
    Field("verdict", "Verdict", "Verdict", "enum", "computed", values=Verdict),
    Field("reason", "Verdict reason", "Verdict", "text", "computed"),
    Field("eligible_units", "Eligible units", "Verdict", "number", "computed"),
    Field("unit_count", "Units", "Verdict", "number", "computed"),
    Field("commute", "Commute (min)", "Commute", "number", "computed"),  # transit to HW_COMMUTE_TO, eligible only
    # the tracker sheet's Status as a stage: legacy statuses mapped, unknown ones as written
    Field("application", "Application stage", "Tracker", "text", "computed", order=tuple(T.STAGES)),
    Field("application_group", "Whose move", "Tracker", "enum", "computed", values=T.StageGroup),
    Field("application_notes", "Tracker notes", "Tracker", "text", "computed"),
]


def listing_fields(listings: Table = ListingRow.__table__, units=UnitRow.__table__, bands=BandRow.__table__,
                   prefix: str = "") -> dict[str, Field]:  # fmt: skip
    fs = _columns(listings, prefix, "listing", HIDDEN["listings"])
    fs += _columns(units, prefix + "units.", "unit", HIDDEN["units"])
    fs += _columns(bands, prefix + "units.bands.", "band", HIDDEN["bands"])
    computed = [Field(prefix + f.name, f.label, f.group, f.type, f.scope, values=f.values, order=f.order)
                for f in COMPUTED]  # fmt: skip
    return {f.name: f for f in fs + computed}


def event_fields(events: Table = EventRow.__table__) -> dict[str, Field]:
    first_run = func.coalesce(events.c.detail["baseline"].astext, "false") == "true"
    fs = _columns(events, "", "event", HIDDEN["events"]) + [
        Field("first_run", "First run", "Event", "bool", "event", first_run)
    ]
    return {f.name: f for f in fs} | listing_fields(prefix="listing.")


LISTING_FIELDS = listing_fields()
EVENT_FIELDS = event_fields()


# --- conditions and the tree -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Cond:
    field: Field
    op: Op
    value: Any  # typed: list[str] for in/not_in, str, float, bool, date, datetime, Range, Days or Duration

    def label(self) -> str:
        f, op, v = self.field, self.op, self.value
        if op == "empty":
            return f"{f.label} is {'' if v else 'not '}empty"
        if op in ("in", "not_in"):
            return f"{f.label} {OP_LABEL[op]} {', '.join(x or '(empty)' for x in v)}"
        if op in ("contains", "not_contains"):
            return f"{f.label} {OP_LABEL[op]} “{v}”"
        if op == "is":
            return f"{f.label}: {'yes' if v else 'no'}"
        if op == "last":
            return f"{f.label} in the last {v.amount} {v.unit[:-1] if v.amount == 1 else v.unit}"
        if op == "between":
            lo, hi = (v.min, v.max) if isinstance(v, Range) else (v.start, v.end)
            return f"{f.label} between {_show(f, lo)} and {_show(f, hi)}"
        return f"{f.label} {OP_LABEL[op]} {_show(f, v)}"


def _show(f: Field, v) -> str:
    if isinstance(v, datetime):
        return v.astimezone(NY).strftime("%b %-d, %Y, %-I:%M %p")
    if isinstance(v, date):
        return v.strftime("%b %-d, %Y")
    return f"${v:,.0f}" if f.money else f"{v:,g}"


def _number(f: Field, v) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise FilterError(f"{f.label}: {v!r} is not a number")
    return float(v)


def _date(f: Field, v) -> date:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    try:
        return date.fromisoformat(v)
    except (TypeError, ValueError):
        raise FilterError(f"{f.label}: {v!r} is not a date (YYYY-MM-DD)") from None


def _datetime(f: Field, v) -> datetime:
    try:
        d = v if isinstance(v, datetime) else datetime.fromisoformat(v)
    except (TypeError, ValueError):
        raise FilterError(f"{f.label}: {v!r} is not a date and time (ISO 8601)") from None
    return d if d.tzinfo else d.replace(tzinfo=NY)


def _object(f: Field, op: str, v, keys: tuple[str, ...]) -> dict:
    if not isinstance(v, dict) or set(v) != set(keys):
        raise FilterError(f"{f.label} {OP_LABEL[op]}: give {' and '.join(keys)}")
    return v


def _value(f: Field, op: str, v):
    """A tree value as the typed value of a Cond; FilterError says what is wrong with it."""
    if op in ("empty", "is"):
        if not isinstance(v, bool):
            raise FilterError(f"{f.label} {OP_LABEL[op]}: use true or false, not {v!r}")
        return v
    if op in ("in", "not_in"):
        if not isinstance(v, list | tuple) or not v or not all(isinstance(x, str) for x in v):
            raise FilterError(f"{f.label} {OP_LABEL[op]}: expected one or more values")
        if f.choices and (bad := [x for x in v if x not in f.choices]):
            raise FilterError(f"{f.label}: “{bad[0]}” is not one of {', '.join(f.choices)}")
        return tuple(dict.fromkeys(v))
    if op in ("contains", "not_contains"):
        if not isinstance(v, str) or not v.strip():
            raise FilterError(f"{f.label} {OP_LABEL[op]}: expected some text")
        return v
    if f.type == "number":
        if op == "between":
            o = _object(f, op, v, ("min", "max"))
            lo, hi = _number(f, o["min"]), _number(f, o["max"])
            return Range(min(lo, hi), max(lo, hi))
        return _number(f, v)
    if op in ("after", "before"):
        return _date(f, v)
    if op == "between":
        o = _object(f, op, v, ("from", "to"))
        a, b = _date(f, o["from"]), _date(f, o["to"])
        return Days(min(a, b), max(a, b))
    if op in ("since", "until"):
        return _datetime(f, v)
    if op == "last":
        o = _object(f, op, v, ("amount", "unit"))
        n, unit = o["amount"], o["unit"]
        if isinstance(n, bool) or not isinstance(n, int) or n < 1 or unit not in values_of(DurationUnit):
            raise FilterError(f"{f.label} in the last: a whole number of minutes, hours or days")
        return Duration(n, unit)
    raise FilterError(f"{f.label} can't be filtered with “{op}”")  # pragma: no cover - OPS gates every op


def cond(f: Field, op: str, v) -> Cond:
    if op not in OPS[f.type]:
        raise FilterError(f"{f.label} can't be filtered with “{op}” (use {', '.join(OPS[f.type])})")
    return Cond(f, op, _value(f, op, v))


def _walk(tree, fields: dict[str, Field], prefix: str, conds: list[Cond], problems: list[str]) -> None:
    if not isinstance(tree, dict):
        problems.append(f"Expected an object of fields at “{prefix.rstrip('.') or 'the top'}”")
        return
    for k, v in tree.items():
        name = prefix + k
        if v is None:  # GraphQL and JSON clients send absent fields as null
            continue
        f = fields.get(name)
        if f is None and k in NESTED and any(n.startswith(name + ".") for n in fields):
            _walk(v, fields, name + ".", conds, problems)
            continue
        if f is None:
            problems.append(f"Unknown filter field “{name}”")
            continue
        if not isinstance(v, dict):
            problems.append(f"{f.label}: expected an object of operators")
            continue
        for op, x in v.items():
            if x is None:
                continue
            try:
                conds.append(cond(f, op, x))
            except FilterError as e:
                problems.append(str(e))


def from_tree(tree: dict | None, fields: dict[str, Field]) -> list[Cond]:
    """The conditions of a filter tree; FilterError lists every problem."""
    conds: list[Cond] = []
    problems: list[str] = []
    _walk(tree or {}, fields, "", conds, problems)
    if problems:
        raise FilterError("; ".join(problems))
    return conds


def from_saved(tree: dict | None, fields: dict[str, Field]) -> tuple[list[Cond], list[str]]:
    """A saved view's filter, validated against today's fields: what no longer applies becomes a warning."""
    conds: list[Cond] = []
    problems: list[str] = []
    _walk(tree or {}, fields, "", conds, problems)
    return conds, [f"{p} — this filter was dropped." for p in problems]


def _json(v):
    if isinstance(v, tuple):
        return list(v)
    if isinstance(v, date):  # datetime too
        return v.isoformat()
    if isinstance(v, Range):
        return {"min": v.min, "max": v.max}
    if isinstance(v, Days):
        return {"from": v.start.isoformat(), "to": v.end.isoformat()}
    if isinstance(v, Duration):
        return {"amount": v.amount, "unit": v.unit}
    return v


def to_tree(conds: list[Cond]) -> dict:
    """The JSON tree of a list of conditions (from_tree's inverse)."""
    out: dict = {}
    for c in conds:
        node = out
        for k in c.field.path:
            node = node.setdefault(k, {})
        node[c.op] = _json(c.value)
    return out


# --- evaluation ----------------------------------------------------------------------------------------


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
