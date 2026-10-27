"""GraphQL filter, sort and column types, derived from filters.py's field registry and queries.py's sort registries.

Every filterable field is a field of `ListingFilterInput` (or `EventFilterInput`), typed by its kind: text, a
closed set (its own enum), number, time or bool. Unit fields sit under `units`, band fields under `units.bands`,
and an event's listing under `listing`; each holds that field's operators. A column added in orm.py appears here,
in `schema.graphql` and in the generated client with no other change. Each input has an output twin without the
suffix (`ListingFilter`), which saved views return.

Values go to filters.py as its JSON tree (`tree()`), the one shape the API, the MCP tools and saved views share.
"""

import enum
from datetime import date, datetime

import strawberry
from strawberry.types.base import has_object_definition

from .. import filters as F
from .. import queries, views
from .derive import enum_of, enum_type

# --- operator values --------------------------------------------------------------------------------------


@strawberry.input(description="Both ends included; `min` above `max` is read the other way round.")
class NumberRangeInput:
    min: float
    max: float


@strawberry.type
class NumberRange:
    min: float
    max: float


@strawberry.input(description="Whole New York days, both included.")
class DateRangeInput:
    from_: date = strawberry.field(name="from")
    to: date


@strawberry.type
class DateRange:
    from_: date = strawberry.field(name="from")
    to: date


DurationUnit = enum_type(F.DurationUnit)


@strawberry.input
class DurationInput:
    amount: int = strawberry.field(description="1 or more.")
    unit: DurationUnit


@strawberry.type
class Duration:
    amount: int
    unit: DurationUnit


_ATTR = {"in": "in_", "is": "is_"}  # Python keywords


def _op_type(kind: str, op: str, item, output: bool):
    """The GraphQL type of operator `op` on a field of `kind` whose values are `item`."""
    if op in ("in", "not_in"):
        return list[item]
    if op in ("contains", "not_contains"):
        return str
    if op in ("empty", "is"):
        return bool
    if op == "between":
        return (
            (NumberRange if output else NumberRangeInput)
            if kind == "number"
            else (DateRange if output else DateRangeInput)
        )
    if op in ("after", "before"):
        return date
    if op in ("since", "until"):
        return datetime
    if op == "last":
        return Duration if output else DurationInput
    return float


def _leaf(name: str, kind: str, item=str, description: str | None = None) -> tuple[type, type]:
    """The input and output types of one kind of field: an optional field per operator, all of which must hold."""
    out = []
    for output in (False, True):
        anns, ns = {}, {}
        for op in F.OPS[kind]:
            attr = _ATTR.get(op, op)
            anns[attr] = _op_type(kind, op, item, output) | None
            ns[attr] = strawberry.field(default=None, name=op, description=F.OP_DOC[op])
        ns["__annotations__"] = anns
        cls = type(name if output else f"{name}Input", (), ns)
        deco = strawberry.type if output else strawberry.input
        out.append(deco(cls, name=cls.__name__, description=description))
    return out[0], out[1]


LEAVES: dict[object, tuple[type, type]] = {
    "text": _leaf("TextFilter", "text"),
    "number": _leaf("NumberFilter", "number"),
    "time": _leaf("TimeFilter", "time", description="Days and instants in New York time."),
    "bool": _leaf("BoolFilter", "bool"),
}


def _leaf_of(f: F.Field) -> tuple[type, type]:
    if f.type != "enum":
        return LEAVES[f.type]
    if f.values not in LEAVES:
        e = enum_type(f.values)
        LEAVES[f.values] = _leaf(f"{e.__name__}Filter", "enum", e)
    return LEAVES[f.values]


# --- the filter objects -----------------------------------------------------------------------------------

_NESTED_DOC = {
    "units": "A listing matches when one of its units meets every condition here together.",
    "bands": "The unit matches when one of its income bands meets every condition here together.",
    "listing": "Conditions on the event's listing, as it reads now; events with no listing never match.",
}


def _container(name: str, fields: dict[str, F.Field], prefix: str, nested: dict[str, tuple[type, type]],
               description: str) -> tuple[type, type]:  # fmt: skip
    """The input and output types of one level of the tree: its fields, then its nested levels."""
    level = [f for n, f in fields.items() if n.startswith(prefix) and "." not in n[len(prefix) :]]
    out = []
    for i in (0, 1):
        anns, ns = {}, {}
        for f in level:
            key = f.name[len(prefix) :]
            anns[key] = _leaf_of(f)[i] | None
            hint = f" ({f.group})" if f.scope == "computed" else ""
            ns[key] = strawberry.field(default=None, description=f"{f.label}{hint}.")
        for key, types in nested.items():
            anns[key] = types[i] | None
            ns[key] = strawberry.field(default=None, description=_NESTED_DOC[key])
        ns["__annotations__"] = anns
        cls = type(f"{name}Input" if i == 0 else name, (), ns)
        out.append((strawberry.input if i == 0 else strawberry.type)(cls, name=cls.__name__, description=description))
    return out[0], out[1]


_ALL = "Every condition must hold."
BandFilters = _container("BandFilter", F.LISTING_FIELDS, "units.bands.", {}, "Conditions on one income band.")
UnitFilters = _container("UnitFilter", F.LISTING_FIELDS, "units.", {"bands": BandFilters},
                         f"Conditions on one unit. {_ALL}")  # fmt: skip
ListingFilters = _container("ListingFilter", F.LISTING_FIELDS, "", {"units": UnitFilters},
                            f"Which listings (or units) to show. {_ALL}")  # fmt: skip
EventFilters = _container("EventFilter", F.EVENT_FIELDS, "", {"listing": ListingFilters},
                          f"Which events to show. {_ALL}")  # fmt: skip
ListingFilterInput, ListingFilter = ListingFilters
EventFilterInput, EventFilter = EventFilters

FilterFieldName = enum_of(
    "FilterFieldName", dict.fromkeys([*F.LISTING_FIELDS, *F.EVENT_FIELDS]),
    "A filterable field, by its path in the filter object (`units.rent` is `units_rent`).",
)  # fmt: skip
FilterOp = enum_type(F.Op, "An operator: a key of a field's filter object.", name="FilterOp")
FieldType = enum_type(F.FieldType, "Which operators a field takes.", name="FieldType")


def tree(obj):
    """A Strawberry input value as filters.py's JSON tree: GraphQL field names, enum members as their values."""
    if obj is None:
        return None
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, list):
        return [tree(x) for x in obj]
    if has_object_definition(obj):
        out = {}
        for fd in obj.__strawberry_definition__.fields:
            v = tree(getattr(obj, fd.python_name))
            if v is not None:
                out[fd.graphql_name or fd.python_name] = v
        return out
    return obj


def _build(cls, node: dict):
    """An output filter object from a typed tree (conditions' values, not JSON)."""
    kw = {}
    for fd in cls.__strawberry_definition__.fields:
        v = node.get(fd.graphql_name or fd.python_name)
        if isinstance(v, dict):
            t = fd.type.of_type if hasattr(fd.type, "of_type") else fd.type
            v = _build(t, v)
        elif isinstance(v, F.Range):
            v = NumberRange(min=v.min, max=v.max)
        elif isinstance(v, F.Days):
            v = DateRange(from_=v.start, to=v.end)
        elif isinstance(v, F.Duration):
            v = Duration(amount=v.amount, unit=DurationUnit(v.unit))
        elif isinstance(v, tuple):
            v = list(v)
        kw[fd.python_name] = v
    return cls(**kw)


def output(cls, conds: list[F.Cond]):
    """`ListingFilter` or `EventFilter` for a list of conditions."""
    node: dict = {}
    for c in conds:
        n = node
        for k in c.field.path:
            n = n.setdefault(k, {})
        n[c.op] = c.value
    return _build(cls, node)


# --- sorts and columns ------------------------------------------------------------------------------------

SortDirection = enum_type(queries.Direction, name="SortDirection")


def _sort_types(name: str, specs: dict[str, queries.SortSpec]):
    fld = enum_of(f"{name}SortField", specs)

    @strawberry.input(name=f"{name}SortInput")
    class SortInput:
        field: fld
        direction: SortDirection | None = strawberry.field(default=None, description="null: the field's natural one.")

    @strawberry.type(name=f"{name}Sort")
    class Sort:
        field: fld
        direction: SortDirection

    @strawberry.type(name=f"{name}SortChoice")
    class SortChoice:
        field: fld
        label: str
        natural: SortDirection = strawberry.field(description="The direction a sort on it starts in.")

    return SortInput, Sort, SortChoice


ListingSortInput, ListingSort, ListingSortChoice = _sort_types("Listing", queries.LISTING_SORTS)
UnitSortInput, UnitSort, UnitSortChoice = _sort_types("Unit", queries.UNIT_SORTS)
SORT_TYPES = {"listings": (ListingSort, ListingSortChoice), "units": (UnitSort, UnitSortChoice)}


def sort_pairs(given: list | None) -> list[tuple[str, str | None]]:
    """Sort inputs as views.sorts() takes them."""
    return [(s.field.value, s.direction.value if s.direction else None) for s in given or ()]


def sorts(page: str, given: list | None) -> tuple[queries.Sort, ...]:
    """The query's sort from its input (views.sorts)."""
    return views.sorts(page, sort_pairs(given))


def sort_output(page: str, sort: tuple[queries.Sort, ...]) -> list:
    cls = SORT_TYPES[page][0]
    return [cls(field=s.field, direction=s.direction) for s in sort]


def _column_types(page: str):
    col = enum_of(f"{page[:-1].capitalize()}Column", views.COLUMNS[page], f"A column of the {page} page.")

    @strawberry.type(name=f"{page[:-1].capitalize()}ColumnChoice")
    class ColumnChoice:
        column: col
        label: str

    return col, ColumnChoice


ListingColumn, ListingColumnChoice = _column_types("listings")
UnitColumn, UnitColumnChoice = _column_types("units")
EventColumn, EventColumnChoice = _column_types("events")
COLUMN_TYPES = {"listings": ListingColumnChoice, "units": UnitColumnChoice, "events": EventColumnChoice}


def values_of(xs) -> tuple[str, ...]:
    """Enum inputs as their domain values."""
    return tuple(x.value for x in xs or ())
