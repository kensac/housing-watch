"""The Overview page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..criteria import ELIGIBLE, PROFILE, Profile, Verdict
from ..models import PublishedStatus, values
from ..orm import EventRow, SourceStateRow
from ._common import VERDICTS, now
from ._events import EventView, _attach, _event_view
from ._judging import ListingSummary, judged
from ._sources import sources


@dataclass
class MatrixCell:
    verdict: Verdict
    status: PublishedStatus
    count: int


@dataclass
class Overview:
    matrix: list[MatrixCell]  # listed listings by verdict and status (gone left out)
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
    n_sources: int  # every source, as the Sources page lists them, with listings or not


def overview(engine: Engine, p: Profile = PROFILE) -> Overview:
    allx = judged(engine, p)
    counts = {(v, st): 0 for v in VERDICTS for st in values(PublishedStatus)}
    for x in allx:
        if x.status != "gone" and not x.copy_of:  # a copy is counted once, as the listing it copies
            counts[x.verdict, x.status] += 1
    matrix = [MatrixCell(v, st, n) for (v, st), n in counts.items()]
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
    live = [x for x in allx if x.verdict == ELIGIBLE and x.status in ("open", "waitlist", "upcoming") and not x.copy_of]
    live.sort(key=lambda x: (x.status != "open", -x.last_changed.timestamp()))
    return Overview(
        matrix=matrix,
        eligible_open=sum(1 for x in live if x.status == "open"),
        eligible_live=len(live),
        eligible_now=live[:10],
        recent=_attach(engine, recent, p),
        total=sum(1 for x in allx if x.status != "gone" and not x.copy_of),
        n_sources=len(sources(engine, p)),
        **ov,
    )
