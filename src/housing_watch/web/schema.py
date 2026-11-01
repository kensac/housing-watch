# pyright: reportInvalidTypeForm=false
# Annotations here name types built at import time from the registries, which a static checker cannot see.
"""The GraphQL schema (Strawberry) over queries/, views.py and tracker.py: what the web app reads and writes.

Its types are in api_types.py. Field names stay snake_case (auto_camel_case off).

Lists are Relay connections (`first`/`after`, opaque cursors). Filters, sorts and columns are typed inputs
derived from the registries (filter_types.py); a filter that is well-typed but meaningless (an empty `in`, a blank
`contains`) is refused with BAD_USER_INPUT. Closed value sets are enums, identifiers are `ID`. Errors carry
`extensions.code` (NOT_FOUND, BAD_USER_INPUT, FORBIDDEN, INTERNAL); the domain's refusals (models.NotFound,
views.ViewError, filters.FilterError) get theirs in Schema.process_errors, so resolvers stay thin. Resolvers are
async and run the blocking store reads in a worker thread; mutations need the context's `request`, and refuse it
unless same-origin, or a context marked `trusted` by a caller that authenticated the user itself (the MCP server's
`graphql_mutation`, behind OAuth). The web app's /graphql never marks it.
"""

import base64
import enum
import functools
import inspect

import strawberry
from starlette.concurrency import run_in_threadpool
from strawberry.dataloader import DataLoader
from strawberry.extensions import MaxAliasesLimiter, MaxTokensLimiter, QueryDepthLimiter
from strawberry.schema.config import StrawberryConfig

from .. import applications, models, queries, tracker, views
from .. import filters as F
from . import filter_types as FT
from .api_types import (
    ApplicationPdf,
    Applications,
    ChangeViewInput,
    Chip,
    CreateViewInput,
    DeleteViewPayload,
    Edge,
    EventConnection,
    EventsPageMeta,
    Fleet,
    LinkTrackerInput,
    ListingConnection,
    ListingDetail,
    ListingsPageMeta,
    Meta,
    OpLabel,
    Overview,
    PageFilterInput,
    PageInfo,
    Pages,
    Source,
    Tracker,
    TrackerLinkPayload,
    UnitConnection,
    UnitsPageMeta,
    UnlinkTrackerInput,
    ValueCount,
    Values,
    VerdictCounts,
    View,
    ViewIdInput,
    ViewPayload,
    ViewStateInput,
)
from .derive import gql_name

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


def _chips(conds: list[F.Cond]) -> list[Chip]:
    return [Chip(field=c.field.name, op=c.op, label=c.label()) for c in conds]


def _view_id(id: strawberry.ID) -> int:
    if not str(id).isdigit():
        raise models.NotFound("No such view")
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
    conds = views.conds(page, FT.tree(filter))
    start = (a + 1) if (a := _after(page, after)) is not None else 0
    res = search(engine, queries.ListingQuery(conds, q, FT.sorts(page, sort), start, _first(first)), p)
    edges = [Edge(cursor=_cursor(page, start + i), node=x) for i, x in enumerate(res.items)]
    return cls(
        edges=edges, page_info=_info(edges, res.next_offset is not None, start > 0), total=res.total,
        verdict_counts=VerdictCounts(**res.verdict_counts),
        filters=_chips(conds), hidden_gone=queries.hides_gone(conds),
    )  # fmt: skip


def _one_page(given: ViewStateInput | PageFilterInput) -> tuple[str, object]:
    """The page set in a `@oneOf` input (GraphQL ensures exactly one is), and its value."""
    return next((k, v) for k in views.FIELDS if (v := getattr(given, k)) not in (None, strawberry.UNSET))


def _state(st: ViewStateInput) -> views.State:
    """The page's state from the one page set in the input."""
    page, given = _one_page(st)
    return views.make_state(page, FT.tree(given.filter), getattr(given, "q", ""),
                            FT.sort_pairs(getattr(given, "sort", None)), FT.values_of(given.columns))  # fmt: skip


def same_origin(req) -> None:
    src = req.headers.get("origin") or req.headers.get("referer") or ""
    host = src.split("://", 1)[-1].split("/", 1)[0]
    if not src or host != req.headers.get("host") or req.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        raise Problem(Code.FORBIDDEN, "cross-origin request refused")


def _write(info: strawberry.Info):
    """The engine and profile for a mutation, after the same-origin check (none for a trusted context)."""
    if info.context.get("trusted") is not True:
        req = info.context.get("request")
        if req is None:
            raise Problem(Code.FORBIDDEN, "writes need a request")
        same_origin(req)
    return _ctx(info)


# --- schema ---------------------------------------------------------------------------------------------

LIST = "`filter` has a field per filterable field, each holding its operators; `meta.pages` describes them."


@strawberry.type
class Query:
    @query
    def meta(self, info: strawberry.Info) -> Meta:
        """The profile and the registries the app builds its pages from."""

        def fields_and_columns(page: str) -> dict:
            col = FT.COLUMN_TYPES[page]
            return {"fields": list(views.FIELDS[page].values()),
                    "columns": [col(column=k, label=v) for k, v in views.COLUMNS[page].items()],
                    "default_columns": list(views.DEFAULT_COLUMNS[page])}  # fmt: skip

        def sorting(page: str) -> dict:
            choice = FT.SORT_TYPES[page][1]
            return {"sorts": [choice(field=k, label=s.label, natural=s.natural) for k, s in views.SORTS[page].items()],
                    "default_sort": FT.sort_output(page, views.DEFAULT_SORT[page])}  # fmt: skip

        pages = Pages(listings=ListingsPageMeta(**fields_and_columns("listings"), **sorting("listings")),
                      units=UnitsPageMeta(**fields_and_columns("units"), **sorting("units")),
                      events=EventsPageMeta(**fields_and_columns("events")))  # fmt: skip
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
            raise models.NotFound("This listing is not in the store.")
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
        page, given = _one_page(filter)
        fld = views.FIELDS[page].get(field.value)
        if fld is None:
            raise Problem(Code.BAD_USER_INPUT, f"{page} has no field {field.name}")
        conds = views.conds(page, FT.tree(given))
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
        conds = views.conds("events", FT.tree(filter))
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
    """Thin adapters over views.py and tracker.py; Schema.process_errors gives their refusals a code."""

    @mutation("createView")
    def create_view(self, info: strawberry.Info, input: CreateViewInput) -> ViewPayload:
        """Save a view of a page."""
        engine, p = _write(info)
        return ViewPayload(view=views.create(engine, _state(input.state), input.name, input.is_default, p))

    @mutation("changeView")
    def change_view(self, info: strawberry.Info, input: ChangeViewInput) -> ViewPayload:
        """Rename, replace the state, set or clear the default, or move it one place."""
        engine, p = _write(info)
        st = _state(input.state) if input.state is not None else None
        move = views.MOVES[input.move.value] if input.move else 0
        return ViewPayload(view=views.change(engine, _view_id(input.id), name=input.name, state=st,
                                             is_default=input.is_default, move=move, p=p))  # fmt: skip

    @mutation("duplicateView")
    def duplicate_view(self, info: strawberry.Info, input: ViewIdInput) -> ViewPayload:
        """A copy of a view, named "<name> (copy)" (the name shortened to fit)."""
        engine, p = _write(info)
        return ViewPayload(view=views.duplicate(engine, _view_id(input.id), p))

    @mutation("deleteView")
    def delete_view(self, info: strawberry.Info, input: ViewIdInput) -> DeleteViewPayload:
        views.delete(_write(info)[0], _view_id(input.id))
        return DeleteViewPayload(id=input.id)

    @mutation("linkTracker")
    def link_tracker(self, info: strawberry.Info, input: LinkTrackerInput) -> TrackerLinkPayload:
        """Link a listing to a sheet row, or mark it as in no row (row_key null)."""
        engine, p = _write(info)
        tracker.link(engine, input.listing_key, input.row_key)
        return queries.tracker_link(engine, input.listing_key, p)

    @mutation("unlinkTracker")
    def unlink_tracker(self, info: strawberry.Info, input: UnlinkTrackerInput) -> TrackerLinkPayload:
        """Drop a manual link: the listing goes back to automatic matching."""
        engine, p = _write(info)
        tracker.unlink(engine, input.listing_key)
        return queries.tracker_link(engine, input.listing_key, p)


class Loaders:
    """The request's DataLoaders: one batched read per kind, however many rows ask."""

    def __init__(self, engine, profile):
        self.engine, self.profile = engine, profile
        self.listing = DataLoader(load_fn=self._listings)

    async def _listings(self, keys: list[str]) -> list[queries.ListingSummary | None]:
        by = await run_in_threadpool(queries.judged_by_key, self.engine, self.profile)
        return [by.get(k) for k in keys]


def context(engine, profile, request=None, *, trusted: bool = False) -> dict:
    """What resolvers read: the store, the profile, the request (for writes) and the DataLoaders. `trusted`: the
    caller authenticated the user, so writes skip the browser's same-origin check."""
    return {"engine": engine, "profile": profile, "request": request, "trusted": trusted,
            "loaders": Loaders(engine, profile)}  # fmt: skip


class Schema(strawberry.Schema):
    def process_errors(self, errors, execution_context=None):
        """Give every error a code; log only the ones that are bugs, and tell the client no more than that."""
        bugs = []
        for e in errors:
            orig = e.original_error
            if isinstance(orig, Problem):
                code = orig.code
            elif isinstance(orig, models.NotFound):
                code = Code.NOT_FOUND
            elif isinstance(orig, (views.ViewError, F.FilterError)):
                code = Code.BAD_USER_INPUT
            elif not e.path:  # not from a resolver: the document or its variables (syntax, validation, limits)
                code = Code.BAD_USER_INPUT
            else:
                code = Code.INTERNAL
                bugs.append(e)
            e.extensions = {**(e.extensions or {}), "code": str(code)}
        if bugs:
            super().process_errors(bugs, execution_context)  # the log keeps the message and the trace
        for e in bugs:
            e.message = "Internal error"


# Introspection stays on (GraphiQL, the MCP tool); these bound what one document can ask for.
LIMITS = [
    lambda: QueryDepthLimiter(max_depth=10),
    lambda: MaxTokensLimiter(max_token_count=2000),
    lambda: MaxAliasesLimiter(max_alias_count=15),
]
schema = Schema(query=Query, mutation=Mutation, extensions=LIMITS, config=StrawberryConfig(auto_camel_case=False))
