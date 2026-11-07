"""The Sources page: each source's health and listings."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..criteria import ELIGIBLE, POSSIBLE, PROFILE, Profile
from ..orm import RunRow, SourceStateRow
from ._common import Health
from ._judging import judged


@dataclass
class SourceHealth:
    source: str
    title: str | None
    url: str | None
    every_hours: float | None
    health: Health  # ok, failing, or never (no run has succeeded yet)
    paused: bool  # the owner paused it: no runs until resumed
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
        from ..adapters import load_all

        return load_all()
    except Exception:  # noqa: BLE001 - the health table must render even if one adapter fails to import
        return {}


def sources(engine: Engine, p: Profile = PROFILE) -> list[SourceHealth]:
    listed, eligible, possible = Counter(), Counter(), Counter()  # by source, gone listings left out
    for x in judged(engine, p):
        if x.status != "gone":
            listed[x.source] += 1
            eligible[x.source] += x.verdict == ELIGIBLE
            possible[x.source] += x.verdict == POSSIBLE
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
    for name in sorted(set(state) | set(last) | set(listed) | set(ads)):
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
                paused=bool(st and st.paused),
                last_ok=st and st.last_ok,
                last_attempt=st and st.last_attempt,
                failing_since=st and st.failing_since,
                consecutive_failures=fails,
                next_due=st and st.next_due,
                last_duration=dur,
                last_error=run.error if run and not run.ok else None,
                last_events=run.events if run else None,
                listings=listed[name],
                eligible=eligible[name],
                possible=possible[name],
                workers=workers.get(name, []),
            )
        )
    out.sort(key=lambda x: (-x.consecutive_failures, x.health == "never", x.source))
    return out
