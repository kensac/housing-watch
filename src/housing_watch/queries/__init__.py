"""Read API over the store, for the UI and the digest. Returns plain dataclasses: the read model.

Verdicts are computed here, at read time, by criteria; nothing judged is ever stored. Every listing is
judged once per store version (a cheap aggregate over `listings`) and kept in memory, so the pages
filter and sort a few thousand listings in Python without reloading them.

These dataclasses are the API's source of truth: web/schema.py derives its GraphQL types from them, a field's
trailing comment becoming its description. A field made with `internal()` stays out of the API and the MCP tools.
"""

# ruff: noqa: F401 - the package's API is these re-exports

from ._common import (
    EVENT_TYPES,
    HEALTH,
    LISTING_EVENTS,
    STATUSES,
    VERDICTS,
    Health,
    JobStatus,
    Status,
    as_open,
    day,
    internal,
    is_baseline,
    known,
    now,
    public,
)
from ._detail import ListingDetail, UnitView, listing_detail
from ._events import (
    Brief,
    BriefUnit,
    EventPage,
    EventView,
    ListingChange,
    PageChange,
    SourceFailure,
    event_detail,
    event_where,
    events,
    load_listings,
    pending_events,
)
from ._fleet import Fleet, FleetFailure, FleetHost, FleetWorker, JobArgs, QueueCount, fleet
from ._judging import ListingSummary, UnitSummary, judged, judged_by_key, judged_listing, judged_units
from ._overview import MatrixCell, Overview, overview
from ._search import (
    COMPUTED,
    DEFAULT_SORT,
    LISTING_SORTS,
    UNIT_COMPUTED,
    UNIT_SORTS,
    Direction,
    ListingPage,
    ListingQuery,
    Sort,
    SortSpec,
    UnitPage,
    filter_listings,
    filter_units,
    hides_gone,
    order,
    search_listings,
    search_units,
)
from ._sources import SourceHealth, sources
from ._tracker import TrackerBoard, TrackerLink, TrackerRowView, tracker_board, tracker_link
from ._value_counts import ENUM_MAX, is_enum, value_counts
