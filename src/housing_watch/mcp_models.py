"""Pydantic models for the MCP tools, derived like the GraphQL types: filters from filters.py's registry, sorts from
queries.py's, results from the read-model dataclasses (minus `internal()` fields). They give every tool a typed
JSON Schema for its parameters and its output, and never restate a field.

A filter model dumps (`tree()`) to filters.py's JSON tree, the same shape the GraphQL input takes, except that enum
values are the data's own strings ("1br", "Staten Island"), not GraphQL names.
"""

from __future__ import annotations

import dataclasses
import types as pytypes
import typing
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from . import filters as F
from . import queries, views


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


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
        ops = {
            f"{op}_": (_op_type(f.type, op, item) | None, Field(None, alias=op, description=F.OP_DOC[op]))
            for op in F.OPS[f.type]
        }
        _LEAVES[key] = create_model(name, __base__=Strict, **ops)
    return _LEAVES[key]


_NESTED_DOC = {
    "units": "A listing matches when one of its units meets every condition here together.",
    "bands": "The unit matches when one of its income bands meets every condition here together.",
    "listing": "Conditions on the event's listing; events with no listing never match.",
}


def _container(name: str, fields: dict[str, F.Field], prefix: str, nested: dict[str, type[BaseModel]],
               doc: str) -> type[BaseModel]:  # fmt: skip
    level = [f for n, f in fields.items() if n.startswith(prefix) and "." not in n[len(prefix) :]]
    spec: dict = {f.name[len(prefix) :]: (_leaf(f) | None, Field(None, description=f.label)) for f in level}
    spec |= {k: (m | None, Field(None, description=_NESTED_DOC[k])) for k, m in nested.items()}
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
        direction=(queries.Direction | None, Field(None, description="Omitted: the field's natural direction "
                   "(newest first for times, ascending otherwise).")),
    )  # fmt: skip


ListingSort = _sort_model("ListingSort", queries.LISTING_SORTS)
UnitSort = _sort_model("UnitSort", queries.UNIT_SORTS)


def sorts(page: str, given: list[BaseModel]) -> tuple[queries.Sort, ...]:
    specs = views.SORTS[page]
    return (
        tuple(queries.Sort(s.field, s.direction or specs[s.field].natural) for s in given) or views.DEFAULT_SORT[page]
    )


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
    hints = typing.get_type_hints(dc)
    spec = {f.name: (override.get(f.name, _py(hints[f.name])), ...) for f in dataclasses.fields(dc)
            if not f.metadata.get("internal")}  # fmt: skip
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
    filter: ListingFilter | None = None
    q: str = ""
    sort: list[ListingSort] = Field(default_factory=list, description=_EMPTY)
    columns: list[_columns("listings")] = Field(default_factory=list, description=_EMPTY)


class UnitsViewState(Strict):
    filter: ListingFilter | None = None
    q: str = ""
    sort: list[UnitSort] = Field(default_factory=list, description=_EMPTY)
    columns: list[_columns("units")] = Field(default_factory=list, description=_EMPTY)


class EventsViewState(Strict):
    filter: EventFilter | None = None
    columns: list[_columns("events")] = Field(default_factory=list, description=_EMPTY)


class ViewState(Strict):
    """A page's state: set exactly one, the page it is for."""

    listings: ListingsViewState | None = None
    units: UnitsViewState | None = None
    events: EventsViewState | None = None

    @model_validator(mode="after")
    def _one(self):
        if sum(x is not None for x in (self.listings, self.units, self.events)) != 1:
            raise ValueError("set exactly one of listings, units, events")
        return self

    def state(self) -> views.State:
        page, given = next((k, v) for k in views.FIELDS if (v := getattr(self, k)) is not None)
        conds = F.from_tree(tree(given.filter), views.FIELDS[page])
        srt = sorts(page, given.sort) if page != "events" else ()
        return views.State(page, conds, getattr(given, "q", ""), srt, tuple(given.columns))


class SortModel(BaseModel):
    field: str
    direction: queries.Direction


class ViewModel(BaseModel):
    """A saved view: a named filter, search, sort and columns for one page."""

    id: int
    page: views.Page
    name: str
    position: int
    is_default: bool = Field(description="The page opens on it.")
    filter: dict = Field(description="filters.py's JSON tree, as the tools take it.")
    q: str
    sort: list[SortModel]
    columns: list[str]
    warnings: list[str] = Field(description="What of the saved state no longer applies, and was dropped.")
    updated: datetime | None
    count: int | None = Field(None, description="What it matches now.")

    @classmethod
    def of(cls, v: views.View) -> ViewModel:
        st = v.state.to_json()
        return cls(id=v.id, page=v.page, name=v.name, position=v.position, is_default=v.is_default,
                   filter=st["filter"], q=st["q"], sort=st["sort"], columns=st["columns"], warnings=v.warnings,
                   updated=v.updated, count=v.count)  # fmt: skip


class Deleted(BaseModel):
    id: int


TrackerModel = model_of(queries.TrackerBoard, "Tracker")


class TrackerLink(BaseModel):
    listing_key: str
    row_key: str | None = Field(description="The row the listing is matched to now; null: none.")
    listing: ListingModel | None = Field(description="The listing as it reads after the change.")
