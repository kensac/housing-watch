"""Read API over the store, for the UI and the digest. Returns plain dataclasses and dicts.

Verdicts are computed here, at read time, by criteria; nothing judged is ever stored. Every listing is
judged once per store version (a cheap aggregate over `listings`) and kept in memory, so the pages
filter and sort a few thousand listings in Python without reloading them.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload

from .criteria import ELIGIBLE, EXCLUDED, POSSIBLE, PROFILE, Profile, listing_verdict, unit_verdict
from .mapping import listing_from_row
from .models import Listing
from .orm import EventRow, ListingRow, RunRow, SourceStateRow, UnitRow

VERDICTS = (ELIGIBLE, POSSIBLE, EXCLUDED)
STATUSES = ("open", "waitlist", "upcoming", "closed", "gone")
LISTING_EVENTS = ("new", "reopened", "changed", "closed", "gone")
EVENT_TYPES = (*LISTING_EVENTS, "page_changed", "watching", "source_failed", "source_recovered")
SORTS = ("verdict", "changed", "first_seen", "rent", "deadline", "title", "source")  # "-x" flips the default
_NEWEST_FIRST = {"changed", "first_seen"}
_VRANK = {ELIGIBLE: 0, POSSIBLE: 1, EXCLUDED: 2}


def now() -> datetime:
    return datetime.now(UTC)


def is_baseline(detail: dict | None) -> bool:
    """Events from a source's first run (db.py marks them) are the starting inventory, not news."""
    return bool(detail) and str(detail.get("baseline")).lower() == "true"


# --- listings ------------------------------------------------------------------------------------------


@dataclass
class ListingSummary:
    key: str
    source: str
    title: str
    url: str
    address: str | None
    borough: str | None
    status: str  # as published, or "gone" once the source stopped listing it
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
    unit_sizes: frozenset[str] = field(default_factory=frozenset, repr=False)
    unit_rents: tuple[float, ...] = field(default=(), repr=False)
    haystack: str = field(default="", repr=False)  # lowercased text the search box matches


def _load(s: Session, *where) -> list[ListingRow]:
    q = select(ListingRow).options(selectinload(ListingRow.units).selectinload(UnitRow.bands))
    return list(s.scalars(q.where(*where)))


def _summary(r: ListingRow, lst: Listing, p: Profile) -> ListingSummary:
    v = listing_verdict(lst, p)
    rents = tuple(u.rent for u in lst.units if u.rent)
    amis = [u.ami for u in lst.units if u.ami]
    order = ["studio", "sro", "1br", "2br", "3br", "4br", "5br", "unknown"]
    sizes = sorted({u.size for u in lst.units}, key=lambda z: order.index(z) if z in order else 99)
    return ListingSummary(
        key=r.key,
        source=r.source,
        title=lst.title or r.key,
        url=lst.url,
        address=lst.address,
        borough=lst.borough,
        status="gone" if r.gone_since else lst.status,
        kind=lst.kind,
        deadline=lst.deadline,
        senior_only=lst.senior_only,
        first_seen=r.first_seen,
        last_changed=r.last_changed,
        gone_since=r.gone_since,
        verdict=v.verdict,
        reason=v.reason,
        units=len(lst.units),
        eligible_units=sum(1 for _, uv, _ in v.units if uv == ELIGIBLE),
        sizes=sizes,
        rent_min=min(rents) if rents else None,
        rent_max=max(rents) if rents else None,
        ami_min=min(amis) if amis else None,
        ami_max=max(amis) if amis else None,
        unit_sizes=frozenset(sizes),
        unit_rents=rents,
        haystack=" ".join(x for x in (r.key, lst.title, lst.address, lst.borough, lst.notes) if x).lower(),
    )


_cache: dict[tuple, tuple[tuple, list[ListingSummary]]] = {}
_lock = threading.Lock()


def judged(engine: Engine, p: Profile = PROFILE) -> list[ListingSummary]:
    """Every listing, summarized and judged; recomputed only when the listings table moved."""
    with Session(engine) as s:
        version = tuple(
            s.execute(
                select(
                    func.count(),
                    func.max(ListingRow.last_seen),
                    func.max(ListingRow.last_changed),
                    func.count(ListingRow.gone_since),
                )
            ).one()
        )
        ck = (str(engine.url), p)
        hit = _cache.get(ck)
        if hit and hit[0] == version:
            return hit[1]
        with _lock:
            out = [_summary(r, listing_from_row(r), p) for r in _load(s)]
            _cache[ck] = (version, out)
        return out


def parse_since(v: str | None) -> datetime | None:
    """ "24h", "7d", "30m" or an ISO date/time; None when empty or unparseable."""
    if not v:
        return None
    m = re.fullmatch(r"(\d+)\s*([mhd])", v.strip())
    if m:
        n, u = int(m[1]), m[2]
        return now() - timedelta(**{{"m": "minutes", "h": "hours", "d": "days"}[u]: n})
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


@dataclass
class ListingFilters:
    verdict: str = ""
    status: str = ""  # "" = everything still listed (not gone)
    source: str = ""
    borough: str = ""  # "unknown" matches listings with no borough
    size: str = ""
    max_rent: float | None = None
    q: str = ""
    since: str = ""  # changed since, see parse_since
    sort: str = "verdict"
    page: int = 1
    per_page: int = 100


@dataclass
class ListingPage:
    items: list[ListingSummary]
    total: int
    page: int
    pages: int
    verdict_counts: dict[str, int]  # over everything matching the other filters, for the verdict tabs
    sources: list[str]
    boroughs: list[str]


def _sort_key(sort: str):
    return {
        "verdict": lambda x: (_VRANK[x.verdict], -x.last_changed.timestamp()),
        "changed": lambda x: x.last_changed,
        "first_seen": lambda x: x.first_seen,
        "rent": lambda x: (x.rent_min is None, x.rent_min or 0),
        "deadline": lambda x: (x.deadline is None, x.deadline or ""),
        "title": lambda x: x.title.lower(),
        "source": lambda x: (x.source, x.title.lower()),
    }.get(sort, lambda x: (_VRANK[x.verdict], -x.last_changed.timestamp()))


def search_listings(engine: Engine, f: ListingFilters, p: Profile = PROFILE) -> ListingPage:
    allx = judged(engine, p)
    since = parse_since(f.since)
    q = f.q.strip().lower()

    def keep(x: ListingSummary) -> bool:
        if f.status == "gone":
            if x.status != "gone":
                return False
        elif f.status:
            if x.status != f.status:
                return False
        elif x.status == "gone":
            return False
        if f.source and x.source != f.source:
            return False
        if f.borough and (x.borough or "unknown") != f.borough:
            return False
        if f.size and f.size not in x.unit_sizes:
            return False
        if f.max_rent is not None and not any(r <= f.max_rent for r in x.unit_rents):
            return False
        if since and x.last_changed < since:
            return False
        return not q or all(w in x.haystack for w in q.split())

    base = [x for x in allx if keep(x)]
    counts = {v: 0 for v in VERDICTS}
    for x in base:
        counts[x.verdict] += 1
    rows = [x for x in base if not f.verdict or x.verdict == f.verdict]
    name = f.sort.lstrip("-")
    rows.sort(key=_sort_key(name), reverse=(name in _NEWEST_FIRST) != f.sort.startswith("-"))
    per = max(1, min(f.per_page, 500))
    pages = max(1, -(-len(rows) // per))
    page = min(max(1, f.page), pages)
    return ListingPage(
        items=rows[(page - 1) * per : page * per],
        total=len(rows),
        page=page,
        pages=pages,
        verdict_counts=counts,
        sources=sorted({x.source for x in allx}),
        boroughs=sorted({x.borough or "unknown" for x in allx}),
    )


@dataclass
class UnitView:
    size: str
    unit_no: str | None
    rent: float | None
    ami: int | None
    set_aside: str | None
    available: bool | None
    bands: list[tuple[int, int | None, int | None]]
    verdict: str
    reason: str


@dataclass
class EventView:
    id: int
    at: datetime
    source: str
    key: str | None
    type: str
    summary: str | None
    detail: dict | None
    notified: datetime | None
    baseline: bool
    title: str | None = None
    verdict: str | None = None


@dataclass
class ListingDetail:
    summary: ListingSummary
    notes: str | None
    ext_id: str
    last_seen: datetime
    units: list[UnitView]
    events: list[EventView]
    household: int


def _event_view(e: EventRow) -> EventView:
    return EventView(e.id, e.at, e.source, e.key, e.type, e.summary, e.detail, e.notified, is_baseline(e.detail))


def listing_detail(engine: Engine, key: str, p: Profile = PROFILE) -> ListingDetail | None:
    with Session(engine) as s:
        rows = _load(s, ListingRow.key == key)
        if not rows:
            return None
        r = rows[0]
        lst = listing_from_row(r)
        units = [
            UnitView(
                u.size,
                u.unit_no,
                u.rent,
                u.ami,
                u.set_aside,
                u.available,
                [(b.household, b.income_min, b.income_max) for b in u.bands],
                *unit_verdict(u, p),
            )
            for u in lst.units
        ]
        evs = s.scalars(select(EventRow).where(EventRow.key == key).order_by(EventRow.at.desc()).limit(50))
        return ListingDetail(
            summary=_summary(r, lst, p),
            notes=lst.notes,
            ext_id=r.ext_id,
            last_seen=r.last_seen,
            units=units,
            events=[_event_view(e) for e in evs],
            household=p.household,
        )


# --- events --------------------------------------------------------------------------------------------


@dataclass
class EventPage:
    items: list[EventView]
    next_before: int | None  # pass as `before` for the next (older) page
    type_counts: dict[str, int]


def _attach(engine: Engine, evs: list[EventView], p: Profile) -> list[EventView]:
    by_key = {x.key: x for x in judged(engine, p)}
    for e in evs:
        x = by_key.get(e.key or "")
        if x:
            e.title, e.verdict = x.title, x.verdict
    return evs


def events(
    engine: Engine,
    type: str = "",
    source: str = "",
    key: str = "",
    baseline: bool = True,
    before: int | None = None,
    limit: int = 100,
    p: Profile = PROFILE,
) -> EventPage:
    conds = []  # every filter but type, which the type counts ignore
    if source:
        conds.append(EventRow.source == source)
    if key:
        conds.append(EventRow.key == key)
    if not baseline:
        conds.append(func.coalesce(EventRow.detail["baseline"].astext, "false") != "true")
    with Session(engine) as s:
        q = select(EventRow).where(*conds, *([EventRow.type == type] if type else []))
        if before:
            q = q.where(EventRow.id < before)
        rows = list(s.scalars(q.order_by(EventRow.id.desc()).limit(limit + 1)))
        tc = dict(s.execute(select(EventRow.type, func.count()).where(*conds).group_by(EventRow.type)).all())
    more = len(rows) > limit
    rows = rows[:limit]
    return EventPage(_attach(engine, [_event_view(e) for e in rows], p), rows[-1].id if more else None, tc)


def pending_events(s: Session) -> list[EventRow]:
    return list(s.scalars(select(EventRow).where(EventRow.notified.is_(None)).order_by(EventRow.at, EventRow.id)))


def load_listings(s: Session, keys) -> dict[str, Listing]:
    keys = [k for k in set(keys) if k]
    return {r.key: listing_from_row(r) for r in _load(s, ListingRow.key.in_(keys))} if keys else {}


# --- overview ------------------------------------------------------------------------------------------


@dataclass
class Overview:
    matrix: dict[str, dict[str, int]]  # verdict -> status -> count (listed only, gone excluded)
    eligible_open: int
    eligible_live: int  # eligible and open, waitlist or upcoming
    new_24h: int
    new_7d: int
    baseline_24h: int
    changed_24h: int
    changed_7d: int
    failing: list[str]
    last_digest: datetime | None
    pending: int
    eligible_now: list[ListingSummary]
    recent: list[EventView]
    total: int
    n_sources: int


def overview(engine: Engine, p: Profile = PROFILE) -> Overview:
    allx = judged(engine, p)
    matrix = {v: {st: 0 for st in STATUSES[:-1]} for v in VERDICTS}
    for x in allx:
        if x.status != "gone":
            matrix[x.verdict][x.status] = matrix[x.verdict].get(x.status, 0) + 1
    t = now()
    d1, d7 = t - timedelta(hours=24), t - timedelta(days=7)
    base = func.coalesce(EventRow.detail["baseline"].astext, "false") == "true"
    with Session(engine) as s:

        def n(*w):
            return s.scalar(select(func.count()).select_from(EventRow).where(*w)) or 0

        new_t, chg = EventRow.type == "new", EventRow.type.in_(("changed", "reopened", "closed", "gone"))
        ov = dict(
            new_24h=n(new_t, EventRow.at >= d1, ~base),
            new_7d=n(new_t, EventRow.at >= d7, ~base),
            baseline_24h=n(new_t, EventRow.at >= d1, base),
            changed_24h=n(chg, EventRow.at >= d1),
            changed_7d=n(chg, EventRow.at >= d7),
            pending=n(EventRow.notified.is_(None)),
            last_digest=s.scalar(select(func.max(EventRow.notified))),
            failing=list(
                s.scalars(
                    select(SourceStateRow.source)
                    .where(SourceStateRow.consecutive_failures > 0)
                    .order_by(SourceStateRow.consecutive_failures.desc())
                )
            ),
        )
        newest = select(EventRow).order_by(EventRow.id.desc()).limit(12)
        recent = [_event_view(e) for e in s.scalars(newest.where(~base))]
        if len(recent) < 12:  # top up with first-run inventory, e.g. right after the fleet starts
            recent = sorted(recent + [_event_view(e) for e in s.scalars(newest.where(base))], key=lambda e: -e.id)[:12]
    live = [x for x in allx if x.verdict == ELIGIBLE and x.status in ("open", "waitlist", "upcoming")]
    live.sort(key=lambda x: (x.status != "open", -x.last_changed.timestamp()))
    return Overview(
        matrix=matrix,
        eligible_open=sum(1 for x in live if x.status == "open"),
        eligible_live=len(live),
        eligible_now=live[:10],
        recent=_attach(engine, recent, p),
        total=sum(1 for x in allx if x.status != "gone"),
        n_sources=len({x.source for x in allx}),
        **ov,
    )


# --- sources -------------------------------------------------------------------------------------------


@dataclass
class SourceHealth:
    source: str
    title: str | None
    url: str | None
    every_hours: float | None
    health: str  # ok | failing | never
    last_ok: datetime | None
    last_attempt: datetime | None
    failing_since: datetime | None
    consecutive_failures: int
    next_due: datetime | None
    last_duration: float | None  # seconds
    last_error: str | None
    last_events: int | None
    listings: int  # listed now (not gone)
    eligible: int
    possible: int
    workers: list[str]  # who ran it in the last 7 days


def _adapters() -> dict:
    try:
        from .adapters import load_all

        return load_all()
    except Exception:  # noqa: BLE001 - the health table must render even if one adapter fails to import
        return {}


def sources(engine: Engine, p: Profile = PROFILE) -> list[SourceHealth]:
    allx = judged(engine, p)
    per: dict[str, list[int]] = {}
    for x in allx:
        if x.status != "gone":
            c = per.setdefault(x.source, [0, 0, 0])
            c[0] += 1
            c[1] += x.verdict == ELIGIBLE
            c[2] += x.verdict == POSSIBLE
    with Session(engine) as s:
        state = {r.source: r for r in s.scalars(select(SourceStateRow))}
        last = {
            r.source: r
            for r in s.scalars(select(RunRow).distinct(RunRow.source).order_by(RunRow.source, RunRow.started.desc()))
        }
        workers = {
            src: sorted(ws or [])
            for src, ws in s.execute(
                text(
                    "SELECT source, array_agg(DISTINCT w) FROM runs, unnest(workers) w"
                    " WHERE started > now() - interval '7 days' GROUP BY source"
                )
            )
        }
    ads = _adapters()
    out = []
    for name in sorted(set(state) | set(last) | set(per) | set(ads)):
        st, run, ad = state.get(name), last.get(name), ads.get(name)
        fails = st.consecutive_failures if st else 0
        dur = (run.finished - run.started).total_seconds() if run and run.finished else None
        out.append(
            SourceHealth(
                source=name,
                title=getattr(ad, "title", None),
                url=getattr(ad, "url", None),
                every_hours=getattr(ad, "every_hours", None),
                health="failing" if fails else ("ok" if st and st.last_ok else "never"),
                last_ok=st and st.last_ok,
                last_attempt=st and st.last_attempt,
                failing_since=st and st.failing_since,
                consecutive_failures=fails,
                next_due=st and st.next_due,
                last_duration=dur,
                last_error=run.error if run and not run.ok else None,
                last_events=run.events if run else None,
                listings=per.get(name, [0])[0],
                eligible=per.get(name, [0, 0])[1],
                possible=per.get(name, [0, 0, 0])[2],
                workers=workers.get(name, []),
            )
        )
    out.sort(key=lambda x: (-x.consecutive_failures, x.health == "never", x.source))
    return out


# --- fleet (Procrastinate's tables; absent until a worker has applied its schema) ----------------------


@dataclass
class Fleet:
    available: bool
    workers: list[dict] = field(default_factory=list)  # id, last_heartbeat, age (s), host, doing
    queues: dict[str, dict[str, int]] = field(default_factory=dict)  # queue -> status -> count
    failures: list[dict] = field(default_factory=list)
    hosts: list[dict] = field(default_factory=list)  # from runs.workers: name, last_run, runs/fails 24h


def fleet(engine: Engine) -> Fleet:
    with engine.connect() as c:
        hosts = [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT w AS name, max(started) AS last_run,"
                    " count(*) FILTER (WHERE started > now() - interval '24 hours') AS runs_24h,"
                    " count(*) FILTER (WHERE started > now() - interval '24 hours' AND ok IS false) AS fails_24h,"
                    " count(DISTINCT source) AS sources"
                    " FROM runs, unnest(workers) w GROUP BY w ORDER BY max(started) DESC"
                )
            )
        ]
        if c.execute(text("SELECT to_regclass('procrastinate_jobs')")).scalar() is None:
            return Fleet(False, hosts=hosts)
        t = now()
        workers = []
        for r in c.execute(
            text(
                "SELECT w.id, w.last_heartbeat,"
                " (SELECT r.workers[1] FROM procrastinate_jobs j JOIN runs r ON r.job_id = j.id"
                "   WHERE j.worker_id = w.id ORDER BY r.started DESC LIMIT 1) AS host,"
                " (SELECT array_agg(j.task_name || coalesce(' ' || (j.args->>'source'), '') ORDER BY j.id)"
                "   FROM procrastinate_jobs j WHERE j.worker_id = w.id AND j.status = 'doing') AS doing"
                " FROM procrastinate_workers w ORDER BY w.last_heartbeat DESC"
            )
        ):
            workers.append({**r._mapping, "age": (t - r.last_heartbeat).total_seconds(), "doing": r.doing or []})
        queues: dict[str, dict[str, int]] = {}
        for q, st, n in c.execute(
            text("SELECT queue_name, status::text, count(*) FROM procrastinate_jobs GROUP BY 1, 2 ORDER BY 1")
        ):
            queues.setdefault(q, {})[st] = n
        failures = [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT j.id, j.queue_name, j.task_name, j.args, j.attempts,"
                    " (SELECT max(e.at) FROM procrastinate_events e WHERE e.job_id = j.id) AS at,"
                    " (SELECT r.error FROM runs r WHERE r.job_id = j.id ORDER BY r.started DESC LIMIT 1) AS error"
                    " FROM procrastinate_jobs j WHERE j.status = 'failed' ORDER BY at DESC NULLS LAST, j.id DESC"
                    " LIMIT 25"
                )
            )
        ]
    return Fleet(True, workers, queues, failures, hosts)


def as_open(lst: Listing) -> Listing:
    """The listing judged as if still open: 'was it eligible?' for closed/gone events."""
    return replace(lst, status="open")
