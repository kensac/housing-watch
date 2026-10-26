"""The GraphQL schema (Strawberry) over queries.py, views.py and tracker.py: what the web app reads and writes.

Object types are derived from the read model's dataclasses (derive.py), never written out: a field added to
a dataclass is in the schema, and in `schema.graphql`, with no other change. Only API envelopes (connections,
inputs, payloads, meta) are declared here. Field names stay snake_case (auto_camel_case off).

Lists are Relay connections (`first`/`after`, opaque cursors). Filters, sorts and columns are typed inputs
derived from the registries (filter_types.py); a filter that is well-typed but meaningless (an empty `in`, a blank
`contains`) is refused with BAD_USER_INPUT. Closed value sets are enums, identifiers are `ID`. Errors carry
`extensions.code` (NOT_FOUND, BAD_USER_INPUT, FORBIDDEN, INTERNAL). Resolvers are async and run the blocking store
reads in a worker thread; mutations need the context's `request`, and refuse it unless same-origin, or a context
marked `trusted` by a caller that authenticated the user itself (the MCP server, behind OAuth).
"""

import base64
import enum
import functools
import inspect
from typing import Annotated

import strawberry
from starlette.concurrency import run_in_threadpool
from strawberry.dataloader import DataLoader
from strawberry.extensions import MaxAliasesLimiter, MaxTokensLimiter, QueryDepthLimiter
from strawberry.schema.config import StrawberryConfig

from .. import applications, criteria, digest, models, queries, tracker, views
from .. import commute as C
from .. import filters as F
from . import filter_types as FT
from .derive import derive, enum_type, gql_name

MAX_FIRST = 500


class Code(enum.StrEnum):
    NOT_FOUND = "NOT_FOUND"
    BAD_USER_INPUT = "BAD_USER_INPUT"
    FORBIDDEN = "FORBIDDEN"
    INTERNAL = "INTERNAL"


_STATUS = {Code.NOT_FOUND: 404, Code.BAD_USER_INPUT: 400, Code.FORBIDDEN: 403, Code.INTERNAL: 500}


class Problem(Exception):
    """A refusal with a human message: a GraphQL error with `extensions.code`, `{"error": ...}` on the PDF route."""

    def __init__(self, code: Code, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status or _STATUS[code]
        self.extensions = {"code": str(code)}


# --- enums: the domain's own constants ------------------------------------------------------------------

# The Literal aliases of the read model become enums wherever derive() meets them; these give them descriptions.
Verdict = enum_type(criteria.Verdict, "How a listing or unit fits the profile, judged at read time.")
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
Profile = derive(criteria.Profile)


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
    built = await run_in_threadpool(digest.build, *_ctx(info))
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


class ViewMove(enum.Enum):
    up = "up"
    down = "down"


strawberry.enum(ViewMove, description="One place up or down the page's views.")


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


@strawberry.type
class TrackerLinkPayload:
    listing_key: strawberry.ID
    row_key: strawberry.ID | None
    listing: Listing | None = strawberry.field(description="The listing as it reads after the change.")


# --- helpers --------------------------------------------------------------------------------------------


def _ctx(info: strawberry.Info):
    return info.context["engine"], info.context["profile"]


def blocking(fn):
    """`fn` as a coroutine that runs it in a worker thread: the store's reads block."""

    @functools.wraps(fn)
    async def run(*a, **kw):
        return await run_in_threadpool(fn, *a, **kw)

    return run


def query(fn=None, **kw):
    """A Query field resolved off the event loop, its docstring as the description."""
    if fn is None:
        return lambda f: query(f, **kw)
    return strawberry.field(resolver=blocking(fn), **{"description": inspect.getdoc(fn), **kw})


def mutation(name: str):
    return lambda fn: strawberry.mutation(resolver=blocking(fn), name=name, description=inspect.getdoc(fn))


def _conds(page: str, given) -> list[F.Cond]:
    """A filter input's conditions; one that means nothing is refused, saying why."""
    try:
        return F.from_tree(FT.tree(given), views.FIELDS[page])
    except F.FilterError as e:
        raise Problem(Code.BAD_USER_INPUT, str(e)) from None


def _chips(conds: list[F.Cond]) -> list[Chip]:
    return [Chip(field=c.field.name, op=c.op, label=c.label()) for c in conds]


def _view_id(id: strawberry.ID) -> int:
    if not str(id).isdigit():
        raise Problem(Code.NOT_FOUND, "No such view")
    return int(id)


def _cursor(kind: str, n: int) -> str:
    return base64.urlsafe_b64encode(f"{kind}:{n}".encode()).decode()


def _after(kind: str, cursor: str | None) -> int | None:
    """The position (listings, units) or event id a cursor stands for."""
    if cursor is None:
        return None
    try:
        k, n = base64.urlsafe_b64decode(cursor.encode()).decode().split(":")
        if k == kind:
            return int(n)
    except ValueError:
        pass
    raise Problem(Code.BAD_USER_INPUT, "That cursor is not from this list.")


def _first(first: int) -> int:
    if not 1 <= first <= MAX_FIRST:
        raise Problem(Code.BAD_USER_INPUT, f"first must be 1 to {MAX_FIRST}")
    return first


def _info(edges: list[Edge], more: bool, earlier: bool) -> PageInfo:
    return PageInfo(has_next_page=more, has_previous_page=earlier, start_cursor=edges[0].cursor if edges else None,
                    end_cursor=edges[-1].cursor if edges else None)  # fmt: skip


def _list(cls, page: str, filter, q, sort, first, after, info, search):
    """A listings or units connection: position cursors over one filtered, sorted list."""
    engine, p = _ctx(info)
    conds = _conds(page, filter)
    start = (a + 1) if (a := _after(page, after)) is not None else 0
    res = search(engine, queries.ListingQuery(conds, q, FT.sorts(page, sort), start, _first(first)), p)
    edges = [Edge(cursor=_cursor(page, start + i), node=x) for i, x in enumerate(res.items)]
    return cls(
        edges=edges, page_info=_info(edges, res.next_offset is not None, start > 0), total=res.total,
        verdict_counts=VerdictCounts(**{v: res.verdict_counts.get(v, 0) for v in queries.VERDICTS}),
        filters=_chips(conds), hidden_gone=queries.hides_gone(conds),
    )  # fmt: skip


def _state(st: ViewStateInput) -> views.State:
    """The page's state from the one page set in the input."""
    page, given = next((k, v) for k in views.FIELDS if (v := getattr(st, k)) not in (None, strawberry.UNSET))
    conds = _conds(page, given.filter)
    try:
        return views.State(page, conds, getattr(given, "q", ""), FT.sorts(page, getattr(given, "sort", None))
                           if page != "events" else (), FT.values_of(given.columns))  # fmt: skip
    except views.ViewError as e:
        raise Problem(Code.BAD_USER_INPUT, str(e)) from None


def same_origin(req) -> None:
    src = req.headers.get("origin") or req.headers.get("referer") or ""
    host = src.split("://", 1)[-1].split("/", 1)[0]
    if not src or host != req.headers.get("host") or req.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        raise Problem(Code.FORBIDDEN, "cross-origin request refused")


def _write(info: strawberry.Info):
    """The engine for a mutation, after the same-origin check (none for a trusted context)."""
    if info.context.get("trusted"):
        return info.context["engine"]
    req = info.context.get("request")
    if req is None:
        raise Problem(Code.FORBIDDEN, "writes need a request")
    same_origin(req)
    return info.context["engine"]


def _summary(engine, key: str, p) -> queries.ListingSummary | None:
    return next((x for x in queries.judged(engine, p) if x.key == key), None)


# --- schema ---------------------------------------------------------------------------------------------

LIST = "`filter` has a field per filterable field, each holding its operators; `meta.pages` describes them."


@strawberry.type
class Query:
    @query
    def meta(self, info: strawberry.Info) -> Meta:
        """The profile and the registries the app builds its pages from."""

        def common(page: str) -> dict:
            col = FT.COLUMN_TYPES[page]
            return {"fields": list(views.FIELDS[page].values()),
                    "columns": [col(column=k, label=v) for k, v in views.COLUMNS[page].items()],
                    "default_columns": list(views.DEFAULT_COLUMNS[page])}  # fmt: skip

        def sorted_(page: str) -> dict:
            choice = FT.SORT_TYPES[page][1]
            return {"sorts": [choice(field=k, label=s.label, natural=s.natural) for k, s in views.SORTS[page].items()],
                    "default_sort": FT.sort_output(page, views.DEFAULT_SORT[page])}  # fmt: skip

        pages = Pages(listings=ListingsPageMeta(**common("listings"), **sorted_("listings")),
                      units=UnitsPageMeta(**common("units"), **sorted_("units")),
                      events=EventsPageMeta(**common("events")))  # fmt: skip
        return Meta(profile=_ctx(info)[1], statuses=list(queries.STATUSES), verdicts=list(queries.VERDICTS),
                    ops=[OpLabel(op=k, label=v) for k, v in F.OP_LABEL.items()], pages=pages)  # fmt: skip

    @query(description=f"Listings matching the filter and search, one per building or opportunity. {LIST}")
    def listings(self, info: strawberry.Info, filter: FT.ListingFilterInput | None = None, q: str = "",
                 sort: list[FT.ListingSortInput] | None = None, first: int = 200,
                 after: str | None = None) -> ListingConnection:  # fmt: skip
        return _list(ListingConnection, "listings", filter, q, sort, first, after, info, queries.search_listings)

    @query(
        description=f"Every unit or rent tier of the matching listings; unit conditions hold on its own unit. {LIST}"
    )
    def units(self, info: strawberry.Info, filter: FT.ListingFilterInput | None = None, q: str = "",
              sort: list[FT.UnitSortInput] | None = None, first: int = 200,
              after: str | None = None) -> UnitConnection:  # fmt: skip
        return _list(UnitConnection, "units", filter, q, sort, first, after, info, queries.search_units)

    @query
    def listing(self, info: strawberry.Info, key: strawberry.ID) -> ListingDetail:
        """One listing with every unit, its notes and its history."""
        engine, p = _ctx(info)
        d = queries.listing_detail(engine, key, p)
        if d is None:
            raise Problem(Code.NOT_FOUND, "This listing is not in the store.")
        return d

    @query(name="listingApplications")
    def listing_applications(self, info: strawberry.Info, key: strawberry.ID) -> Applications:
        """Each linked application PDF, fetched to see whether it is a form we can fill."""
        engine, p = _ctx(info)
        d = queries.listing_detail(engine, key, p)
        out = []
        for n, url in enumerate(applications.pdf_urls(d.notes) if d else []):
            try:
                form = applications.detect(applications.blank(url))
                out.append(ApplicationPdf(n=n, url=url, form=form.name if form else None, error=None))
            except Exception as e:  # noqa: BLE001 — a dead link is shown, not raised
                out.append(ApplicationPdf(n=n, url=url, form=None, error=str(e)))
        return Applications(pdfs=out, ready=applications.applicant() is not None)

    @query
    def values(self, info: strawberry.Info, field: FT.FilterFieldName, filter: PageFilterInput,
               q: str = "") -> Values:  # fmt: skip
        """Distinct values of a field with counts under a page's filter (send it without the condition being
        edited); `enum` says whether the field has few enough values to pick from."""
        engine, p = _ctx(info)
        page, given = next((k, v) for k in views.FIELDS if (v := getattr(filter, k)) not in (None, strawberry.UNSET))
        fld = views.FIELDS[page].get(field.value)
        if fld is None:
            raise Problem(Code.BAD_USER_INPUT, f"{page} has no field {field.name}")
        conds = _conds(page, given)
        is_enum = queries.is_enum(engine, fld, p)
        counts = queries.value_counts(engine, page, fld, conds, q, p) if is_enum else []
        name = gql_name if fld.type == "enum" else str
        return Values(enum=is_enum, counts=[ValueCount(value=None if v in (None, "") else name(v),
                                                       label=v or "(empty)", count=n) for v, n in counts])  # fmt: skip

    @query
    def events(self, info: strawberry.Info, filter: FT.EventFilterInput | None = None, first: int = 200,
               after: str | None = None) -> EventConnection:  # fmt: skip
        """What happened, newest first. Listing conditions (`listing`) apply through the event's listing."""
        engine, p = _ctx(info)
        conds = _conds("events", filter)
        page = queries.events(engine, conds, before=_after("events", after), limit=_first(first), p=p)
        edges = [Edge(cursor=_cursor("events", e.id), node=e) for e in page.items]
        return EventConnection(edges=edges, page_info=_info(edges, page.next_before is not None, after is not None),
                               total=page.total, filters=_chips(conds))  # fmt: skip

    @query
    def overview(self, info: strawberry.Info) -> Overview:
        """Counts by verdict and status, eligible listings open now, recent events and failing sources."""
        return queries.overview(*_ctx(info))

    @query
    def sources(self, info: strawberry.Info) -> list[Source]:
        """Every source with its health, schedule and listing counts."""
        return queries.sources(*_ctx(info))

    @query
    def fleet(self, info: strawberry.Info) -> Fleet:
        """Procrastinate's workers, queues and failed jobs, and the hosts that ran sources."""
        return queries.fleet(_ctx(info)[0])

    @query
    def tracker(self, info: strawberry.Info) -> Tracker:
        """Every tracker sheet row with its stage history and the listings matched to it."""
        return queries.tracker_board(*_ctx(info))

    @query
    def views(self, info: strawberry.Info) -> list[View]:
        """Every saved view with its current match count."""
        return views.with_counts(*_ctx(info))


@strawberry.type
class Mutation:
    @mutation("createView")
    def create_view(self, info: strawberry.Info, input: CreateViewInput) -> ViewPayload:
        """Save a view of a page."""
        engine = _write(info)
        st = _state(input.state)
        try:
            return ViewPayload(view=views.create(engine, st, input.name, input.is_default))
        except views.ViewError as e:
            raise Problem(Code.BAD_USER_INPUT, str(e)) from None

    @mutation("changeView")
    def change_view(self, info: strawberry.Info, input: ChangeViewInput) -> ViewPayload:
        """Rename, replace the state, set or clear the default, or move it one place."""
        engine = _write(info)
        vid = _view_id(input.id)
        v = views.get(engine, vid)
        if v is None:
            raise Problem(Code.NOT_FOUND, "No such view")
        kw: dict = {}
        if input.name is not None:
            kw["name"] = input.name
        if input.state is not None:
            kw["state"] = _state(input.state)
        if input.is_default is not None:
            kw["is_default"] = input.is_default
        if input.move is not None:
            kw["move"] = -1 if input.move == ViewMove.up else 1
        try:
            return ViewPayload(view=views.change(engine, vid, **kw))
        except views.ViewError as e:
            raise Problem(Code.BAD_USER_INPUT, str(e)) from None

    @mutation("duplicateView")
    def duplicate_view(self, info: strawberry.Info, input: ViewIdInput) -> ViewPayload:
        """A copy of a view, named "<name> (copy)"."""
        v = views.duplicate(_write(info), _view_id(input.id))
        if v is None:
            raise Problem(Code.NOT_FOUND, "No such view")
        return ViewPayload(view=v)

    @mutation("deleteView")
    def delete_view(self, info: strawberry.Info, input: ViewIdInput) -> DeleteViewPayload:
        if not views.delete(_write(info), _view_id(input.id)):
            raise Problem(Code.NOT_FOUND, "No such view")
        return DeleteViewPayload(id=input.id)

    @mutation("linkTracker")
    def link_tracker(self, info: strawberry.Info, input: LinkTrackerInput) -> TrackerLinkPayload:
        """Link a listing to a sheet row, or mark it as in no row (row_key null)."""
        engine, p = _write(info), info.context["profile"]
        if not input.listing_key or _summary(engine, input.listing_key, p) is None:
            raise Problem(Code.NOT_FOUND, "No such listing")
        if input.row_key is not None and input.row_key not in {r.row_key for r in tracker.load(engine)[0]}:
            raise Problem(Code.NOT_FOUND, "No such tracker row")
        tracker.set_link(engine, input.listing_key, input.row_key)
        return TrackerLinkPayload(listing_key=input.listing_key, row_key=input.row_key,
                                  listing=_summary(engine, input.listing_key, p))  # fmt: skip

    @mutation("unlinkTracker")
    def unlink_tracker(self, info: strawberry.Info, input: UnlinkTrackerInput) -> TrackerLinkPayload:
        """Drop a manual link: the listing goes back to automatic matching."""
        engine = _write(info)
        if not tracker.clear_link(engine, input.listing_key):
            raise Problem(Code.NOT_FOUND, "No manual link for that listing")
        x = _summary(engine, input.listing_key, info.context["profile"])
        row = tracker.row_key(x.tracker_row) if x and x.tracker_row else None
        return TrackerLinkPayload(listing_key=input.listing_key, row_key=row, listing=x)


class Loaders:
    """The request's DataLoaders: one batched read per kind, however many rows ask."""

    def __init__(self, engine, profile):
        self.engine, self.profile = engine, profile
        self.listing = DataLoader(load_fn=self._listings)

    async def _listings(self, keys: list[str]) -> list[queries.ListingSummary | None]:
        by = await run_in_threadpool(lambda: {x.key: x for x in queries.judged(self.engine, self.profile)})
        return [by.get(k) for k in keys]


def context(engine, profile, request=None, trusted: bool = False) -> dict:
    """What resolvers read: the store, the profile, the request (for writes) and the DataLoaders. `trusted`: the
    caller authenticated the user, so writes skip the browser's same-origin check."""
    return {"engine": engine, "profile": profile, "request": request, "trusted": trusted,
            "loaders": Loaders(engine, profile)}  # fmt: skip


class Schema(strawberry.Schema):
    def process_errors(self, errors, execution_context=None):
        """Give every error a code; log only the ones that are bugs."""
        bugs = []
        for e in errors:
            orig = e.original_error
            if isinstance(orig, Problem):
                code = orig.code
            elif not e.path:  # not from a resolver: the document or its variables (syntax, validation, limits)
                code = Code.BAD_USER_INPUT
            else:
                code = Code.INTERNAL
                bugs.append(e)
            e.extensions = {**(e.extensions or {}), "code": str(code)}
        if bugs:
            super().process_errors(bugs, execution_context)


# Introspection stays on (GraphiQL, the MCP tool); these bound what one document can ask for.
LIMITS = [
    lambda: QueryDepthLimiter(max_depth=10),
    lambda: MaxTokensLimiter(max_token_count=2000),
    lambda: MaxAliasesLimiter(max_alias_count=15),
]
schema = Schema(query=Query, mutation=Mutation, extensions=LIMITS, config=StrawberryConfig(auto_camel_case=False))
