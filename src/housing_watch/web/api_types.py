# pyright: reportInvalidTypeForm=false
# Annotations here name types built at import time from the registries, which a static checker cannot see.
"""The GraphQL schema's types: enums, object types, connections, inputs and payloads. schema.py resolves them.

Object types are derived from the read model's dataclasses (derive.py), never written out: a field added to
a dataclass is in the schema, and in `schema.graphql`, with no other change. Only API envelopes (connections,
inputs, payloads, meta) are declared here; filter, sort and column types in filter_types.py.
"""

from typing import Annotated

import strawberry
from starlette.concurrency import run_in_threadpool

from .. import applications, eligibility, models, queries, tracker, views
from .. import commute as C
from .. import filters as F
from ..notify import digest
from . import filter_types as FT
from .derive import derive, enum_type, gql_name

# --- enums: the domain's own constants ------------------------------------------------------------------

# The Literal aliases of the read model become enums wherever derive() meets them; these give them descriptions.
Verdict = enum_type(eligibility.Verdict, "How a listing or unit fits the profile, judged at read time.")
Status = enum_type(queries.Status, "A listing's status as published; gone once its source dropped it.")
enum_type(models.PublishedStatus, "A listing's status as its source publishes it.")
enum_type(models.Size, "A unit size: a GraphQL name cannot start with a digit, so 1br is `_1br`.")
enum_type(models.Borough)
enum_type(models.Kind, "What kind of opportunity: `re_rental` is re-rental.")
enum_type(models.EventType)
Page = enum_type(views.Page, "A list page of the app.")
StageGroup = enum_type(tracker.StageGroup, "Whose move an application stage is.")
enum_type(tracker.Match, "How a listing was matched to its tracker row.", name="TrackerMatch")
enum_type(queries.Health, "Ok, failing, or never (no run has succeeded yet).")
enum_type(queries.JobStatus, "A Procrastinate job's status.")


# --- derived types ----------------------------------------------------------------------------------------

derive(models.Band, "IncomeBand")
derive(C.Route, "CommuteRoute")
Listing = derive(queries.ListingSummary, "Listing", description="A listing, summarized and judged for the profile.")
Unit = derive(queries.UnitSummary, "Unit")
derive(queries.UnitView, "DetailUnit")
ListingChange = derive(queries.ListingChange)
PageChange = derive(queries.PageChange)
SourceFailure = derive(queries.SourceFailure)
EventDetail = Annotated[ListingChange | PageChange | SourceFailure, strawberry.union("EventDetail")]


def _detail(root: strawberry.Parent[queries.EventView]) -> EventDetail | None:
    """What the event recorded, typed by its kind; null for kinds that record nothing."""
    return queries.event_detail(root)


async def _event_listing(root: strawberry.Parent[queries.EventView], info: strawberry.Info) -> Listing | None:
    """The listing the event is about, as it reads now; null for source events and listings no longer stored."""
    return await info.context["loaders"].listing.load(root.key) if root.key else None


Event = derive(queries.EventView, "Event", extra={"detail": strawberry.field(resolver=_detail),
                                                  "listing": strawberry.field(resolver=_event_listing)})  # fmt: skip


def _pdfs(root: strawberry.Parent[queries.ListingDetail]) -> list[str]:
    """The application PDFs linked from the listing's notes."""
    return applications.pdf_urls(root.notes)


ListingDetail = derive(queries.ListingDetail, extra={"pdfs": strawberry.field(resolver=_pdfs)})
Source = derive(queries.SourceHealth, "Source")
derive(queries.FleetWorker)
derive(queries.FleetFailure)
derive(queries.FleetHost)
derive(tracker.Step, "StageStep", props=("group",))
derive(tracker.Cell, "TrackerCell")
derive(queries.TrackerRowView, "TrackerRow")
Tracker = derive(queries.TrackerBoard, "Tracker")
Profile = derive(eligibility.Profile)


@strawberry.type(description="A value an enum field can take.")
class EnumChoice:
    name: str = strawberry.field(description="As the filter takes it: the value's GraphQL enum name.")
    label: str = strawberry.field(description="As the data has it.")


def _ops(root: strawberry.Parent[F.Field]) -> list[FT.FilterOp]:
    """The operators a filter on this field can use: keys of its filter object."""
    return list(F.OPS[root.type])


def _choices(root: strawberry.Parent[F.Field]) -> list[EnumChoice]:
    """An enum field's values, in order; empty for the other types."""
    return [EnumChoice(name=gql_name(v), label=v) for v in root.choices]


def _path(root: strawberry.Parent[F.Field]) -> list[str]:
    """Where it sits in the filter object: `["units", "rent"]` is `{units: {rent: {...}}}`."""
    return list(root.path)


FilterField = derive(F.Field, "FilterField", exclude=("scope", "column", "order", "values"), props=("money",),
                     types={"name": FT.FilterFieldName, "type": FT.FieldType},
                     extra={"ops": strawberry.field(resolver=_ops), "choices": strawberry.field(resolver=_choices),
                            "path": strawberry.field(resolver=_path)})  # fmt: skip


@strawberry.type(description="What the Listings page shows: the part of its URL a saved view captures.")
class ListingsViewState:
    filter: FT.ListingFilter
    q: str
    sort: list[FT.ListingSort]
    columns: list[FT.ListingColumn]


@strawberry.type(description="What the Units page shows.")
class UnitsViewState:
    filter: FT.ListingFilter
    q: str
    sort: list[FT.UnitSort]
    columns: list[FT.UnitColumn]


@strawberry.type(description="What the Events page shows (newest first, no search).")
class EventsViewState:
    filter: FT.EventFilter
    columns: list[FT.EventColumn]


ViewState = Annotated[ListingsViewState | UnitsViewState | EventsViewState, strawberry.union("ViewState")]


def view_state(st: views.State) -> ListingsViewState | UnitsViewState | EventsViewState:
    if st.page == "events":
        return EventsViewState(filter=FT.output(FT.EventFilter, st.filters), columns=list(st.columns))
    cls = ListingsViewState if st.page == "listings" else UnitsViewState
    return cls(filter=FT.output(FT.ListingFilter, st.filters), q=st.q, sort=FT.sort_output(st.page, st.sort),
               columns=list(st.columns))  # fmt: skip


def _state_of(root: strawberry.Parent[views.View]) -> ViewState:
    """The saved state, typed by its page."""
    return view_state(root.state)


View = derive(views.View, exclude=("state",), extra={"state": strawberry.field(resolver=_state_of)})


# --- envelopes --------------------------------------------------------------------------------------------


@strawberry.type
class PageInfo:
    has_next_page: bool
    has_previous_page: bool
    start_cursor: str | None
    end_cursor: str | None


@strawberry.type
class Edge[T]:
    cursor: str
    node: T


@strawberry.type(description="One condition of the request's filter, in words: the operator `op` of field `field`.")
class Chip:
    field: FT.FilterFieldName
    op: FT.FilterOp
    label: str


@strawberry.type
class VerdictCounts:
    eligible: int
    possible: int
    excluded: int


@strawberry.type
class Connection[T]:
    edges: list[Edge[T]]
    page_info: PageInfo
    total: int = strawberry.field(description="Everything matching, across pages.")
    filters: list[Chip] = strawberry.field(description="The filter's conditions, in words.")


@strawberry.type
class ListingConnection(Connection[Listing]):
    verdict_counts: VerdictCounts = strawberry.field(description="Counts by verdict, ignoring a verdict filter.")
    hidden_gone: bool = strawberry.field(description="Removed listings are left out: no filter asks about removal.")


@strawberry.type
class UnitConnection(Connection[Unit]):
    verdict_counts: VerdictCounts = strawberry.field(description="Counts by verdict, ignoring a verdict filter.")
    hidden_gone: bool = strawberry.field(description="Units of removed listings are left out.")


@strawberry.type
class EventConnection(Connection[Event]):
    pass


@strawberry.type
class ValueCount:
    value: str | None = strawberry.field(
        description="As the filter takes it (an enum field's GraphQL name); null: empty."
    )
    label: str = strawberry.field(description="As the data has it.")
    count: int


@strawberry.type
class Values:
    enum: bool = strawberry.field(description="The field has few enough values to pick from.")
    counts: list[ValueCount]


@strawberry.type
class OpLabel:
    op: FT.FilterOp
    label: str


@strawberry.type
class ListingsPageMeta:
    fields: list[FilterField]
    columns: list[FT.ListingColumnChoice]
    default_columns: list[FT.ListingColumn]
    sorts: list[FT.ListingSortChoice]
    default_sort: list[FT.ListingSort]


@strawberry.type
class UnitsPageMeta:
    fields: list[FilterField]
    columns: list[FT.UnitColumnChoice]
    default_columns: list[FT.UnitColumn]
    sorts: list[FT.UnitSortChoice]
    default_sort: list[FT.UnitSort]


@strawberry.type
class EventsPageMeta:
    fields: list[FilterField]
    columns: list[FT.EventColumnChoice]
    default_columns: list[FT.EventColumn]


@strawberry.type
class Pages:
    listings: ListingsPageMeta
    units: UnitsPageMeta
    events: EventsPageMeta


@strawberry.type(description="The profile and the registries the app builds its pages from.")
class Meta:
    profile: Profile
    statuses: list[Status]
    verdicts: list[Verdict]
    ops: list[OpLabel]
    pages: Pages


@strawberry.type
class ApplicationPdf:
    n: int
    url: str
    form: str | None = strawberry.field(description="The form housing-watch can fill, if it is one.")
    error: str | None = strawberry.field(description="Why the PDF could not be read.")


@strawberry.type
class Applications:
    pdfs: list[ApplicationPdf]
    ready: bool = strawberry.field(description="An applicant file is configured, so known forms can be filled.")


@strawberry.type
class Digest:
    subject: str
    text: str
    events: int


async def _digest(info: strawberry.Info) -> Digest | None:
    """The digest email that would go out next; null when nothing is pending."""
    built = await run_in_threadpool(digest.build, info.context["engine"], info.context["profile"])
    return Digest(subject=built[0], text=built[1], events=len(built[3])) if built else None


Overview = derive(queries.Overview, extra={"digest": strawberry.field(resolver=_digest)})
Fleet = derive(queries.Fleet)


@strawberry.input
class ListingsViewStateInput:
    filter: FT.ListingFilterInput | None = None
    q: str = ""
    sort: list[FT.ListingSortInput] | None = strawberry.field(default=None, description="null or []: the default.")
    columns: list[FT.ListingColumn] | None = strawberry.field(default=None, description="null or []: the default.")


@strawberry.input
class UnitsViewStateInput:
    filter: FT.ListingFilterInput | None = None
    q: str = ""
    sort: list[FT.UnitSortInput] | None = strawberry.field(default=None, description="null or []: the default.")
    columns: list[FT.UnitColumn] | None = strawberry.field(default=None, description="null or []: the default.")


@strawberry.input
class EventsViewStateInput:
    filter: FT.EventFilterInput | None = None
    columns: list[FT.EventColumn] | None = strawberry.field(default=None, description="null or []: the default.")


@strawberry.input(one_of=True, description="A page's state: set exactly one, the page it is for.")
class ViewStateInput:
    listings: ListingsViewStateInput | None = strawberry.UNSET
    units: UnitsViewStateInput | None = strawberry.UNSET
    events: EventsViewStateInput | None = strawberry.UNSET


@strawberry.input(one_of=True, description="A page's filter: set exactly one, the page it is for.")
class PageFilterInput:
    listings: FT.ListingFilterInput | None = strawberry.UNSET
    units: FT.ListingFilterInput | None = strawberry.UNSET
    events: FT.EventFilterInput | None = strawberry.UNSET


ViewMove = enum_type(views.Move, "One place up or down the page's views.", name="ViewMove")


@strawberry.input
class CreateViewInput:
    name: str
    state: ViewStateInput = strawberry.field(description="Its page is the view's page.")
    is_default: bool = False


@strawberry.input
class ChangeViewInput:
    id: strawberry.ID
    name: str | None = None
    state: ViewStateInput | None = strawberry.field(default=None, description="For the view's own page.")
    is_default: bool | None = None
    move: ViewMove | None = None


@strawberry.input
class ViewIdInput:
    id: strawberry.ID


@strawberry.input
class LinkTrackerInput:
    listing_key: strawberry.ID
    row_key: strawberry.ID | None = strawberry.field(default=None, description="null: the listing is in no row.")


@strawberry.input
class UnlinkTrackerInput:
    listing_key: strawberry.ID


@strawberry.type
class ViewPayload:
    view: View


@strawberry.type
class DeleteViewPayload:
    id: strawberry.ID


TrackerLinkPayload = derive(queries.TrackerLink, "TrackerLinkPayload")


# --- owner writes: sources, overrides, profile, the tracker sheet ---------------------------------------

_Borough, _Size = enum_type(models.Borough), enum_type(models.Size)
_PublishedStatus, _Kind = enum_type(models.PublishedStatus), enum_type(models.Kind)


@strawberry.input
class SourceInput:
    source: strawberry.ID


@strawberry.input
class PauseSourceInput:
    source: strawberry.ID
    paused: bool = strawberry.field(description="true: no more runs; false: resume, due at once.")


@strawberry.type
class SourcePayload:
    source: Source


@strawberry.input(description="Published fields to set; a field left out keeps the source's value, null clears it.")
class OverrideFieldsInput:
    title: str | None = strawberry.UNSET
    url: str | None = strawberry.UNSET
    address: str | None = strawberry.UNSET
    borough: _Borough | None = strawberry.UNSET  # pyright: ignore[reportInvalidTypeForm]
    status: _PublishedStatus | None = strawberry.UNSET  # pyright: ignore[reportInvalidTypeForm]
    kind: _Kind | None = strawberry.UNSET  # pyright: ignore[reportInvalidTypeForm]
    deadline: str | None = strawberry.field(default=strawberry.UNSET, description="ISO date (YYYY-MM-DD).")
    senior_only: bool | None = strawberry.UNSET
    notes: str | None = strawberry.UNSET


@strawberry.input
class BandInput:
    household: int
    income_min: int | None = None
    income_max: int | None = None


@strawberry.input
class UnitInput:
    size: _Size  # pyright: ignore[reportInvalidTypeForm]
    rent: float | None = None
    ami: int | None = None
    unit_no: str | None = None
    bands: list[BandInput] = strawberry.field(default_factory=list)
    set_aside: str | None = None
    available: bool | None = None
    chart_year: int | None = None


@strawberry.input
class SetOverrideInput:
    listing_key: strawberry.ID
    fields: OverrideFieldsInput | None = None
    units: list[UnitInput] | None = strawberry.field(
        default=None, description="Replaces the source's units outright; null keeps them."
    )
    reason: str | None = None


@strawberry.input
class ListingKeyInput:
    listing_key: strawberry.ID


@strawberry.type
class ListingPayload:
    listing: ListingDetail


@strawberry.input(description="Profile fields to change; one left out keeps its value.")
class SetProfileInput:
    household: int | None = None
    income: int | None = None
    sizes: list[_Size] | None = None  # pyright: ignore[reportInvalidTypeForm]
    excluded_boroughs: list[_Borough] | None = None  # pyright: ignore[reportInvalidTypeForm]


@strawberry.type
class ProfilePayload:
    profile: Profile
    custom: bool = strawberry.field(description="Set by the owner; false: the HW_* env vars' profile.")


@strawberry.input
class WriteTrackerInput:
    row_key: strawberry.ID
    status: str | None = strawberry.field(
        default=strawberry.UNSET, description="An application stage, or empty to clear; left out: unchanged."
    )
    notes: str | None = strawberry.field(default=strawberry.UNSET, description="Left out: unchanged.")


@strawberry.input
class AddTrackerRowInput:
    listing_key: strawberry.ID
    status: str | None = None
    notes: str | None = None


@strawberry.type
class TrackerWritePayload:
    tracker: Tracker
