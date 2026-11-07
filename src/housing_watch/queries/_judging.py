"""Every listing and unit summarized and judged, cached per store version."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload

from .. import commute as C
from .. import tracker as T
from ..criteria import ELIGIBLE, EXCLUDED, POSSIBLE, PROFILE, Profile, Verdict, listing_verdict
from ..mapping import listing_from_row
from ..models import Borough, Key, Kind, Listing, Serial, Size
from ..orm import CommuteRow, ListingRow, TrackerEventRow, TrackerLinkRow, TrackerRow, UnitRow
from ._common import _SIZE_ORDER, STATUSES, Status, day, internal


@dataclass
class ListingSummary:
    key: Key
    source: str
    title: str
    url: str
    address: str | None
    borough: Borough | None
    status: Status  # as published, or "gone" once the source stopped listing it
    kind: Kind
    deadline: date | None
    senior_only: bool
    first_seen: datetime
    last_changed: datetime
    gone_since: datetime | None
    verdict: Verdict
    reason: str
    units: int
    eligible_units: int
    sizes: list[Size]
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
    application_group: T.StageGroup | None = None  # whose move that stage is; None for an unknown status
    application_since: datetime | None = None  # when the row entered that stage, as first recorded
    application_since_baseline: bool = False  # the stage predates tracking: it began before application_since
    application_notes: str | None = None
    tracker_row: str | None = None  # that row's Property
    tracker_match: T.Match | None = None  # how the row was matched to this listing


def _load(s: Session, *where) -> list[ListingRow]:
    q = select(ListingRow).options(selectinload(ListingRow.units).selectinload(UnitRow.bands))
    return list(s.scalars(q.where(*where)))


def _summary(r: ListingRow, lst: Listing, p: Profile, commutes: dict | None = None) -> ListingSummary:
    v = listing_verdict(lst, p)
    # ranges over the units that could fit (not excluded); all units when none does
    fit = [u for u, uv, _ in v.units if uv != EXCLUDED] or lst.units
    rents = tuple(u.rent for u in fit if u.rent)
    amis = [u.ami for u in fit if u.ami]
    sizes = sorted({u.size for u in lst.units}, key=lambda z: _SIZE_ORDER.get(z, 99))
    return ListingSummary(
        key=r.key,
        source=r.source,
        title=lst.title or r.key,
        url=lst.url,
        address=lst.address,
        borough=lst.borough,
        status="gone" if r.gone_since else lst.status if lst.status in STATUSES else "unknown",
        kind=lst.kind,
        deadline=day(lst.deadline),
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

    id: Serial
    key: Key  # the listing's key
    source: str
    title: str
    url: str
    address: str | None
    borough: Borough | None
    status: Status
    kind: Kind
    deadline: date | None
    first_seen: datetime
    last_changed: datetime
    gone_since: datetime | None
    listing_verdict: Verdict
    size: Size
    unit_no: str | None
    rent: float | None
    ami: int | None
    set_aside: str | None
    available: bool | None
    chart_year: int | None
    income_min: int | None  # the band for the profile's household size
    income_max: int | None
    verdict: Verdict  # this unit's, after the listing-level rules (closed, borough, senior, ...)
    reason: str
    commute: int | None = None
    application: str | None = None
    application_group: T.StageGroup | None = None
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


@dataclass(frozen=True)
class _Judged:
    listings: list[ListingSummary]
    units: list[UnitSummary]
    by_key: dict[str, ListingSummary]


_cache: dict[tuple, tuple[tuple, _Judged]] = {}
_lock = threading.Lock()


def judged(engine: Engine, p: Profile = PROFILE) -> list[ListingSummary]:
    """Every listing, summarized and judged; recomputed only when a listing, commute or tracker row changed."""
    return _judged(engine, p).listings


def judged_units(engine: Engine, p: Profile = PROFILE) -> list[UnitSummary]:
    """Every unit of every listing, judged; cached with judged()."""
    return _judged(engine, p).units


def judged_by_key(engine: Engine, p: Profile = PROFILE) -> dict[str, ListingSummary]:
    """judged() by listing key."""
    return _judged(engine, p).by_key


def judged_listing(engine: Engine, key: str, p: Profile = PROFILE) -> ListingSummary | None:
    """One listing as judged(), by key."""
    return _judged(engine, p).by_key.get(key)


def _store_version(s: Session) -> tuple:
    """Moves whenever a judged listing could read differently: its listing, commute or tracker row changed."""
    parts = [
        # a sum, not a max: runs commit out of order, so a row can move to an older `updated` than the newest
        select(func.count(), func.sum(func.extract("epoch", ListingRow.updated))),
        select(func.count(), func.max(CommuteRow.computed)),
        select(func.count(), func.max(TrackerRow.synced)),
        select(func.count(), func.max(TrackerLinkRow.created)),
        select(func.max(TrackerEventRow.id)),
    ]
    return tuple(v for q in parts for v in s.execute(q).one())


def _judged(engine: Engine, p: Profile) -> _Judged:
    with Session(engine) as s:
        version = _store_version(s)
        ck = (str(engine.url), p)
        hit = _cache.get(ck)
        if hit and hit[0] == version:
            return hit[1]
        with _lock:
            commutes = C.table(engine)
            rows = _load(s)
            lsts = [listing_from_row(r) for r in rows]
            out = [_summary(r, lst, p, commutes) for r, lst in zip(rows, lsts, strict=True)]
            refs = T.match(out, *T.load(engine), T.history(engine))  # the tracker's rows, matched to listings
            out = [_with_tracker(x, refs.get(x.key)) for x in out]
            units = [u for r, lst, x in zip(rows, lsts, out, strict=True) for u in _units(r, lst, x, p)]
            j = _cache[ck] = (version, _Judged(out, units, {x.key: x for x in out}))
        return j[1]
