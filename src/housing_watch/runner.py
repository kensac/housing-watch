"""Run one source: fetch, check, record, bookkeep. Shared by the CLI (inline) and the fleet (tasks.py).

A run records in one transaction: listings, snapshots, cache and run/source state land together or not
at all. An adapter error is a failed run, never an exception out of here (except NeedsByparr, which
the fleet turns into a re-queue).
"""

from __future__ import annotations

import copy
import logging
import os
import socket
import traceback

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from . import db
from .adapters import PLAN, Adapter, load_all
from .fetch import NeedsByparr
from .models import Result

log = logging.getLogger("housing_watch")
WORKER = os.environ.get("HW_WORKER_NAME") or socket.gethostname()


def adapter_cls(name: str) -> type[Adapter]:
    return load_all()[name]


def cache_delta(before: dict, after: dict) -> dict:
    """Top-level cache keys a chunk added or changed: what it hands back to the run's merge_cache()."""
    return {k: v for k, v in after.items() if k not in before or before[k] != v}


def fetch_all(adapter: Adapter) -> tuple[Result, dict]:
    """Every chunk in this process, merged as the fleet would. Returns (result, new cache)."""
    keys = adapter.chunks()
    if len(keys) == 1:
        return adapter.fetch_chunk(keys[0]), adapter.cache
    res, deltas = Result(), []
    for k in keys:
        part = type(adapter)(copy.deepcopy(adapter.cache))
        res.extend(part.fetch_chunk(k))
        deltas.append(cache_delta(adapter.cache, part.cache))
    return res, adapter.merge_cache(deltas)


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


def start(eng: Engine, name: str, job_id: int | None = None, worker: str = WORKER) -> int:
    with Session(eng) as s, s.begin():
        return db.start_run(s, name, job_id, worker)


def record(eng: Engine, run_id: int, name: str, res: Result, cache: dict, workers: list[str]) -> tuple[str, int]:
    """Check and record a source's complete Result; raises when it is implausibly small."""
    cls = adapter_cls(name)
    if len(res.listings) + len(res.snapshots) < cls.min_listings:
        raise RuntimeError(
            f"only {len(res.listings)} listings / {len(res.snapshots)} pages, expected >= {cls.min_listings}"
        )
    with Session(eng) as s, s.begin():
        baseline = db.is_baseline(s, name)
        n = db.record_listings(s, name, res.listings, baseline=baseline)  # an empty list marks the old ones gone
        n += db.record_snapshots(s, name, res.snapshots, baseline=baseline)
        db.save_cache(s, name, {k: v for k, v in cache.items() if k != PLAN})
        db.finish_run(
            s, run_id, name, True, listings=len(res.listings), snapshots=len(res.snapshots), events=n,
            every_hours=cls.every_hours, workers=workers,
        )  # fmt: skip
        db.clear_chunks(s, run_id)
    return f"ok {len(res.listings)}L/{len(res.snapshots)}P, {n} events", n


def fail(eng: Engine, run_id: int, name: str, err: str, workers: list[str] | None = None) -> str:
    cls = load_all().get(name)
    with Session(eng) as s, s.begin():
        db.finish_run(s, run_id, name, False, error=err, every_hours=cls.every_hours if cls else None, workers=workers)
        db.clear_chunks(s, run_id)
    return f"FAIL {err[:200]}"


def run_inline(eng: Engine, name: str) -> str:
    """One full run in this process, chunks included, without the queue."""
    run_id = start(eng, name)
    try:
        with Session(eng) as s:
            adapter = adapter_cls(name)(db.load_cache(s, name))
        res, cache = fetch_all(adapter)
        return record(eng, run_id, name, res, cache, [WORKER])[0]
    except NeedsByparr as e:
        with Session(eng) as s, s.begin():
            db.discard_run(s, run_id)
        return f"SKIP {e} (set BYPARR_URL)"
    except Exception as e:  # noqa: BLE001 - one broken source must not stop the others
        log.warning("%s failed: %s\n%s", name, _err(e), traceback.format_exc(limit=3))
        return fail(eng, run_id, name, _err(e), [WORKER])


def merge(eng: Engine, run_id: int, name: str) -> tuple[str, int] | None:
    """Merge a chunked run's results and record them. None when the run is already finished."""
    with Session(eng) as s:
        plan = db.open_plan(s, run_id)
        if plan is None:
            return None
        parts = db.chunk_results(s, run_id)  # loaded rows stay readable after the session closes
    workers = [p.worker for p in parts if p.worker]
    bad = next((p for p in parts if not p.ok), None)
    if bad:
        return fail(eng, run_id, name, f"chunk {bad.key!r}: {bad.error}", workers), 0
    try:
        res = Result()
        for p in parts:
            res.extend(Result.from_json(p.result))
        cache = adapter_cls(name)(plan[1]).merge_cache([p.cache or {} for p in parts])
        return record(eng, run_id, name, res, cache, workers)
    except Exception as e:  # noqa: BLE001 - recorded as a failed run
        log.warning("%s merge failed: %s\n%s", name, _err(e), traceback.format_exc(limit=3))
        return fail(eng, run_id, name, _err(e), workers), 0
