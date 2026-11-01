"""Conditions: a filter tree checked into typed Conds, and back to its JSON."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..models import values as values_of
from .fields import NESTED, OP_LABEL, OPS, DurationUnit, Field, Op

NY = ZoneInfo("America/New_York")


class FilterError(ValueError):
    pass


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
