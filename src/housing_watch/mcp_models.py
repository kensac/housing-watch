"""Pydantic models for the MCP tools, derived like the GraphQL types: filters from filters.py's registry, sorts from
queries.py's, results from the read-model dataclasses (minus `internal()` fields). They give every tool a typed
JSON Schema for its parameters and its output, and never restate a field.

A filter model dumps (`tree()`) to filters.py's JSON tree, the same shape the GraphQL input takes, except that enum
values are the data's own strings ("1br", "Staten Island"), not GraphQL names. Each model is a JSON Schema `$defs`
entry, referenced wherever it is used (the server sends schemas unexpanded); an optional field is just not required,
never `anyOf [X, null]`, and an unset one is left out of the output.
"""

from __future__ import annotations

import dataclasses
import types as pytypes
import typing
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_serializer, model_validator
from pydantic.json_schema import SkipJsonSchema

from . import filters as F
from . import queries, views
from .web.derive import comments


def _no_default(s: dict) -> None:
    s.pop("default", None)


def opt(t, description: str | None = None, **kw):
    """An optional field: absent or null in, absent out; `t` alone in the schema."""
    return t | SkipJsonSchema[None], Field(None, description=description, json_schema_extra=_no_default, **kw)


class Strict(BaseModel):
    """Refuses unknown keys; leaves unset (None) fields out of its output."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    @model_serializer(mode="wrap")
    def _set_only(self, handler):
        return {k: v for k, v in handler(self).items() if v is not None}


class NumberRange(Strict):
    min: float
    max: float


class DateRange(Strict):
    """Whole New York days, both included."""

    from_: date = Field(alias="from")
    to: date


class Duration(Strict):
    amount: int = Field(ge=1)
    unit: F.DurationUnit


def _op_type(kind: str, op: str, item):
    if op in ("in", "not_in"):
        return list[item]
    if op in ("contains", "not_contains"):
        return str
    if op in ("empty", "is"):
        return bool
    if op == "between":
        return NumberRange if kind == "number" else DateRange
    if op in ("after", "before"):
        return date
    if op in ("since", "until"):
        return datetime
    if op == "last":
        return Duration
    return float


_LEAVES: dict[object, type[BaseModel]] = {}


def _leaf(f: F.Field) -> type[BaseModel]:
    key = f.values if f.type == "enum" else f.type
    if key not in _LEAVES:
        item = f.values if f.type == "enum" else str
        name = f"{f.values.__name__}Filter" if f.type == "enum" else f"{f.type.capitalize()}Filter"
        ops = {f"{op}_": opt(_op_type(f.type, op, item), F.OP_DOC[op], alias=op) for op in F.OPS[f.type]}
        _LEAVES[key] = create_model(name, __base__=Strict, **ops)
    return _LEAVES[key]


_NESTED_DOC = {
    "units": "A listing matches when one of its units meets every condition here together.",
    "bands": "The unit matches when one of its income bands meets every condition here together.",
    "listing": "Conditions on the event's listing; events with no listing never match.",
}


def _label(f: F.Field, prefix: str) -> str | None:
    """The field's label, unless it only restates its name."""
    return None if f.label.lower().replace(" ", "_") == f.name[len(prefix) :] else f.label


def _container(name: str, fields: dict[str, F.Field], prefix: str, nested: dict[str, type[BaseModel]],
               doc: str) -> type[BaseModel]:  # fmt: skip
    level = [f for n, f in fields.items() if n.startswith(prefix) and "." not in n[len(prefix) :]]
    spec: dict = {f.name[len(prefix) :]: opt(_leaf(f), _label(f, prefix)) for f in level}
    spec |= {k: opt(m, _NESTED_DOC[k]) for k, m in nested.items()}
    return create_model(name, __base__=Strict, __doc__=doc, **spec)


_ALL = "Every condition must hold; each field holds its operators."
BandFilter = _container("BandFilter", F.LISTING_FIELDS, "units.bands.", {}, "Conditions on one income band.")
UnitFilter = _container("UnitFilter", F.LISTING_FIELDS, "units.", {"bands": BandFilter}, f"One unit. {_ALL}")
ListingFilter = _container("ListingFilter", F.LISTING_FIELDS, "", {"units": UnitFilter}, f"Which listings. {_ALL}")
EventFilter = _container("EventFilter", F.EVENT_FIELDS, "", {"listing": ListingFilter}, f"Which events. {_ALL}")


def tree(m: BaseModel | None) -> dict:
    """A filter model as filters.py's JSON tree."""
    return m.model_dump(mode="json", by_alias=True, exclude_none=True) if m else {}


def _sort_model(name: str, specs: dict[str, queries.SortSpec]) -> type[BaseModel]:
    return create_model(
        name, __base__=Strict,
        field=(Literal[tuple(specs)], Field(description="; ".join(f"{k}: {s.label}" for k, s in specs.items()))),
        direction=opt(queries.Direction, "Omitted: the field's natural direction (newest first for times, ascending "
                      "otherwise)."),
    )  # fmt: skip


ListingSort = _sort_model("ListingSort", queries.LISTING_SORTS)
UnitSort = _sort_model("UnitSort", queries.UNIT_SORTS)


def sorts(page: str, given: list[BaseModel]) -> tuple[queries.Sort, ...]:
    return views.sorts(page, [(s.field, s.direction) for s in given])


# --- results: the read model without its internal fields ---------------------------------------------------

_MODELS: dict[type, type[BaseModel]] = {}


def _py(t):
    origin, args = typing.get_origin(t), typing.get_args(t)
    if origin in (typing.Union, pytypes.UnionType):
        return typing.Union[tuple(_py(a) for a in args)]  # noqa: UP007 - built from a tuple
    if origin in (list, tuple, set, frozenset):
        return list[_py(args[0])]
    if dataclasses.is_dataclass(t):
        return model_of(t)
    return t


def model_of(dc: type, name: str | None = None, **override) -> type[BaseModel]:
    """A pydantic model of a read-model dataclass: its public fields, nested dataclasses as models."""
    if not override and dc in _MODELS:
        return _MODELS[dc]
    hints, notes = typing.get_type_hints(dc), comments(dc)
    spec = {f.name: (override.get(f.name, _py(hints[f.name])), Field(description=notes.get(f.name)))
            for f in dataclasses.fields(dc) if not f.metadata.get("internal")}  # fmt: skip
    m = create_model(name or dc.__name__, __doc__=dc.__doc__ if dc.__doc__ and "(" not in dc.__doc__[:40] else None,
                     **spec)  # fmt: skip
    if not override:
        _MODELS[dc] = m
    return m


ListingModel = model_of(queries.ListingSummary, "Listing")
UnitModel = model_of(queries.UnitSummary, "Unit")


class VerdictCounts(BaseModel):
    eligible: int
    possible: int
    excluded: int


class ListingsPage(BaseModel):
    items: list[ListingModel]
    total: int
    next_offset: int | None = Field(description="Pass as offset for the next page; null after the last.")
    verdict_counts: VerdictCounts = Field(description="Matches by verdict, ignoring a verdict condition.")


class UnitsPage(BaseModel):
    items: list[UnitModel]
    total: int
    next_offset: int | None


class Change(BaseModel):
    """An event, with what it recorded for its type."""

    at: datetime
    type: queries.EventType
    source: str
    key: str | None
    title: str | None
    verdict: queries.Verdict | None
    first_run: bool = Field(description="From a source's first run: starting inventory, not news.")
    changes: list[str] | None = Field(None, description="changed: the differences in words, most decisive first.")
    noise: bool | None = Field(None, description="changed: a status flap to or from unknown.")
    added: list[str] | None = Field(None, description="page_changed: new lines of text (the first 15).")
    new_links: list[str] | None = None
    error: str | None = Field(None, description="source_failed / source_recovered.")


class Changes(BaseModel):
    items: list[Change]
    total: int
    next_before: int | None = Field(description="Pass as before for older events; null after the last.")


OverviewModel = model_of(queries.Overview, "Overview", eligible_now=list[ListingModel], recent=list[Change])


class OverviewResult(OverviewModel):
    next_digest: str | None = Field(description="The digest email that would go out next, as text.")


SourceModel = model_of(queries.SourceHealth, "Source")


class SearchHit(BaseModel):
    id: str
    title: str
    url: str


class SearchResults(BaseModel):
    results: list[SearchHit]


class FetchMetadata(BaseModel):
    verdict: queries.Verdict


class Document(BaseModel):
    id: str
    title: str
    url: str
    text: str = Field(description="The listing's detail as JSON: units, verdicts, commute, tracker stage, history.")
    metadata: FetchMetadata


class FieldInfo(BaseModel):
    name: str = Field(description="Its path in the filter, dot-joined.")
    path: list[str]
    label: str
    group: str
    type: F.FieldType
    ops: list[F.Op]
    choices: list[str] = Field(description="An enum field's values.")


# --- saved views and the tracker: what the write tools take and return -------------------------------------


def _columns(page: str):
    return Literal[tuple(views.COLUMNS[page])]


_EMPTY = "Empty: the page's default."


class ListingsViewState(Strict):
    filter: ListingFilter | SkipJsonSchema[None] = Field(None, json_schema_extra=_no_default)
    q: str = ""
    sort: list[ListingSort] = Field(default_factory=list, description=_EMPTY)
    columns: list[_columns("listings")] = Field(default_factory=list, description=_EMPTY)


class UnitsViewState(Strict):
    filter: ListingFilter | SkipJsonSchema[None] = Field(None, json_schema_extra=_no_default)
    q: str = ""
    sort: list[UnitSort] = Field(default_factory=list, description=_EMPTY)
    columns: list[_columns("units")] = Field(default_factory=list, description=_EMPTY)


class EventsViewState(Strict):
    filter: EventFilter | SkipJsonSchema[None] = Field(None, json_schema_extra=_no_default)
    columns: list[_columns("events")] = Field(default_factory=list, description=_EMPTY)


_PAGE_STATE = {"listings": ListingsViewState, "units": UnitsViewState, "events": EventsViewState}


class ViewState(Strict):
    """A page's state: set exactly one, the page it is for."""

    listings: ListingsViewState | SkipJsonSchema[None] = Field(None, json_schema_extra=_no_default)
    units: UnitsViewState | SkipJsonSchema[None] = Field(None, json_schema_extra=_no_default)
    events: EventsViewState | SkipJsonSchema[None] = Field(None, json_schema_extra=_no_default)

    @model_validator(mode="after")
    def _one(self):
        if sum(x is not None for x in (self.listings, self.units, self.events)) != 1:
            raise ValueError("set exactly one of listings, units, events")
        return self

    def state(self) -> views.State:
        """As views.make_state() builds it; FilterError or ViewError says what is wrong."""
        page, given = next((k, v) for k in views.FIELDS if (v := getattr(self, k)) is not None)
        sort = [(s.field, s.direction) for s in getattr(given, "sort", ())]
        return views.make_state(page, tree(given.filter), getattr(given, "q", ""), sort, given.columns)

    @classmethod
    def of(cls, st: views.State) -> ViewState:
        """A saved state, as create_view and change_view take it."""
        j = st.to_json()
        given = {"filter": j["filter"] or None, "columns": j["columns"]}
        if st.page != "events":
            given |= {"q": j["q"], "sort": j["sort"]}
        return cls.model_validate({st.page: _PAGE_STATE[st.page].model_validate(given)})


class ViewModel(BaseModel):
    """A saved view: a named filter, search, sort and columns for one page."""

    id: int
    page: views.Page
    name: str
    position: int
    is_default: bool = Field(description="The page opens on it.")
    state: ViewState = Field(description="As create_view and change_view take it.")
    warnings: list[str] = Field(description="What of the saved state no longer applies, and was dropped.")
    updated: datetime | None
    count: int = Field(description="What it matches now.")

    @classmethod
    def of(cls, v: views.View) -> ViewModel:
        return cls(id=v.id, page=v.page, name=v.name, position=v.position, is_default=v.is_default,
                   state=ViewState.of(v.state), warnings=v.warnings, updated=v.updated, count=v.count)  # fmt: skip


class Deleted(BaseModel):
    id: int


TrackerModel = model_of(queries.TrackerBoard, "Tracker")


TrackerLinkModel = model_of(queries.TrackerLink)
