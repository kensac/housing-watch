"""Postgres store and change detection. Pure data: no eligibility logic lives here.

Every listing a source publishes is stored, normalized into listings -> units -> bands, whatever it
would be judged. Change events come only from the data:

  new | changed (detail.before/after) | closed | gone | reopened             listings
  watching (first sight) | page_changed (detail.added/removed/new_links)    snapshots
  source_failed (2nd and 10th consecutive failure) | source_recovered       sources

Events recorded by a source's first successful run carry detail.baseline=true: they describe what was
already there, not what changed, so the digest can collapse them.

Functions take a Session and never commit; callers own the transaction (one per recorded run).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, create_engine, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from .mapping import apply_listing, listing_from_row
from .models import Listing, Snapshot
from .orm import (
    AdapterCacheRow,
    ChunkResultRow,
    EventRow,
    ListingRow,
    RunRow,
    SnapshotRow,
    SourceStateRow,
    UnitRow,
)

DATABASE_URL = os.environ.get("HW_DATABASE_URL", "")
MIGRATE_LOCK = 0x68770001  # pg advisory lock id held while migrating
DUE_SLACK = timedelta(seconds=60)  # a run finishing a little late still counts as on schedule


def utcnow() -> datetime:
    return datetime.now(UTC)


def engine(url: str | None = None) -> Engine:
    url = url or DATABASE_URL
    if not url:
        raise RuntimeError("set HW_DATABASE_URL (postgresql+psycopg://user:pass@host:port/db)")
    return create_engine(url, pool_pre_ping=True)


def migrate(eng: Engine) -> None:
    """alembic upgrade head, under an advisory lock so workers starting together migrate once."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    with eng.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": MIGRATE_LOCK})
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


# ---- change detection ------------------------------------------------------------------------------


def _fp(lst: Listing) -> str:
    """Hash of everything that, if it moves, is a change in the listing itself."""
    units = sorted(
        (
            (u.key(), u.rent, u.available, tuple((b.household, b.income_min, b.income_max) for b in u.bands))
            for u in lst.units
        ),
        key=repr,  # fields can be None, which tuples won't compare
    )
    return hashlib.sha256(
        json.dumps([lst.status, lst.deadline, lst.borough, lst.senior_only, lst.kind, units], default=str).encode()
    ).hexdigest()


def _brief(lst: Listing) -> dict:
    return {
        "status": lst.status,
        "deadline": lst.deadline,
        "units": [[u.size, u.unit_no, u.rent, u.ami, u.available] for u in lst.units],
    }


def _event(s: Session, source, key, typ, summary="", detail=None, now=None, baseline=False) -> None:
    if baseline:
        detail = {**(detail or {}), "baseline": True}
    s.add(EventRow(at=now or utcnow(), source=source, key=key, type=typ, summary=summary, detail=detail))


def is_baseline(s: Session, source: str) -> bool:
    """True until the source's first successful run is recorded."""
    return s.scalar(select(SourceStateRow.last_ok).where(SourceStateRow.source == source)) is None


def load_listing(s: Session, key: str) -> Listing | None:
    r = s.get(ListingRow, key)
    return listing_from_row(r) if r else None


def record_listings(
    s: Session, source: str, listings: list[Listing], now: datetime | None = None, baseline: bool = False
) -> int:
    """Upsert a source's complete listing set; anything it no longer lists is marked gone. Returns events."""
    now = now or utcnow()
    rows = {
        r.key: r
        for r in s.scalars(
            select(ListingRow)
            .where(ListingRow.source == source)
            .options(selectinload(ListingRow.units).selectinload(UnitRow.bands))
        )
    }
    seen: set[str] = set()
    n = 0
    for lst in listings:
        key = lst.key()
        if key in seen:
            continue  # an adapter listing one thing twice must not flap
        seen.add(key)
        fp = _fp(lst)
        row = rows.get(key)
        if row is None:
            row = apply_listing(lst, ListingRow(fingerprint=fp, first_seen=now, last_seen=now, last_changed=now))
            row.source = source  # the run's source owns the row, as gone detection assumes
            s.add(row)
            _event(s, source, key, "new", lst.title, None, now, baseline)
            n += 1
            continue
        typ = None
        if row.gone_since is not None or (row.status == "closed" and lst.status != "closed"):
            typ = "reopened"
        elif row.fingerprint != fp:
            typ = "closed" if lst.status == "closed" and row.status != "closed" else "changed"
        detail = {"before": _brief(listing_from_row(row)), "after": _brief(lst)} if typ == "changed" else None
        apply_listing(lst, row, units=row.fingerprint != fp)
        row.source, row.fingerprint, row.last_seen, row.gone_since = source, fp, now, None
        if typ:
            row.last_changed = now
            _event(s, source, key, typ, lst.title, detail, now, baseline)
            n += 1
    for key, row in rows.items():
        if key not in seen and row.gone_since is None:
            row.gone_since = now
            _event(s, source, key, "gone", row.title, None, now, baseline)
            n += 1
    return n


def record_snapshots(
    s: Session, source: str, snaps: list[Snapshot], now: datetime | None = None, baseline: bool = False
) -> int:
    now = now or utcnow()
    n = 0
    for sn in snaps:
        dg = hashlib.sha256((sn.text + "\n" + "\n".join(sorted(sn.links))).encode()).hexdigest()
        row = s.get(SnapshotRow, sn.key())
        if row is None:
            s.add(
                SnapshotRow(
                    key=sn.key(),
                    source=source,
                    url=sn.url,
                    digest=dg,
                    text=sn.text,
                    links=sorted(sn.links),
                    first_seen=now,
                    last_seen=now,
                )  # fmt: skip
            )
            _event(s, source, sn.key(), "watching", sn.url, None, now, baseline)
            n += 1
            continue
        row.last_seen = now
        if row.digest == dg:
            continue
        old, new = (row.text or "").splitlines(), sn.text.splitlines()
        old_set, new_set = set(old), set(new)
        detail = {
            "added": [x for x in new if x not in old_set][:40],
            "removed": [x for x in old if x not in new_set][:20],
            "new_links": sorted(set(sn.links) - set(row.links or [])),
        }
        row.url, row.digest, row.text, row.links, row.changed = sn.url, dg, sn.text, sorted(sn.links), now
        _event(s, source, sn.key(), "page_changed", sn.url, detail, now, baseline)
        n += 1
    return n


# ---- runs and source state -------------------------------------------------------------------------


def start_run(s: Session, source: str, job_id: int | None = None, worker: str | None = None) -> int:
    run = RunRow(source=source, job_id=job_id, started=utcnow(), workers=[worker] if worker else [])
    s.add(run)
    s.flush()
    return run.id


def discard_run(s: Session, run_id: int) -> None:
    """Forget a run that never really happened (it was handed to another queue)."""
    run = s.get(RunRow, run_id)
    if run is not None:
        s.delete(run)


def _state(s: Session, source: str) -> SourceStateRow:
    st = s.get(SourceStateRow, source, with_for_update=True)
    if st is None:
        st = SourceStateRow(source=source, consecutive_failures=0)
        s.add(st)
    return st


def finish_run(
    s: Session,
    run_id: int,
    source: str,
    ok: bool,
    *,
    listings: int = 0,
    snapshots: int = 0,
    events: int = 0,
    error: str | None = None,
    every_hours: float | None = None,
    workers: list[str] | None = None,
) -> None:
    now = utcnow()
    run = s.get(RunRow, run_id)
    run.finished, run.ok, run.listings, run.snapshots, run.events, run.error = (
        now, ok, listings, snapshots, events, error,
    )  # fmt: skip
    if workers is not None:
        run.workers = sorted(set(workers))
    st = _state(s, source)
    st.last_attempt = now
    if every_hours is not None:
        st.next_due = now + timedelta(hours=every_hours) - DUE_SLACK
    if ok:
        if st.failing_since:
            _event(s, source, None, "source_recovered", source, None, now)
        st.last_ok, st.failing_since, st.consecutive_failures = now, None, 0
        return
    st.consecutive_failures = (st.consecutive_failures or 0) + 1
    st.failing_since = st.failing_since or now
    if st.consecutive_failures in (2, 10):  # one transient failure is noise; report on the 2nd and the 10th
        detail = {"error": (error or "")[:500], "fails": st.consecutive_failures}
        _event(s, source, None, "source_failed", source, detail, now)


def claim_due(s: Session, every: dict[str, float], now: datetime | None = None) -> list[tuple[str, str | None]]:
    """Sources whose next_due has passed, as (source, learned queue); each is pushed a full cycle ahead.

    Pushing next_due at claim time means a slow or lost run is not re-enqueued every tick; finish_run
    sets it again from the real finish time.
    """
    now = now or utcnow()
    known = {st.source: st for st in s.scalars(select(SourceStateRow).with_for_update(skip_locked=True))}
    out = []
    for source, hours in sorted(every.items()):
        st = known.get(source)
        if st is None:
            if s.get(SourceStateRow, source) is not None:
                continue  # locked by a run finishing right now
            st = SourceStateRow(source=source, consecutive_failures=0)
            s.add(st)
        elif st.next_due is not None and st.next_due > now:
            continue
        st.next_due = now + timedelta(hours=hours)
        out.append((source, st.queue))
    return out


def set_queue(s: Session, source: str, queue: str | None) -> None:
    _state(s, source).queue = queue


# ---- chunked runs ----------------------------------------------------------------------------------


def plan_run(s: Session, run_id: int, keys: list[str], cache: dict) -> None:
    run = s.get(RunRow, run_id)
    run.chunks, run.plan = len(keys), {"keys": keys, "cache": _jsonable(cache)}


def open_plan(s: Session, run_id: int) -> tuple[list[str], dict] | None:
    """(keys, cache) of an unfinished chunked run, or None when it is finished or gone."""
    run = s.get(RunRow, run_id)
    if run is None or run.finished is not None or run.plan is None:
        return None
    return run.plan["keys"], run.plan["cache"]


def save_chunk(
    s: Session,
    run_id: int,
    key: str,
    *,
    ok: bool,
    result: dict | None = None,
    cache: dict | None = None,
    worker: str | None = None,
    error: str | None = None,
) -> bool:
    """Store one chunk's outcome. True when every planned chunk of the run now has one."""
    run = s.get(RunRow, run_id, with_for_update=True)  # serializes the completion check across workers
    if run is None or run.finished is not None:
        return False
    vals = dict(
        pos=run.plan["keys"].index(key), ok=ok, result=result, cache=_jsonable(cache), worker=worker, error=error
    )
    s.execute(
        insert(ChunkResultRow)
        .values(run_id=run_id, key=key, **vals)
        .on_conflict_do_update(index_elements=["run_id", "key"], set_={**vals, "finished": func.now()})
    )
    done = s.scalar(select(func.count()).select_from(ChunkResultRow).where(ChunkResultRow.run_id == run_id))
    return done >= run.chunks


def chunk_results(s: Session, run_id: int) -> list[ChunkResultRow]:
    return list(s.scalars(select(ChunkResultRow).where(ChunkResultRow.run_id == run_id).order_by(ChunkResultRow.pos)))


def clear_chunks(s: Session, run_id: int) -> None:
    s.execute(delete(ChunkResultRow).where(ChunkResultRow.run_id == run_id))


def stale_runs(s: Session, older_than: timedelta) -> list[tuple[int, str]]:
    """Unfinished runs started before now - older_than (their jobs were lost)."""
    q = select(RunRow.id, RunRow.source).where(RunRow.finished.is_(None), RunRow.started < utcnow() - older_than)
    return [(r.id, r.source) for r in s.execute(q)]


def mergeable_runs(s: Session, older_than: timedelta) -> list[tuple[int, str]]:
    """Unfinished chunked runs with every chunk in, the last older than older_than (merge never queued)."""
    q = (
        select(RunRow.id, RunRow.source)
        .join(ChunkResultRow, ChunkResultRow.run_id == RunRow.id)
        .where(RunRow.finished.is_(None), RunRow.chunks.is_not(None))
        .group_by(RunRow.id)
        .having(func.count() >= RunRow.chunks, func.max(ChunkResultRow.finished) < utcnow() - older_than)
    )
    return [(r.id, r.source) for r in s.execute(q)]


# ---- adapter cache ---------------------------------------------------------------------------------


def _jsonable(v):
    return None if v is None else json.loads(json.dumps(v, default=str))


def load_cache(s: Session, source: str) -> dict:
    row = s.get(AdapterCacheRow, source)
    return dict(row.value) if row else {}


def save_cache(s: Session, source: str, value: dict) -> None:
    value = _jsonable(value)
    s.execute(
        insert(AdapterCacheRow)
        .values(source=source, value=value, updated=func.now())
        .on_conflict_do_update(index_elements=["source"], set_={"value": value, "updated": func.now()})
    )


def source_states(s: Session) -> dict[str, SourceStateRow]:
    return {st.source: st for st in s.scalars(select(SourceStateRow))}
