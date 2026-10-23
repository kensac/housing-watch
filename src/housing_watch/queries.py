"""Read API over the store, for the UI and the digest. Returns plain dataclasses: the read model.

Verdicts are computed here, at read time, by criteria; nothing judged is ever stored. Every listing is
judged once per store version (a cheap aggregate over `listings`) and kept in memory, so the pages
filter and sort a few thousand listings in Python without reloading them.

These dataclasses are the API's source of truth: web/schema.py derives its GraphQL types from them, a field's
trailing comment becoming its description. A field made with `internal()` stays out of the API and the MCP tools.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload

from . import commute as C
from . import filters as F
from . import tracker as T
from .criteria import ELIGIBLE, EXCLUDED, POSSIBLE, PROFILE, Profile, listing_verdict, unit_verdict
from .mapping import listing_from_row
from .models import Band, Listing
from .orm import (
    BandRow,
    CommuteRow,
    EventRow,
    ListingRow,
    RunRow,
    SourceStateRow,
    TrackerEventRow,
    TrackerLinkRow,
    TrackerRow,
    UnitRow,
)

VERDICTS = (ELIGIBLE, POSSIBLE, EXCLUDED)
STATUSES = ("open", "waitlist", "upcoming", "unknown", "closed", "gone")
HEALTH = ("ok", "failing", "never")
LISTING_EVENTS = ("new", "reopened", "changed", "closed", "gone")
EVENT_TYPES = (*LISTING_EVENTS, "page_changed", "watching", "source_failed", "source_recovered")
SORTS = ("verdict", "changed", "first_seen", "rent", "deadline", "title", "source")  # "-x" flips the default
_NEWEST_FIRST = {"changed", "first_seen"}
_VRANK = {ELIGIBLE: 0, POSSIBLE: 1, EXCLUDED: 2}


def now() -> datetime:
    return datetime.now(UTC)


def internal(**kw):
    """A read-side field (search, sort) that the API and the MCP tools never show."""
    return field(repr=False, metadata={"internal": True}, **kw)


def public(x) -> dict:
    """A read-model dataclass as a dict, without its internal fields."""
    d = asdict(x)
    return {f.name: d[f.name] for f in fields(x) if not f.metadata.get("internal")}


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
    unit_sizes: frozenset[str] = internal(default_factory=frozenset)
    unit_rents: tuple[float, ...] = internal(default=())
    haystack: str = internal(default="")  # lowercased text the search box matches
    commute: int | None = None  # transit minutes to HW_COMMUTE_TO (eligible listings only)
    commute_route: C.Route | None = None
    application: str | None = None  # the stage of this listing's tracker row (tracker.stage of its Status)
    application_group: str | None = None  # whose move that stage is; None for an unknown status
    application_since: datetime | None = None  # when the row entered that stage, as first recorded
    application_since_baseline: bool = False  # the stage predates tracking: it began before application_since
    application_notes: str | None = None
    tracker_row: str | None = None  # that row's Property
    tracker_match: str | None = None  # how the row was matched to this listing


def _load(s: Session, *where) -> list[ListingRow]:
    q = select(ListingRow).options(selectinload(ListingRow.units).selectinload(UnitRow.bands))
    return list(s.scalars(q.where(*where)))


def _summary(r: ListingRow, lst: Listing, p: Profile, commutes: dict | None = None) -> ListingSummary:
    v = listing_verdict(lst, p)
    # ranges over the units that could fit (not excluded); all units when none does
    fit = [u for u, uv, _ in v.units if uv != EXCLUDED] or lst.units
    rents = tuple(u.rent for u in fit if u.rent)
    amis = [u.ami for u in fit if u.ami]
    order = ["studio", "sro", "1br", "2br", "3br", "4br", "5br", "unknown"]
    sizes = sorted({u.size for u in lst.units}, key=lambda z: order.index(z) if z in order else 99)
    return ListingSummary(
        key=r.key,
        source=r.source,
        title=lst.title or r.key,
        url=lst.url,
        address=lst.address,
        borough=lst.borough,
        status="gone" if r.gone_since else lst.status if lst.status in STATUSES else "unknown",
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
        unit_rents=tuple(u.rent for u in lst.units if u.rent),
        haystack=" ".join(x for x in (r.key, lst.title, lst.address, lst.borough, lst.notes) if x).lower(),
        **_commute(commutes or {}, lst),
    )


def _with_tracker(x: ListingSummary, ref: T.Ref | None) -> ListingSummary:
    if ref is None:
        return x
    return replace(x, application=ref.stage, application_group=ref.group,
                   application_since=ref.since and ref.since.at,
                   application_since_baseline=bool(ref.since and ref.since.baseline),
                   application_notes=ref.notes, tracker_row=ref.property, tracker_match=ref.how)  # fmt: skip


def _commute(commutes: dict, lst: Listing) -> dict:
    c = commutes.get(C.origin(lst.address, lst.borough) or "")
    if not (c and c.status == "ok"):
        return {}
    return {"commute": c.minutes, "commute_route": C.Route.of(c.route)}


@dataclass
class UnitSummary:
    """One unit with its listing's context: the row of the Units page."""

    id: int
    key: str  # the listing's key
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
    income_min: int | None  # the band for the profile's household size
    income_max: int | None
    verdict: str  # this unit's, after the listing-level rules (closed, borough, senior, ...)
    reason: str
    commute: int | None = None
    application: str | None = None
    application_group: str | None = None
    application_since: datetime | None = None
    application_since_baseline: bool = False
    haystack: str = internal(default="")


def _units(r: ListingRow, lst: Listing, x: ListingSummary, p: Profile) -> list[UnitSummary]:
    v = listing_verdict(lst, p)
    per = {id(u): (uv, why) for u, uv, why in v.units}
    out = []
    for row, u in zip(r.units, lst.units, strict=True):
        uv, why = per.get(id(u), (v.verdict, v.reason))  # no per-unit verdict: a listing-level rule
        if uv == ELIGIBLE and v.verdict == POSSIBLE:
            uv, why = POSSIBLE, v.reason  # e.g. borough unknown
        band = next((b for b in u.bands if b.household == p.household), None)
        out.append(UnitSummary(
            id=row.id, key=x.key, source=x.source, title=x.title, url=x.url, address=x.address, borough=x.borough,
            status=x.status, kind=x.kind, deadline=x.deadline, first_seen=x.first_seen, last_changed=x.last_changed,
            gone_since=x.gone_since, listing_verdict=x.verdict, size=u.size, unit_no=u.unit_no, rent=u.rent,
            ami=u.ami, set_aside=u.set_aside, available=u.available, chart_year=u.chart_year,
            income_min=band.income_min if band else None, income_max=band.income_max if band else None,
            verdict=uv, reason=why, commute=x.commute, application=x.application,
            application_group=x.application_group, application_since=x.application_since,
            application_since_baseline=x.application_since_baseline, haystack=x.haystack,
        ))  # fmt: skip
    return out


_cache: dict[tuple, tuple[tuple, list[ListingSummary], list[UnitSummary]]] = {}
_lock = threading.Lock()


def judged(engine: Engine, p: Profile = PROFILE) -> list[ListingSummary]:
    """Every listing, summarized and judged; recomputed only when the listings table moved."""
    return _judged(engine, p)[0]


def judged_units(engine: Engine, p: Profile = PROFILE) -> list[UnitSummary]:
    """Every unit of every listing, judged; cached with judged()."""
    return _judged(engine, p)[1]


def _judged(engine: Engine, p: Profile) -> tuple[list[ListingSummary], list[UnitSummary]]:
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
        ) + tuple(s.execute(select(func.count(), func.max(CommuteRow.computed))).one())
        version += tuple(s.execute(select(func.count(), func.max(TrackerRow.synced))).one())
        version += tuple(s.execute(select(func.count(), func.max(TrackerLinkRow.created))).one())
        version += tuple(s.execute(select(func.max(TrackerEventRow.id))).one())
        ck = (str(engine.url), p)
        hit = _cache.get(ck)
        if hit and hit[0] == version:
            return hit[1], hit[2]
        with _lock:
            commutes = C.table(engine)
            rows = _load(s)
            lsts = [listing_from_row(r) for r in rows]
            out = [_summary(r, lst, p, commutes) for r, lst in zip(rows, lsts, strict=True)]
            refs = T.match(out, *T.load(engine), T.history(engine))  # the tracker sheet's rows, matched to listings
            out = [_with_tracker(x, refs.get(x.key)) for x in out]
            units = [u for r, lst, x in zip(rows, lsts, out, strict=True) for u in _units(r, lst, x, p)]
            _cache[ck] = (version, out, units)
        return out, units


COMPUTED = {  # read-time values behind filters.COMPUTED
    "verdict": lambda x: x.verdict,
    "reason": lambda x: x.reason,
    "eligible_units": lambda x: x.eligible_units,
    "unit_count": lambda x: x.units,
    "commute": lambda x: x.commute,
    "application": lambda x: x.application,
    "application_group": lambda x: x.application_group,
    "application_notes": lambda x: x.application_notes,
}


@dataclass
class ListingQuery:
    filters: list[F.Cond] = field(default_factory=list)
    q: str = ""  # quick search over title, address, borough and notes
    sort: str = "verdict"
    offset: int = 0  # the list scrolls: each batch starts where the last one ended
    limit: int = 200


@dataclass
class ListingPage:
    items: list[ListingSummary]
    total: int
    next_offset: int | None  # None once the last batch is out
    verdict_counts: dict[str, int]  # over everything matching the other filters, for the verdict tabs


def hides_gone(conds: list[F.Cond]) -> bool:
    """Removed listings stay out of the way unless a filter asks about removal."""
    return not any(c.field.name.removeprefix("listing.") == "gone_since" for c in conds)


def filter_listings(
    engine: Engine, conds: list[F.Cond], q: str = "", p: Profile = PROFILE, hide_gone: bool | None = None
) -> list[ListingSummary]:
    """SQL for the column conditions, then the computed ones in Python over the cached verdicts."""
    allx = judged(engine, p)
    where = F.listing_where(conds)
    if where:
        with Session(engine) as s:
            keys = set(s.scalars(select(ListingRow.key).where(*where)))
        allx = [x for x in allx if x.key in keys]
    hide = hides_gone(conds) if hide_gone is None else hide_gone
    comp = [c for c in conds if c.field.scope == "computed"]
    words = q.strip().lower().split()
    return [
        x
        for x in allx
        if not (hide and x.gone_since)
        and all(F.match(c, COMPUTED[c.field.name](x)) for c in comp)
        and all(w in x.haystack for w in words)
    ]


def _by_stage(x: ListingSummary | UnitSummary) -> tuple:
    """Stage order (tracker.rank), then longest in its stage first."""
    since = x.application_since.timestamp() if x.application_since else float("inf")
    return (T.rank(x.application), since, x.title.lower())


def _sort_key(sort: str):
    return {
        "verdict": lambda x: (_VRANK[x.verdict], -x.last_changed.timestamp()),
        "changed": lambda x: x.last_changed,
        "first_seen": lambda x: x.first_seen,
        "rent": lambda x: (x.rent_min is None, x.rent_min or 0),
        "commute": lambda x: (x.commute is None, x.commute or 0),
        "application": _by_stage,
        "deadline": lambda x: (x.deadline is None, x.deadline or ""),
        "title": lambda x: x.title.lower(),
        "source": lambda x: (x.source, x.title.lower()),
        "borough": lambda x: (x.borough or "~", x.title.lower()),
    }.get(sort, lambda x: (_VRANK[x.verdict], -x.last_changed.timestamp()))


def _ranges_over(rows: list[ListingSummary], units: list[UnitSummary]) -> list[ListingSummary]:
    by: dict[str, list[UnitSummary]] = {}
    for u in units:
        by.setdefault(u.key, []).append(u)
    out = []
    for x in rows:
        us = [u for u in by.get(x.key, []) if u.verdict != EXCLUDED] or by.get(x.key, [])
        rents, amis = [u.rent for u in us if u.rent], [u.ami for u in us if u.ami]
        out.append(replace(x, rent_min=min(rents, default=None), rent_max=max(rents, default=None),
                           ami_min=min(amis, default=None), ami_max=max(amis, default=None)))  # fmt: skip
    return out


def search_listings(engine: Engine, f: ListingQuery, p: Profile = PROFILE) -> ListingPage:
    verdict = [c for c in f.filters if c.field.name == "verdict"]
    base = filter_listings(engine, [c for c in f.filters if c not in verdict], f.q, p, hides_gone(f.filters))
    counts = {v: 0 for v in VERDICTS}
    for x in base:
        counts[x.verdict] += 1
    rows = [x for x in base if all(F.match(c, x.verdict) for c in verdict)]
    unit_conds = [c for c in f.filters if c.field.scope in ("unit", "band")]
    if unit_conds:  # ranges (and the rent sort) over the units the filters picked, of those that fit
        rows = _ranges_over(rows, filter_units(engine, unit_conds, p=p, hide_gone=False))
    name = f.sort.lstrip("-")
    rows.sort(key=_sort_key(name), reverse=(name in _NEWEST_FIRST) != f.sort.startswith("-"))
    start, end = max(0, f.offset), max(0, f.offset) + max(1, min(f.limit, 1000))
    return ListingPage(rows[start:end], len(rows), end if end < len(rows) else None, counts)


UNIT_COMPUTED = {  # the same computed fields on the Units page: verdict and reason are the unit's
    "verdict": lambda u, x: u.verdict,
    "reason": lambda u, x: u.reason,
    "eligible_units": lambda u, x: x.eligible_units,
    "unit_count": lambda u, x: x.units,
    "commute": lambda u, x: x.commute,
    "application": lambda u, x: x.application,
    "application_group": lambda u, x: x.application_group,
    "application_notes": lambda u, x: x.application_notes,
}


def filter_units(
    engine: Engine, conds: list[F.Cond], q: str = "", p: Profile = PROFILE, hide_gone: bool | None = None
) -> list[UnitSummary]:
    """Like filter_listings, but every unit and band condition must hold on the row's own unit."""
    by_key = {x.key: x for x in judged(engine, p)}
    allu = judged_units(engine, p)
    where = F.unit_where(conds)
    if where:
        with Session(engine) as s:
            ids = set(
                s.scalars(select(UnitRow.id).join(ListingRow, ListingRow.key == UnitRow.listing_key).where(*where))
            )
        allu = [u for u in allu if u.id in ids]
    hide = hides_gone(conds) if hide_gone is None else hide_gone
    comp = [c for c in conds if c.field.scope == "computed"]
    words = q.strip().lower().split()
    return [
        u
        for u in allu
        if not (hide and u.gone_since)
        and all(F.match(c, UNIT_COMPUTED[c.field.name](u, by_key[u.key])) for c in comp)
        and all(w in u.haystack for w in words)
    ]


_SIZE_ORDER = {z: i for i, z in enumerate(["studio", "sro", "1br", "2br", "3br", "4br", "5br", "unknown"])}
UNIT_SORTS = {
    "verdict": lambda u: (_VRANK[u.verdict], u.rent is None, u.rent or 0),
    "rent": lambda u: (u.rent is None, u.rent or 0),
    "ami": lambda u: (u.ami is None, u.ami or 0),
    "commute": lambda u: (u.commute is None, u.commute or 0),
    "application": _by_stage,
    "size": lambda u: (_SIZE_ORDER.get(u.size, 99), u.rent or 0),
    "income_min": lambda u: (u.income_min is None, u.income_min or 0),
    "income_max": lambda u: (u.income_max is None, u.income_max or 0),
    "changed": lambda u: u.last_changed,
    "first_seen": lambda u: u.first_seen,
    "deadline": lambda u: (u.deadline is None, u.deadline or ""),
    "title": lambda u: (u.title.lower(), u.unit_no or ""),
    "borough": lambda u: (u.borough or "~", u.title.lower()),
    "source": lambda u: (u.source, u.title.lower()),
}


@dataclass
class UnitPage:
    items: list[UnitSummary]
    total: int
    next_offset: int | None
    verdict_counts: dict[str, int]


def search_units(engine: Engine, f: ListingQuery, p: Profile = PROFILE) -> UnitPage:
    verdict = [c for c in f.filters if c.field.name == "verdict"]
    base = filter_units(engine, [c for c in f.filters if c not in verdict], f.q, p, hides_gone(f.filters))
    counts = {v: 0 for v in VERDICTS}
    for u in base:
        counts[u.verdict] += 1
    rows = [u for u in base if all(F.match(c, u.verdict) for c in verdict)]
    name = f.sort.lstrip("-")
    rows.sort(
        key=UNIT_SORTS.get(name, UNIT_SORTS["verdict"]), reverse=(name in _NEWEST_FIRST) != f.sort.startswith("-")
    )
    start, end = max(0, f.offset), max(0, f.offset) + max(1, min(f.limit, 1000))
    return UnitPage(rows[start:end], len(rows), end if end < len(rows) else None, counts)


# --- value counts for the filter builder ---------------------------------------------------------------

ENUM_MAX = 40  # a text field with at most this many distinct values is offered as a checkbox list
_distinct: dict[tuple, tuple[float, int]] = {}


def is_enum(engine: Engine, fld: F.Field, p: Profile = PROFILE) -> bool:
    if fld.choices or fld.order:
        return True
    if fld.type != "text":
        return False
    ck = (str(engine.url), fld.name)
    hit = _distinct.get(ck)
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1] <= ENUM_MAX
    if fld.scope == "computed":
        n = len({COMPUTED[fld.name](x) for x in judged(engine, p)})
    else:
        with Session(engine) as s:
            n = s.scalar(select(func.count(fld.column.distinct())))
    _distinct[ck] = (time.monotonic(), n)
    return n <= ENUM_MAX


def _values_by_key(s: Session, fld: F.Field, keys: list[str], p: Profile, engine: Engine):
    """(listing key, value) pairs for one listing/unit/band/computed field over the given listings."""
    if fld.scope == "computed":
        by = {x.key: x for x in judged(engine, p)}
        return [(k, COMPUTED[fld.name](by[k])) for k in keys if k in by]
    u, b, ls = UnitRow.__table__, BandRow.__table__, ListingRow.__table__
    if fld.scope == "listing":
        q = select(ls.c.key, fld.column).where(ls.c.key.in_(keys))
    elif fld.scope == "unit":
        q = select(u.c.listing_key, fld.column).distinct().where(u.c.listing_key.in_(keys))
    else:
        q = select(u.c.listing_key, fld.column).distinct().join(b, b.c.unit_id == u.c.id)
        q = q.where(u.c.listing_key.in_(keys))
    return s.execute(q).all()


def value_counts(
    engine: Engine, target: str, fld: F.Field, conds: list[F.Cond], q: str = "", p: Profile = PROFILE
) -> list[tuple[str | None, int]]:
    """Each value of an enum field with how many results it would give, under the other filters."""
    others = [c for c in conds if c.field is not fld]
    counts: dict = {}
    if target == "units":
        return _sorted_counts(fld, _unit_value_counts(engine, fld, others, q, p, hides_gone(conds)))
    with Session(engine) as s:
        if target == "events" and fld.scope == "event":
            q = select(fld.column, func.count()).where(*event_where(engine, others, p)).group_by(fld.column)
            counts = dict(s.execute(q).all())
        else:
            if target == "events":
                w = select(EventRow.key, func.count()).where(*event_where(engine, others, p), EventRow.key.isnot(None))
                weights = dict(s.execute(w.group_by(EventRow.key)).all())
            else:
                weights = {x.key: 1 for x in filter_listings(engine, others, q, p, hides_gone(conds))}
            seen: set = set()
            for k, v in _values_by_key(s, fld, list(weights), p, engine):
                if (k, v) not in seen:
                    seen.add((k, v))
                    counts[v] = counts.get(v, 0) + weights[k]
    return _sorted_counts(fld, counts)


def _sorted_counts(fld: F.Field, counts: dict) -> list[tuple[str | None, int]]:
    known = fld.choices or fld.order
    for v in known:
        counts.setdefault(v, 0)
    order = {v: i for i, v in enumerate(known)}
    return sorted(counts.items(), key=lambda kv: (order.get(kv[0], 99), -kv[1], str(kv[0])))


def _unit_value_counts(engine: Engine, fld: F.Field, others: list[F.Cond], q: str, p: Profile, hide: bool) -> dict:
    """How many units each value of the field would give on the Units page."""
    units = filter_units(engine, others, q, p, hide)
    if fld.scope == "computed":
        by_key = {x.key: x for x in judged(engine, p)}
        vals = [(u.id, UNIT_COMPUTED[fld.name](u, by_key[u.key])) for u in units]
    else:
        ids = [u.id for u in units]
        u, b, ls = UnitRow.__table__, BandRow.__table__, ListingRow.__table__
        q_ = select(u.c.id, fld.column).distinct().where(u.c.id.in_(ids))
        if fld.scope == "listing":
            q_ = q_.join(ls, ls.c.key == u.c.listing_key)
        elif fld.scope == "band":
            q_ = q_.join(b, b.c.unit_id == u.c.id)
        with Session(engine) as s:
            vals = s.execute(q_).all() if ids else []
    counts: dict = {}
    for _, v in set(vals):
        counts[v] = counts.get(v, 0) + 1
    return counts


@dataclass
class UnitView:
    size: str
    unit_no: str | None
    rent: float | None
    ami: int | None
    set_aside: str | None
    available: bool | None
    bands: list[Band]
    verdict: str
    reason: str
    chart_year: int | None = None  # HUD chart the ad's printed income caps come from


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
class BriefUnit:
    size: str | None
    unit_no: str | None
    rent: float | None
    ami: int | None
    available: bool | None
    set_aside: str | None
    bands: list[Band]


@dataclass
class Brief:
    """One side of a `changed` event: what db._brief keeps of the listing."""

    status: str | None
    deadline: str | None
    kind: str | None
    borough: str | None
    senior_only: bool | None
    units: list[BriefUnit]

    @classmethod
    def of(cls, d: dict) -> Brief:
        def unit(u: list) -> BriefUnit:
            u = [*u, *[None] * (7 - len(u))]  # events from before bands were kept have six
            return BriefUnit(*u[:6], [Band(*b) for b in u[6] or []])

        return cls(d.get("status"), d.get("deadline"), d.get("kind"), d.get("borough"), d.get("senior_only"),
                   [unit(u) for u in d.get("units") or []])  # fmt: skip


@dataclass
class ListingChange:
    """What a `changed` event changed."""

    before: Brief
    after: Brief
    changes: list[str]  # the differences in words (changes.describe), most decisive first
    noise: bool  # a status flap to or from "unknown", not news


@dataclass
class PageChange:
    """What changed on a watched page (`page_changed`)."""

    added: list[str]  # new lines of text
    removed: list[str]
    new_links: list[str]


@dataclass
class SourceFailure:
    """A `source_failed` event."""

    error: str | None
    fails: int | None  # consecutive failures when it was recorded


def event_detail(e: EventView) -> ListingChange | PageChange | SourceFailure | None:
    """An event's stored detail as its type's dataclass; None for types that keep none."""
    d = e.detail or {}
    if e.type == "changed" and "before" in d:
        return ListingChange(Brief.of(d["before"]), Brief.of(d.get("after") or {}), list(d.get("changes") or []),
                             bool(d.get("noise")))  # fmt: skip
    if e.type == "page_changed":
        return PageChange(list(d.get("added") or []), list(d.get("removed") or []), list(d.get("new_links") or []))
    if e.type == "source_failed":
        return SourceFailure(d.get("error"), d.get("fails"))
    return None


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
                list(u.bands),
                *unit_verdict(u, p),
                u.chart_year,
            )
            for u in lst.units
        ]
        evs = s.scalars(select(EventRow).where(EventRow.key == key).order_by(EventRow.at.desc()).limit(50))
        return ListingDetail(
            summary=next((x for x in judged(engine, p) if x.key == key), None)
            or _summary(r, lst, p),  # with commute + tracker
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
    next_before: int | None  # pass as `before` for the next (older) batch
    total: int


def _attach(engine: Engine, evs: list[EventView], p: Profile) -> list[EventView]:
    by_key = {x.key: x for x in judged(engine, p)}
    for e in evs:
        x = by_key.get(e.key or "")
        if x:
            e.title, e.verdict = x.title, x.verdict
    return evs


def event_where(engine: Engine, conds: list[F.Cond], p: Profile = PROFILE) -> list:
    """Event columns in SQL; listing, unit, band and verdict conditions through the event's listing."""
    out = [F.sql(c) for c in conds if c.field.scope == "event"]
    via = [c for c in conds if c.field.scope != "event"]
    if via:
        out.append(EventRow.key.in_([x.key for x in filter_listings(engine, via, p=p, hide_gone=False)]))
    return out


def events(
    engine: Engine, conds: list[F.Cond] = (), key: str = "", before: int | None = None, limit: int = 200,
    p: Profile = PROFILE,
) -> EventPage:  # fmt: skip
    where = event_where(engine, list(conds), p) + ([EventRow.key == key] if key else [])
    with Session(engine) as s:
        q = select(EventRow).where(*where)
        if before:
            q = q.where(EventRow.id < before)
        rows = list(s.scalars(q.order_by(EventRow.id.desc()).limit(limit + 1)))
        total = s.scalar(select(func.count()).select_from(EventRow).where(*where))
    more = len(rows) > limit
    rows = rows[:limit]
    return EventPage(_attach(engine, [_event_view(e) for e in rows], p), rows[-1].id if more else None, total)


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


# --- tracker -------------------------------------------------------------------------------------------


@dataclass
class TrackerRowView:
    """A tracker sheet row with its stage history and the listings matched to it."""

    row_key: str
    position: int  # the sheet row number
    property: str
    status: str | None  # as written in the sheet
    stage: str | None  # the stage that status means (tracker.stage)
    group: str | None  # whose move that stage is
    stage_since: datetime | None  # when the row entered its stage
    stage_since_baseline: bool  # the stage predates tracking: it began before stage_since
    events: list[T.Step]  # stage changes, oldest first
    notes: str | None
    source_url: str | None
    page_url: str | None
    cells: list[T.Cell]  # every column of the row, as the sheet has them
    listings: list[ListingSummary]  # empty: unmatched, link one by hand


@dataclass
class TrackerBoard:
    configured: bool  # HW_TRACKER_SHEET is set
    synced: datetime | None
    rows: list[TrackerRowView]  # by stage, then longest in it


def tracker_board(engine: Engine, p: Profile = PROFILE) -> TrackerBoard:
    rows, _ = T.load(engine)
    steps = T.history(engine)
    by_row: dict[str, list[ListingSummary]] = {}
    for x in judged(engine, p):
        if x.tracker_row:
            by_row.setdefault(T.row_key(x.tracker_row), []).append(x)
    synced = max((r.synced for r in rows), default=None)
    out = []
    for r in rows:
        st, hist = T.stage(r.status), steps.get(r.row_key, [])
        since = T.since(hist, r.status)
        out.append(TrackerRowView(
            r.row_key, r.position, r.property, r.status, st, T.group(st), since and since.at,
            bool(since and since.baseline), hist, r.notes, r.source_url, r.page_url, T.cells(r.cells),
            by_row.get(r.row_key, []),
        ))  # fmt: skip
    out.sort(key=lambda d: (T.rank(d.stage), d.stage_since is None, d.stage_since or synced, d.position))
    return TrackerBoard(bool(T.SHEET), synced, out)


# --- sources -------------------------------------------------------------------------------------------


@dataclass
class SourceHealth:
    source: str
    title: str | None
    url: str | None
    every_hours: float | None
    health: str  # ok, failing, or never (no run has succeeded yet)
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
class FleetWorker:
    """A worker process, from its fleet_workers heartbeat."""

    name: str  # the host it runs on
    queues: list[str]
    concurrency: int
    started: datetime
    last_seen: datetime
    age: float  # seconds since its last heartbeat
    doing: list[str]  # sources it is running now


@dataclass
class FleetFailure:
    """A failed Procrastinate job."""

    id: int
    queue_name: str
    task_name: str
    args: dict  # the task's keyword arguments, as queued
    attempts: int
    at: datetime | None
    error: str | None  # from the job's last run


@dataclass
class FleetHost:
    """A host that ran sources, from runs.workers."""

    name: str
    last_run: datetime
    runs_24h: int
    fails_24h: int
    sources: int


@dataclass
class Fleet:
    available: bool  # Procrastinate's tables exist
    workers: list[FleetWorker] = field(default_factory=list)
    queues: dict[str, dict[str, int]] = field(default_factory=dict)  # queue -> status -> count
    failures: list[FleetFailure] = field(default_factory=list)
    hosts: list[FleetHost] = field(default_factory=list)


JOBS_TABLE = "procrastinate_jobs"  # absent until migrations (or a worker) create Procrastinate's schema


def fleet(engine: Engine) -> Fleet:
    with engine.connect() as c:
        hosts = [
            FleetHost(**r._mapping)
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
        if c.execute(text("SELECT to_regclass(:t)"), {"t": JOBS_TABLE}).scalar() is None:
            return Fleet(False, hosts=hosts)
        t = now()
        workers = []
        for r in c.execute(
            text(
                "SELECT w.name, w.queues, w.concurrency, w.started, w.last_seen,"
                " (SELECT array_agg(r.source ORDER BY r.started) FROM runs r WHERE r.finished IS NULL"
                "   AND w.name = ANY(r.workers) AND r.started > now() - interval '6 hours') AS doing"
                " FROM fleet_workers w ORDER BY w.last_seen DESC, w.name"
            )
        ):
            workers.append(FleetWorker(r.name, list(r.queues), r.concurrency, r.started, r.last_seen,
                                       (t - r.last_seen).total_seconds(), r.doing or []))  # fmt: skip
        queues: dict[str, dict[str, int]] = {}
        for q, st, n in c.execute(
            text("SELECT queue_name, status::text, count(*) FROM procrastinate_jobs GROUP BY 1, 2 ORDER BY 1")
        ):
            queues.setdefault(q, {})[st] = n
        failures = [
            FleetFailure(**r._mapping)
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
