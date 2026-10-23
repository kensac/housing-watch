"""The GraphQL schema (Strawberry) over queries.py, views.py and tracker.py: what the web app reads and writes.

Object types are derived from the read model's dataclasses (derive.py), never written out: a field added to
a dataclass is in the schema, and in `schema.graphql`, with no other change. Only API envelopes (connections,
inputs, payloads, meta) are declared here. Field names stay snake_case (auto_camel_case off).

Lists are Relay connections (`first`/`after`, opaque cursors). Filters are `[String!]` in the URL grammar of
filters.py; one that does not parse is reported in `problems`, not raised. Errors carry `extensions.code`
(NOT_FOUND, BAD_USER_INPUT, FORBIDDEN, INTERNAL). Resolvers are async and run the blocking store reads in a
worker thread; mutations need the context's `request`, and refuse it unless same-origin.
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
from strawberry.scalars import JSON
from strawberry.schema.config import StrawberryConfig

from .. import applications, criteria, digest, models, queries, tracker, views
from .. import commute as C
from .. import filters as F
from .derive import derive, enum_of

SORTS = {
    "listings": {"verdict": "Verdict", "changed": "Last changed", "first_seen": "First seen",
                 "rent": "Lowest rent that fits", "commute": "Commute", "application": "Application stage",
                 "deadline": "Deadline", "title": "Title", "source": "Source", "borough": "Borough"},
    "units": {"verdict": "Verdict", "rent": "Rent", "commute": "Commute", "application": "Application stage",
              "ami": "AMI", "size": "Size",
              "income_min": "Income min", "income_max": "Income max", "changed": "Last changed",
              "first_seen": "First seen", "deadline": "Deadline", "title": "Building", "borough": "Borough",
              "source": "Source"},
}  # fmt: skip
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

Verdict = enum_of("Verdict", queries.VERDICTS, "How a listing or unit fits the profile, judged at read time.")
Status = enum_of("Status", queries.STATUSES, "A listing's status as published; gone once its source dropped it.")
EventType = enum_of("EventType", queries.EVENT_TYPES)
Page = enum_of("Page", views.FIELDS, "A list page of the app.")
StageGroup = enum_of("StageGroup", tracker.GROUPS, "Whose move an application stage is.")
Match = enum_of("TrackerMatch", tracker.MATCHES, "How a listing was matched to its tracker row.")
FilterOp = enum_of("FilterOp", F.OP_LABEL)
FieldType = enum_of("FieldType", F.OPS)
Health = enum_of("Health", queries.HEALTH)


# --- derived types ----------------------------------------------------------------------------------------

derive(models.Band, "IncomeBand")
derive(C.Route, "CommuteRoute")
Listing = derive(queries.ListingSummary, "Listing", description="A listing, summarized and judged for the profile.",
                 types={"verdict": Verdict, "status": Status, "application_group": StageGroup,
                        "tracker_match": Match})  # fmt: skip
Unit = derive(queries.UnitSummary, "Unit", types={"verdict": Verdict, "listing_verdict": Verdict, "status": Status,
                                                  "application_group": StageGroup})  # fmt: skip
derive(queries.UnitView, "DetailUnit", types={"verdict": Verdict})
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


Event = derive(queries.EventView, "Event", types={"type": EventType, "verdict": Verdict}, exclude=("detail",),
               extra={"detail": strawberry.field(resolver=_detail),
                      "listing": strawberry.field(resolver=_event_listing)})  # fmt: skip


def _pdfs(root: strawberry.Parent[queries.ListingDetail]) -> list[str]:
    """The application PDFs linked from the listing's notes."""
    return applications.pdf_urls(root.notes)


ListingDetail = derive(queries.ListingDetail, extra={"pdfs": strawberry.field(resolver=_pdfs)})
Source = derive(queries.SourceHealth, "Source", types={"health": Health})
derive(queries.FleetWorker)
derive(queries.FleetFailure, types={"args": JSON})  # a task's own kwargs: free-form by design
derive(queries.FleetHost)
derive(tracker.Step, "StageStep", props=("group",), types={"group": StageGroup})
derive(tracker.Cell, "TrackerCell")
derive(queries.TrackerRowView, "TrackerRow", types={"group": StageGroup})
Tracker = derive(queries.TrackerBoard, "Tracker")
Profile = derive(criteria.Profile)


def _ops(root: strawberry.Parent[F.Field]) -> list[FilterOp]:
    """The operators a filter on this field can use."""
    return list(F.OPS[root.type])


FilterField = derive(F.Field, "FilterField", exclude=("scope", "column", "order"), props=("money",),
                     types={"type": FieldType}, extra={"ops": strawberry.field(resolver=_ops)})  # fmt: skip


def _view_f(root: strawberry.Parent[views.State]) -> list[str]:
    """The filters, in the URL grammar."""
    return [c.url() for c in root.filters]


derive(views.State, "ViewState", exclude=("page", "filters"), extra={"f": strawberry.field(resolver=_view_f)})
View = derive(views.View, types={"page": Page}, description="A saved view: a named filter, search, sort and columns.")


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


@strawberry.type(description="A filter of the request that parsed, as a chip; `i` is its index in `f`.")
class Chip:
    i: int
    f: str
    label: str
    field: str
    op: FilterOp
    values: list[str]


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
    filters: list[Chip]
    problems: list[str] = strawberry.field(description="Why each filter that did not parse was ignored.")


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
    value: str | None
    count: int


@strawberry.type
class Values:
    enum: bool = strawberry.field(description="The field has few enough values to pick from.")
    counts: list[ValueCount]


@strawberry.type
class Choice:
    key: str
    label: str


@strawberry.type
class OpLabel:
    op: FilterOp
    label: str


@strawberry.type
class PageMeta:
    fields: list[FilterField]
    columns: list[Choice]
    default_columns: list[str]
    default_sort: str
    sorts: list[Choice]


@strawberry.type
class Pages:
    listings: PageMeta
    units: PageMeta
    events: PageMeta


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


@strawberry.type
class MatrixCell:
    verdict: Verdict
    status: Status
    count: int


@strawberry.type
class QueueCount:
    queue: str
    status: str
    count: int


def _matrix(root: strawberry.Parent[queries.Overview]) -> list[MatrixCell]:
    """Listed listings by verdict and status."""
    return [MatrixCell(verdict=v, status=s, count=n) for v, row in root.matrix.items() for s, n in row.items()]


async def _digest(info: strawberry.Info) -> Digest | None:
    """The digest email that would go out next; null when nothing is pending."""
    built = await run_in_threadpool(digest.build, *_ctx(info))
    return Digest(subject=built[0], text=built[1], events=len(built[3])) if built else None


Overview = derive(queries.Overview, exclude=("matrix",), extra={
    "matrix": strawberry.field(resolver=_matrix), "digest": strawberry.field(resolver=_digest)})  # fmt: skip


def _queues(root: strawberry.Parent[queries.Fleet]) -> list[QueueCount]:
    """Jobs by queue and status."""
    return [QueueCount(queue=q, status=s, count=n) for q, row in root.queues.items() for s, n in row.items()]


Fleet = derive(queries.Fleet, exclude=("queues",), extra={"queues": strawberry.field(resolver=_queues)})


@strawberry.input
class ViewStateInput:
    f: list[str] | None = None
    q: str | None = None
    sort: str | None = None
    columns: list[str] | None = None


@strawberry.input
class CreateViewInput:
    page: Page
    name: str
    is_default: bool = False
    state: ViewStateInput | None = None


@strawberry.input
class ChangeViewInput:
    id: int
    name: str | None = None
    state: ViewStateInput | None = None
    is_default: bool | None = None
    move: int | None = strawberry.field(default=None, description="-1 or 1: one place up or down.")


@strawberry.input
class ViewIdInput:
    id: int


@strawberry.input
class LinkTrackerInput:
    listing_key: str
    row_key: str | None = strawberry.field(default=None, description="null: the listing is in no row.")


@strawberry.input
class UnlinkTrackerInput:
    listing_key: str


@strawberry.type
class ViewPayload:
    view: View


@strawberry.type
class DeleteViewPayload:
    id: int


@strawberry.type
class TrackerLinkPayload:
    listing_key: str
    row_key: str | None
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


def _parse(page: str, raw: list[str] | None) -> tuple[list[tuple[int, F.Cond]], list[str]]:
    """Parse every filter, keeping its index in the request; one that does not parse is reported, not fatal."""
    out, problems = [], []
    for i, s in enumerate(raw or []):
        try:
            out.append((i, F.parse_one(s, views.FIELDS[page])))
        except F.FilterError as e:
            problems.append(str(e))
    return out, problems


def _conds(page: str, raw: list[str] | None) -> tuple[list[F.Cond], list[str]]:
    parsed, problems = _parse(page, raw)
    return [c for _, c in parsed], problems


def _chips(page: str, raw: list[str] | None) -> list[Chip]:
    return [Chip(i=i, f=c.url(), label=c.label(), field=c.field.name, op=c.op, values=list(c.raw))
            for i, c in _parse(page, raw)[0]]  # fmt: skip


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


def _list(cls, page: str, f, q, sort, first, after, info, search):
    """A listings or units connection: position cursors over one filtered, sorted list."""
    engine, p = _ctx(info)
    conds, problems = _conds(page, f)
    start = (a + 1) if (a := _after(page, after)) is not None else 0
    res = search(engine, queries.ListingQuery(conds, q, sort or views.DEFAULT_SORT[page], start, _first(first)), p)
    edges = [Edge(cursor=_cursor(page, start + i), node=x) for i, x in enumerate(res.items)]
    return cls(
        edges=edges, page_info=_info(edges, res.next_offset is not None, start > 0), total=res.total,
        verdict_counts=VerdictCounts(**{v: res.verdict_counts.get(v, 0) for v in queries.VERDICTS}),
        filters=_chips(page, f), problems=problems, hidden_gone=queries.hides_gone(conds),
    )  # fmt: skip


def _state(page: str, st: ViewStateInput | None) -> views.State:
    st = st or ViewStateInput()
    conds, problems = _conds(page, st.f)
    if problems:
        raise Problem(Code.BAD_USER_INPUT, "; ".join(problems))
    return views.State(page, conds, st.q or "", st.sort or "", tuple(st.columns or ()))


def same_origin(req) -> None:
    src = req.headers.get("origin") or req.headers.get("referer") or ""
    host = src.split("://", 1)[-1].split("/", 1)[0]
    if not src or host != req.headers.get("host") or req.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        raise Problem(Code.FORBIDDEN, "cross-origin request refused")


def _write(info: strawberry.Info):
    """The engine for a mutation, after the same-origin check."""
    req = info.context.get("request")
    if req is None:
        raise Problem(Code.FORBIDDEN, "writes need a request")
    same_origin(req)
    return info.context["engine"]


def _summary(engine, key: str, p) -> queries.ListingSummary | None:
    return next((x for x in queries.judged(engine, p) if x.key == key), None)


# --- schema ---------------------------------------------------------------------------------------------

LIST = "Filters (`f`) are `field.op.values` strings; `meta.pages` lists the fields and operators."


@strawberry.type
class Query:
    @query
    def meta(self, info: strawberry.Info) -> Meta:
        """The profile and the registries the app builds its pages from."""
        pages = {
            page: PageMeta(
                fields=list(views.FIELDS[page].values()),
                columns=[Choice(key=k, label=v) for k, v in views.COLUMNS[page].items()],
                default_columns=list(views.DEFAULT_COLUMNS[page]), default_sort=views.DEFAULT_SORT[page],
                sorts=[Choice(key=k, label=v) for k, v in SORTS.get(page, {}).items()],
            )
            for page in views.FIELDS
        }  # fmt: skip
        return Meta(profile=_ctx(info)[1], statuses=list(queries.STATUSES), verdicts=list(queries.VERDICTS),
                    ops=[OpLabel(op=k, label=v) for k, v in F.OP_LABEL.items()], pages=Pages(**pages))  # fmt: skip

    @query(description=f"Listings matching the filters and search, one per building or opportunity. {LIST}")
    def listings(self, info: strawberry.Info, f: list[str] | None = None, q: str = "", sort: str = "",
                 first: int = 200, after: str | None = None) -> ListingConnection:  # fmt: skip
        return _list(ListingConnection, "listings", f, q, sort, first, after, info, queries.search_listings)

    @query(description=f"Every unit or rent tier of the matching listings; unit filters hold on its own unit. {LIST}")
    def units(self, info: strawberry.Info, f: list[str] | None = None, q: str = "", sort: str = "",
              first: int = 200, after: str | None = None) -> UnitConnection:  # fmt: skip
        return _list(UnitConnection, "units", f, q, sort, first, after, info, queries.search_units)

    @query
    def listing(self, info: strawberry.Info, key: str) -> ListingDetail:
        """One listing with every unit, its notes and its history."""
        engine, p = _ctx(info)
        d = queries.listing_detail(engine, key, p)
        if d is None:
            raise Problem(Code.NOT_FOUND, "This listing is not in the store.")
        return d

    @query(name="listingApplications")
    def listing_applications(self, info: strawberry.Info, key: str) -> Applications:
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
    def values(self, info: strawberry.Info, page: Page, field: str, f: list[str] | None = None, q: str = "") -> Values:
        """Distinct values of a field with counts under the other filters (send them without the one being
        edited); `enum` says whether the field has few enough values to pick from."""
        engine, p = _ctx(info)
        if field not in views.FIELDS[page.value]:
            raise Problem(Code.NOT_FOUND, "Unknown field")
        fld = views.FIELDS[page.value][field]
        conds, _ = _conds(page.value, f)
        is_enum = queries.is_enum(engine, fld, p)
        counts = queries.value_counts(engine, page.value, fld, conds, q, p) if is_enum else []
        return Values(enum=is_enum, counts=[ValueCount(value=v, count=n) for v, n in counts])

    @query
    def events(self, info: strawberry.Info, f: list[str] | None = None, first: int = 200,
               after: str | None = None) -> EventConnection:  # fmt: skip
        """What happened, newest first. Listing filters apply through the event's listing."""
        engine, p = _ctx(info)
        conds, problems = _conds("events", f)
        page = queries.events(engine, conds, before=_after("events", after), limit=_first(first), p=p)
        edges = [Edge(cursor=_cursor("events", e.id), node=e) for e in page.items]
        return EventConnection(edges=edges, page_info=_info(edges, page.next_before is not None, after is not None),
                               total=page.total, filters=_chips("events", f), problems=problems)  # fmt: skip

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
        st = _state(input.page.value, input.state)
        try:
            return ViewPayload(view=views.create(engine, st, input.name, input.is_default))
        except views.ViewError as e:
            raise Problem(Code.BAD_USER_INPUT, str(e)) from None

    @mutation("changeView")
    def change_view(self, info: strawberry.Info, input: ChangeViewInput) -> ViewPayload:
        """Rename, replace the state, set or clear the default, or move it one place."""
        engine = _write(info)
        v = views.get(engine, input.id)
        if v is None:
            raise Problem(Code.NOT_FOUND, "No such view")
        kw: dict = {}
        if input.name is not None:
            kw["name"] = input.name
        if input.state is not None:
            kw["state"] = _state(v.page, input.state)
        if input.is_default is not None:
            kw["is_default"] = input.is_default
        if input.move in (-1, 1):
            kw["move"] = input.move
        try:
            return ViewPayload(view=views.change(engine, input.id, **kw))
        except views.ViewError as e:
            raise Problem(Code.BAD_USER_INPUT, str(e)) from None

    @mutation("duplicateView")
    def duplicate_view(self, info: strawberry.Info, input: ViewIdInput) -> ViewPayload:
        """A copy of a view, named "<name> (copy)"."""
        v = views.duplicate(_write(info), input.id)
        if v is None:
            raise Problem(Code.NOT_FOUND, "No such view")
        return ViewPayload(view=v)

    @mutation("deleteView")
    def delete_view(self, info: strawberry.Info, input: ViewIdInput) -> DeleteViewPayload:
        if not views.delete(_write(info), input.id):
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


def context(engine, profile, request=None) -> dict:
    """What resolvers read: the store, the profile, the request (for writes) and the DataLoaders."""
    return {"engine": engine, "profile": profile, "request": request, "loaders": Loaders(engine, profile)}


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
