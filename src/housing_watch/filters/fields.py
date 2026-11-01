"""The field registry: every filterable field, read off orm.py's columns, plus the computed ones."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Table, func

from .. import tracker as T
from ..criteria import Verdict
from ..models import values as values_of
from ..orm import BandRow, EventRow, ListingRow, UnitRow

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
    "listings": {"key", "fingerprint", "ext_id", "url", "updated"},
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


def listing_fields(listings: Table = ListingRow.__table__, prefix: str = "") -> dict[str, Field]:
    fs = _columns(listings, prefix, "listing", HIDDEN["listings"])
    fs += _columns(UnitRow.__table__, prefix + "units.", "unit", HIDDEN["units"])
    fs += _columns(BandRow.__table__, prefix + "units.bands.", "band", HIDDEN["bands"])
    computed = [Field(prefix + f.name, f.label, f.group, f.type, f.scope, values=f.values, order=f.order)
                for f in COMPUTED]  # fmt: skip
    return {f.name: f for f in fs + computed}


def event_fields() -> dict[str, Field]:
    events = EventRow.__table__
    first_run = func.coalesce(events.c.detail["baseline"].astext, "false") == "true"
    fs = _columns(events, "", "event", HIDDEN["events"]) + [
        Field("first_run", "First run", "Event", "bool", "event", first_run)
    ]
    return {f.name: f for f in fs} | listing_fields(prefix="listing.")


LISTING_FIELDS = listing_fields()
EVENT_FIELDS = event_fields()
