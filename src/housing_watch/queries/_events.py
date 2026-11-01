"""The Events page, an event's typed detail, and the digest's event reads."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .. import filters as F
from ..criteria import PROFILE, Profile, Verdict
from ..mapping import listing_from_row
from ..models import Band, Borough, EventType, Key, Kind, Listing, PublishedStatus, Serial, Size
from ..orm import EventRow, ListingRow
from ._common import day, internal, is_baseline, known
from ._judging import _load, judged_by_key
from ._search import filter_listings


@dataclass
class EventView:
    id: Serial
    at: datetime
    source: str
    key: Key | None
    type: EventType
    summary: str | None
    detail: dict | None = internal()  # stored as recorded; the API reads it typed through event_detail()
    notified: datetime | None = None
    baseline: bool = False
    title: str | None = None
    verdict: Verdict | None = None


@dataclass
class BriefUnit:
    size: Size | None
    unit_no: str | None
    rent: float | None
    ami: int | None
    available: bool | None
    set_aside: str | None
    bands: list[Band]


@dataclass
class Brief:
    """One side of a `changed` event: what db._brief keeps of the listing."""

    status: PublishedStatus | None
    deadline: date | None
    kind: Kind | None
    borough: Borough | None
    senior_only: bool | None
    units: list[BriefUnit]

    @classmethod
    def of(cls, d: dict) -> Brief:
        def unit(u: list) -> BriefUnit:
            u = [*u, *[None] * (7 - len(u))]  # events from before bands were kept have six
            return BriefUnit(known(u[0], Size), *u[1:6], [Band(*b) for b in u[6] or []])

        return cls(known(d.get("status"), PublishedStatus), day(d.get("deadline")), known(d.get("kind"), Kind),
                   known(d.get("borough"), Borough), d.get("senior_only"),
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


def _event_view(e: EventRow) -> EventView:
    return EventView(e.id, e.at, e.source, e.key, e.type, e.summary, e.detail, e.notified, is_baseline(e.detail))


@dataclass
class EventPage:
    items: list[EventView]
    next_before: int | None  # pass as `before` for the next (older) batch
    total: int


def _attach(engine: Engine, evs: list[EventView], p: Profile) -> list[EventView]:
    by_key = judged_by_key(engine, p)
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
