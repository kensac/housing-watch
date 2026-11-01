"""Generic filters: a field registry read off orm.py, typed conditions, their JSON tree, and SQL for them.

A filter is a tree with one key per field, holding that field's operators; every condition must hold:

    {"borough": {"in": ["Brooklyn", "Queens"]}, "units": {"rent": {"le": 3000}, "bands": {"household": {"eq": 1}}},
     "last_changed": {"last": {"amount": 7, "unit": "days"}}}

The same tree is the GraphQL input (web/filter_types.py derives `ListingFilterInput` from this registry), the MCP
tools' parameter (mcp_models.py derives pydantic models from it) and what a saved view stores. Fields come from the
listings/units/bands (and events) columns, so a new column in orm.py is filterable with no other change; a column
with `info={"values": <Literal alias>}` is an enum; LABELS and HIDDEN only rename or hide. Unit fields match a
listing when one unit matches all unit conditions together; band conditions (`units.bands`) must hold on one band
of that same unit. Computed fields (verdict, reason, unit counts) are judged at read time, so queries/ applies
them in Python.
"""

from .evaluate import listing_where, match, sql, unit_where
from .fields import (
    EVENT_FIELDS,
    LISTING_FIELDS,
    OP_DOC,
    OP_LABEL,
    OPS,
    DurationUnit,
    Field,
    FieldType,
    Op,
    listing_fields,
)
from .tree import Cond, Days, Duration, FilterError, Range, from_saved, from_tree, to_tree

__all__ = [
    "EVENT_FIELDS", "LISTING_FIELDS", "OPS", "OP_DOC", "OP_LABEL", "Cond", "Days", "Duration", "DurationUnit", "Field",
    "FieldType", "FilterError", "Op", "Range", "from_saved", "from_tree", "listing_fields", "listing_where", "match",
    "sql", "to_tree", "unit_where",
]  # fmt: skip
