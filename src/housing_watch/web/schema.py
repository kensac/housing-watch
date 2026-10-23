"""The GraphQL schema (Strawberry) over queries.py, views.py and tracker.py: what the web app reads and writes.

Field names stay snake_case (auto_camel_case off). Filters are `[String!]` in the URL grammar of filters.py; one
that does not parse is reported in `problems`, not raised. Resolvers take the engine and profile from the
context; mutations also need its `request`, and refuse it unless same-origin.
"""

import dataclasses
from datetime import datetime

import strawberry
from starlette.concurrency import run_in_threadpool
from strawberry.scalars import JSON
from strawberry.schema.config import StrawberryConfig

from .. import applications, digest, queries, tracker, views
from .. import filters as F

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


class Problem(Exception):
    """A refusal with a human message: a GraphQL error here, `{"error": ...}` on the PDF route."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _mk(cls, src, **over):
    """An instance of the Strawberry type `cls` from a dataclass or dict, taking the fields it declares."""
    get = src.get if isinstance(src, dict) else (lambda k: getattr(src, k, None))
    return cls(**{f.name: over[f.name] if f.name in over else get(f.name) for f in dataclasses.fields(cls)})


# --- types ----------------------------------------------------------------------------------------------


@strawberry.type
class Profile:
    household: int
    income: int
    sizes: list[str]
    excluded_boroughs: list[str]


@strawberry.type
class FieldMeta:
    name: str
    label: str
    group: str
    type: str
    ops: list[str]
    money: bool
    choices: list[str]


@strawberry.type
class PageMeta:
    fields: list[FieldMeta]
    columns: JSON
    default_columns: list[str]
    default_sort: str
    sorts: JSON


@strawberry.type
class Pages:
    listings: PageMeta
    units: PageMeta
    events: PageMeta


@strawberry.type
class Meta:
    profile: Profile
    statuses: list[str]
    verdicts: list[str]
    op_labels: JSON
    pages: Pages


@strawberry.type
class Chip:
    i: int
    f: str
    label: str
    field: str
    op: str
    values: list[str]


@strawberry.type
class VerdictCounts:
    eligible: int = 0
    possible: int = 0
    excluded: int = 0


@strawberry.type
class Listing:
    key: str
    source: str
    title: str
    url: str
    address: str | None
    borough: str | None
    status: str
    kind: str
    deadline: str | None
    senior_only: bool
    first_seen: datetime
    last_changed: datetime
    gone_since: datetime | None
    verdict: str
    reason: str
    units: int
    eligible_units: int
    sizes: list[str]
    rent_min: float | None
    rent_max: float | None
    ami_min: int | None
    ami_max: int | None
    commute: int | None
    commute_route: JSON | None
    application: str | None
    application_group: str | None
    application_since: datetime | None
    application_since_baseline: bool
    application_notes: str | None
    tracker_row: str | None
    tracker_match: str | None


@strawberry.type
class Unit:
    id: int
    key: str
    source: str
    title: str
    url: str
    address: str | None
    borough: str | None
    status: str
    kind: str
    deadline: str | None
    first_seen: datetime
    last_changed: datetime
    gone_since: datetime | None
    listing_verdict: str
    size: str
    unit_no: str | None
    rent: float | None
    ami: int | None
    set_aside: str | None
    available: bool | None
    chart_year: int | None
    income_min: int | None
    income_max: int | None
    verdict: str
    reason: str
    commute: int | None
    application: str | None
    application_group: str | None
    application_since: datetime | None
    application_since_baseline: bool


@strawberry.type
class ListPage[T]:
    items: list[T]
    total: int
    next_offset: int | None
    verdict_counts: VerdictCounts
    filters: list[Chip]
    problems: list[str]
    hidden_gone: bool


@strawberry.type
class Event:
    id: int
    at: datetime
    source: str
    key: str | None
    type: str
    summary: str | None
    detail: JSON | None
    notified: datetime | None
    baseline: bool
    title: str | None
    verdict: str | None


@strawberry.type
class EventPage:
    items: list[Event]
    total: int
    next_before: int | None
    filters: list[Chip]
    problems: list[str]


@strawberry.type
class DetailUnit:
    size: str
    unit_no: str | None
    rent: float | None
    ami: int | None
    set_aside: str | None
    available: bool | None
    bands: list[list[int | None]]  # [household, income min, income max]
    verdict: str
    reason: str
    chart_year: int | None


@strawberry.type
class Detail:
    summary: Listing
    notes: str | None
    ext_id: str
    last_seen: datetime
    units: list[DetailUnit]
    events: list[Event]
    household: int
    pdfs: list[str]


@strawberry.type
class ApplicationPdf:
    n: int
    url: str
    form: str | None
    error: str | None


@strawberry.type
class Applications:
    pdfs: list[ApplicationPdf]
    ready: bool


@strawberry.type
class ValueCount:
    value: str | None
    count: int


@strawberry.type
class Values:
    enum: bool
    counts: list[ValueCount]


@strawberry.type
class Digest:
    subject: str
    text: str
    events: int


@strawberry.type
class Overview:
    matrix: JSON  # verdict -> status -> count
    eligible_open: int
    eligible_live: int
    new_24h: int
    new_7d: int
    baseline_24h: int
    changed_24h: int
    changed_7d: int
    failing: list[str]
    last_digest: datetime | None
    pending: int
    eligible_now: list[Listing]
    recent: list[Event]
    total: int
    n_sources: int
    digest: Digest | None


@strawberry.type
class Source:
    source: str
    title: str | None
    url: str | None
    every_hours: float | None
    health: str
    last_ok: datetime | None
    last_attempt: datetime | None
    failing_since: datetime | None
    consecutive_failures: int
    next_due: datetime | None
    last_duration: float | None
    last_error: str | None
    last_events: int | None
    listings: int
    eligible: int
    possible: int
    workers: list[str]


@strawberry.type
class FleetWorker:
    name: str
    queues: list[str]
    concurrency: int
    started: datetime
    last_seen: datetime
    age: float
    doing: list[str]


@strawberry.type
class FleetFailure:
    id: int
    queue_name: str
    task_name: str
    args: JSON
    attempts: int
    at: datetime | None
    error: str | None


@strawberry.type
class FleetHost:
    name: str
    last_run: datetime
    runs_24h: int
    fails_24h: int
    sources: int


@strawberry.type
class Fleet:
    available: bool
    workers: list[FleetWorker]
    queues: JSON  # queue -> status -> count
    failures: list[FleetFailure]
    hosts: list[FleetHost]


@strawberry.type
class StageStep:
    at: datetime
    stage_from: str | None
    stage_to: str | None
    group: str | None
    baseline: bool


@strawberry.type
class TrackerListing:
    key: str
    title: str
    verdict: str
    status: str
    how: str | None
    commute: int | None


@strawberry.type
class TrackerRow:
    row_key: str
    position: int
    property: str
    status: str | None
    stage: str | None
    group: str | None
    stage_since: datetime | None
    stage_since_baseline: bool
    events: list[StageStep]
    notes: str | None
    source_url: str | None
    page_url: str | None
    cells: JSON
    listings: list[TrackerListing]


@strawberry.type
class Tracker:
    configured: bool
    synced: datetime | None
    rows: list[TrackerRow]


@strawberry.type
class TrackerLink:
    listing_key: str
    row_key: str | None


@strawberry.type
class ViewState:
    f: list[str]
    q: str
    sort: str
    columns: list[str]


@strawberry.type
class View:
    id: int
    page: str
    name: str
    position: int
    is_default: bool
    warnings: list[str]
    count: int | None
    updated: datetime | None
    state: ViewState


@strawberry.input
class ViewStateInput:
    f: list[str] | None = None
    q: str | None = None
    sort: str | None = None
    columns: list[str] | None = None


# --- helpers --------------------------------------------------------------------------------------------


def _ctx(info: strawberry.Info):
    return info.context["engine"], info.context["profile"]


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


def _listing(x: queries.ListingSummary) -> Listing:
    return _mk(Listing, x)


def _event(e: queries.EventView) -> Event:
    return _mk(Event, e)


def _view(v: views.View) -> View:
    st = v.state
    state = ViewState(f=[c.url() for c in st.filters], q=st.q, sort=st.sort, columns=list(st.columns))
    return _mk(View, v, state=state)


def _state(page: str, st: ViewStateInput | None) -> views.State:
    if page not in views.FIELDS:
        raise Problem(400, f"Unknown page “{page}”")
    st = st or ViewStateInput()
    conds, problems = _conds(page, st.f)
    if problems:
        raise Problem(400, "; ".join(problems))
    return views.State(page, conds, st.q or "", st.sort or "", tuple(st.columns or ()))


def same_origin(req) -> None:
    src = req.headers.get("origin") or req.headers.get("referer") or ""
    host = src.split("://", 1)[-1].split("/", 1)[0]
    if not src or host != req.headers.get("host") or req.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        raise Problem(403, "cross-origin request refused")


def _write(info: strawberry.Info):
    """The engine for a mutation, after the same-origin check."""
    req = info.context.get("request")
    if req is None:
        raise Problem(403, "writes need a request")
    same_origin(req)
    return info.context["engine"]


def _page(cls, page: str, f, q, sort, offset, limit, engine, profile, search):
    conds, problems = _conds(page, f)
    lq = queries.ListingQuery(conds, q, sort or views.DEFAULT_SORT[page], max(offset, 0), min(max(limit, 1), 500))
    res = search(engine, lq, profile)
    return ListPage(
        items=[_mk(cls, x) for x in res.items], total=res.total, next_offset=res.next_offset,
        verdict_counts=_mk(VerdictCounts, {k: 0 for k in queries.VERDICTS} | res.verdict_counts),
        filters=_chips(page, f), problems=problems, hidden_gone=queries.hides_gone(conds),
    )  # fmt: skip


# --- schema ---------------------------------------------------------------------------------------------


@strawberry.type
class Query:
    @strawberry.field
    def meta(self, info: strawberry.Info) -> Meta:
        p = _ctx(info)[1]
        pages = {
            page: PageMeta(
                fields=[FieldMeta(name=f.name, label=f.label, group=f.group, type=f.type, ops=list(F.OPS[f.type]),
                                  money=f.money, choices=list(f.choices)) for f in views.FIELDS[page].values()],
                columns=views.COLUMNS[page], default_columns=list(views.DEFAULT_COLUMNS[page]),
                default_sort=views.DEFAULT_SORT[page], sorts=SORTS.get(page, {}),
            )
            for page in views.FIELDS
        }  # fmt: skip
        return Meta(
            profile=Profile(household=p.household, income=p.income, sizes=sorted(p.sizes),
                            excluded_boroughs=sorted(p.excluded_boroughs)),
            statuses=list(queries.STATUSES), verdicts=list(queries.VERDICTS), op_labels=F.OP_LABEL,
            pages=Pages(**pages),
        )  # fmt: skip

    @strawberry.field
    def listings(self, info: strawberry.Info, f: list[str] | None = None, q: str = "", sort: str = "",
                 offset: int = 0, limit: int = 200) -> ListPage[Listing]:  # fmt: skip
        return _page(Listing, "listings", f, q, sort, offset, limit, *_ctx(info), queries.search_listings)

    @strawberry.field
    def units(self, info: strawberry.Info, f: list[str] | None = None, q: str = "", sort: str = "",
              offset: int = 0, limit: int = 200) -> ListPage[Unit]:  # fmt: skip
        return _page(Unit, "units", f, q, sort, offset, limit, *_ctx(info), queries.search_units)

    @strawberry.field
    def listing(self, info: strawberry.Info, key: str) -> Detail | None:
        engine, p = _ctx(info)
        d = queries.listing_detail(engine, key, p)
        if d is None:
            raise Problem(404, "This listing is not in the store.")
        return _mk(Detail, d, summary=_listing(d.summary), events=[_event(e) for e in d.events],
                   units=[_mk(DetailUnit, u) for u in d.units], pdfs=applications.pdf_urls(d.notes))  # fmt: skip

    @strawberry.field(name="listingApplications")
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

    @strawberry.field
    def values(self, info: strawberry.Info, page: str, field: str, f: list[str] | None = None, q: str = "") -> Values:
        """Distinct values of a field with counts under the other filters (send them without the one being
        edited); `enum` says whether the field has few enough values to pick from."""
        engine, p = _ctx(info)
        if page not in views.FIELDS or field not in views.FIELDS[page]:
            raise Problem(404, "Unknown field")
        fld = views.FIELDS[page][field]
        conds, _ = _conds(page, f)
        enum = queries.is_enum(engine, fld, p)
        counts = queries.value_counts(engine, page, fld, conds, q, p) if enum else []
        return Values(enum=enum, counts=[ValueCount(value=v, count=n) for v, n in counts])

    @strawberry.field
    def events(self, info: strawberry.Info, f: list[str] | None = None, before: int | None = None) -> EventPage:
        engine, p = _ctx(info)
        conds, problems = _conds("events", f)
        page = queries.events(engine, conds, before=before, p=p)
        return EventPage(items=[_event(e) for e in page.items], total=page.total, next_before=page.next_before,
                         filters=_chips("events", f), problems=problems)  # fmt: skip

    @strawberry.field
    def overview(self, info: strawberry.Info) -> Overview:
        engine, p = _ctx(info)
        o = queries.overview(engine, p)
        built = digest.build(engine, p)
        return _mk(Overview, o, eligible_now=[_listing(x) for x in o.eligible_now],
                   recent=[_event(e) for e in o.recent],
                   digest=Digest(subject=built[0], text=built[1], events=len(built[3])) if built else None)  # fmt: skip

    @strawberry.field
    def sources(self, info: strawberry.Info) -> list[Source]:
        return [_mk(Source, x) for x in queries.sources(*_ctx(info))]

    @strawberry.field
    def fleet(self, info: strawberry.Info) -> Fleet:
        fl = queries.fleet(_ctx(info)[0])
        return _mk(Fleet, fl, workers=[_mk(FleetWorker, w) for w in fl.workers],
                   failures=[_mk(FleetFailure, x) for x in fl.failures],
                   hosts=[_mk(FleetHost, h) for h in fl.hosts])  # fmt: skip

    @strawberry.field
    def tracker(self, info: strawberry.Info) -> Tracker:
        """Every tracker sheet row with its stage history and the listings matched to it (empty: unmatched, link
        one by hand), by stage then longest in it."""
        engine, p = _ctx(info)
        rows, _ = tracker.load(engine)
        steps = tracker.history(engine)
        by_row: dict[str, list[TrackerListing]] = {}
        for x in queries.judged(engine, p):
            if x.tracker_row:
                row_listing = _mk(TrackerListing, x, how=x.tracker_match)
                by_row.setdefault(tracker.row_key(x.tracker_row), []).append(row_listing)
        synced = max((r.synced for r in rows), default=None)

        def row(r) -> TrackerRow:
            st, hist = tracker.stage(r.status), steps.get(r.row_key, [])
            since = tracker.since(hist, r.status)
            return _mk(TrackerRow, r, stage=st, group=tracker.group(st), stage_since=since and since.at,
                       stage_since_baseline=bool(since and since.baseline),
                       events=[_mk(StageStep, e, group=tracker.group(e.stage_to)) for e in hist],
                       listings=by_row.get(r.row_key, []))  # fmt: skip

        out = [row(r) for r in rows]
        out.sort(key=lambda d: (tracker.rank(d.stage), d.stage_since is None, d.stage_since or synced, d.position))
        return Tracker(configured=bool(tracker.SHEET), synced=synced, rows=out)

    @strawberry.field
    def views(self, info: strawberry.Info) -> list[View]:
        return [_view(v) for v in views.with_counts(*_ctx(info))]


@strawberry.type
class Mutation:
    @strawberry.mutation(name="createView")
    def create_view(self, info: strawberry.Info, page: str, name: str, is_default: bool = False,
                    state: ViewStateInput | None = None) -> View:  # fmt: skip
        engine = _write(info)
        st = _state(page, state)
        try:
            return _view(views.create(engine, st, name, is_default))
        except views.ViewError as e:
            raise Problem(400, str(e)) from None

    @strawberry.mutation(name="changeView")
    def change_view(self, info: strawberry.Info, id: int, name: str | None = None, state: ViewStateInput | None = None,
                    is_default: bool | None = None, move: int | None = None) -> View:  # fmt: skip
        """Rename, replace the state, set or clear the default, or move it one place (move: -1 or 1)."""
        engine = _write(info)
        v = views.get(engine, id)
        if v is None:
            raise Problem(404, "No such view")
        kw: dict = {}
        if name is not None:
            kw["name"] = name
        if state is not None:
            kw["state"] = _state(v.page, state)
        if is_default is not None:
            kw["is_default"] = is_default
        if move in (-1, 1):
            kw["move"] = move
        try:
            return _view(views.change(engine, id, **kw))
        except views.ViewError as e:
            raise Problem(400, str(e)) from None

    @strawberry.mutation(name="duplicateView")
    def duplicate_view(self, info: strawberry.Info, id: int) -> View:
        v = views.duplicate(_write(info), id)
        if v is None:
            raise Problem(404, "No such view")
        return _view(v)

    @strawberry.mutation(name="deleteView")
    def delete_view(self, info: strawberry.Info, id: int) -> bool:
        if not views.delete(_write(info), id):
            raise Problem(404, "No such view")
        return True

    @strawberry.mutation(name="linkTracker")
    def link_tracker(self, info: strawberry.Info, listing_key: str, row_key: str | None = None) -> TrackerLink:
        """Link a listing to a sheet row (row_key), or mark it as no row (row_key null)."""
        engine = _write(info)
        p = info.context["profile"]
        if not listing_key or queries.listing_detail(engine, listing_key, p) is None:
            raise Problem(404, "No such listing")
        if row_key is not None and row_key not in {r.row_key for r in tracker.load(engine)[0]}:
            raise Problem(404, "No such tracker row")
        tracker.set_link(engine, listing_key, row_key)
        return TrackerLink(listing_key=listing_key, row_key=row_key)

    @strawberry.mutation(name="unlinkTracker")
    def unlink_tracker(self, info: strawberry.Info, listing_key: str) -> bool:
        """Drop a manual link: the listing goes back to automatic matching."""
        if not tracker.clear_link(_write(info), listing_key):
            raise Problem(404, "No manual link for that listing")
        return True


class Schema(strawberry.Schema):
    async def execute(self, *a, **kw):  # resolvers block on the store: run them off the event loop
        return await run_in_threadpool(self.execute_sync, *a, **kw)

    def process_errors(self, errors, execution_context=None):  # a Problem is an answer, not a crash to log
        rest = [e for e in errors if not isinstance(e.original_error, Problem)]
        if rest:
            super().process_errors(rest, execution_context)


schema = Schema(query=Query, mutation=Mutation, config=StrawberryConfig(auto_camel_case=False))
