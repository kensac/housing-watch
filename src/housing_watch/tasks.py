"""The worker fleet: Procrastinate tasks on HW_DATABASE_URL. Contract: docs/ARCHITECTURE.md (Fleet).

Queues: `sources` (every worker), `byparr` (workers with HW_WORKER_CAPS containing byparr) and `digest`
(workers with the digest cap: the one that has the profile and SMTP credentials).

Chunk results flow back through the database, because Procrastinate keeps no return values:
source_run plans the chunks onto the run row (runs.plan), each source_chunk writes its Result and cache
delta to chunk_results, and the chunk that completes the set defers source_merge, which merges,
records and saves the cache. Nothing blocks waiting on another job, so chunked runs cannot starve the
worker slots their own chunks need. Chunk i takes Procrastinate lock "<source>#<i % parallel>", which
caps a source at `parallel` chunks in flight.
"""

from __future__ import annotations

import copy
import logging
import os
import threading
import time
import traceback
from datetime import datetime, timedelta

from procrastinate import App, PsycopgConnector, RetryDecision
from procrastinate.exceptions import AlreadyEnqueued
from procrastinate.retry import BaseRetryStrategy
from sqlalchemy import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from . import db, metrics, runner
from .adapters import load_all
from .fetch import NeedsByparr

log = logging.getLogger("housing_watch")

SOURCES, BYPARR, DIGEST = "sources", "byparr", "digest"
DIGEST_HOURS = [int(h) for h in os.environ.get("HW_DIGEST_HOURS", "8,13,19").split(",")]  # container local time
RUN_TIMEOUT = timedelta(hours=float(os.environ.get("HW_RUN_TIMEOUT_HOURS", "6")))  # unfinished runs older are lost
MERGE_GRACE = timedelta(minutes=2)  # a complete chunked run not merged by then gets its merge re-queued


def libpq_url(url: str) -> str:
    """postgresql+psycopg://... (SQLAlchemy) -> postgresql://... (libpq, for Procrastinate's pool)."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False) if url else ""


def queues(caps: str | None = None) -> list[str]:
    caps_set = {c.strip() for c in (caps if caps is not None else os.environ.get("HW_WORKER_CAPS", "")).split(",")}
    return [SOURCES] + [q for q in (BYPARR, DIGEST) if q in caps_set]


app = App(connector=PsycopgConnector(conninfo=libpq_url(db.DATABASE_URL)))

_engine: Engine | None = None
_engine_lock = threading.Lock()


def engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = db.engine()
        return _engine


def set_engine(eng: Engine | None) -> None:
    global _engine
    _engine = eng


class Retry(BaseRetryStrategy):
    """NeedsByparr moves the job to the byparr queue; anything else retries with a linear backoff."""

    def __init__(self, retries: int, wait: int):
        self.retries, self.wait = retries, wait

    def get_retry_decision(self, *, exception, job) -> RetryDecision | None:
        if isinstance(exception, NeedsByparr):
            return RetryDecision(queue=BYPARR) if job.queue != BYPARR else None
        if job.attempts >= self.retries:
            return None
        return RetryDecision(retry_in={"seconds": self.wait * (job.attempts + 1)})


# A source_run's own adapter errors are recorded as a failed run, not raised: Procrastinate retries only
# infrastructure trouble (database, a worker dying mid-job) and the byparr hand-off.
RUN_RETRY = Retry(retries=2, wait=60)
CHUNK_RETRY = Retry(retries=2, wait=30)


def _defer(task, *, queueing_lock: str | None = None, lock: str | None = None, queue: str | None = None, **kw):
    """Defer, treating "an identical job is already waiting" (same queueing_lock) as done."""
    try:
        return task.configure(queueing_lock=queueing_lock, lock=lock, queue=queue).defer(**kw)
    except AlreadyEnqueued:
        return None


def _learned_queue(source: str) -> str:
    with Session(engine()) as s:
        st = s.get(db.SourceStateRow, source)
        return st.queue if st and st.queue else SOURCES


def _needs_byparr(source: str) -> None:
    with Session(engine()) as s, s.begin():
        db.set_queue(s, source, BYPARR)


def _after_record(source: str, events: int) -> None:
    if events:
        _defer(digest, queueing_lock="digest:urgent", lock="digest")


@app.periodic(cron="* * * * *")
@app.task(name="tick", queue=SOURCES, queueing_lock="tick", retry=False)
def tick(timestamp: int) -> str:
    """Enqueue due sources, recover lost runs, and the digest at its slots."""
    eng = engine()
    every = {name: cls.every_hours for name, cls in load_all().items()}
    with Session(eng) as s, s.begin():
        due = db.claim_due(s, every)
        stale = db.stale_runs(s, RUN_TIMEOUT)
        mergeable = db.mergeable_runs(s, MERGE_GRACE)
    for source, queue in due:
        _defer(source_run, queueing_lock=f"run:{source}", lock=f"source:{source}", queue=queue, source=source)
    for run_id, source in stale:
        runner.fail(eng, run_id, source, f"abandoned: unfinished after {RUN_TIMEOUT}")
    for run_id, source in mergeable:
        _defer_merge(source, run_id)
    at = datetime.fromtimestamp(timestamp)
    if at.minute == 0 and at.hour in DIGEST_HOURS:
        _defer(digest, queueing_lock="digest:slot", lock="digest", scheduled=True)
    return f"{len(due)} due, {len(stale)} abandoned, {len(mergeable)} merges re-queued"


@app.task(name="source_run", queue=SOURCES, pass_context=True, retry=RUN_RETRY)
def source_run(context, source: str) -> str:
    eng, cls = engine(), load_all()[source]
    worker = context.worker_name or runner.WORKER
    t0 = time.monotonic()
    run_id = runner.start(eng, source, context.job.id, worker)
    try:
        with Session(eng) as s:
            adapter = cls(db.load_cache(s, source))
        keys = adapter.chunks()
        if len(keys) == 1:
            out, n = runner.record(eng, run_id, source, adapter.fetch_chunk(keys[0]), adapter.cache, [worker])
            metrics.observe_run(source, True, time.monotonic() - t0)
            _after_record(source, n)
            return out
    except NeedsByparr:
        with Session(eng) as s, s.begin():
            db.discard_run(s, run_id)
        _needs_byparr(source)
        raise
    except Exception as e:  # noqa: BLE001 - an adapter error is a failed run, not a job error
        log.warning("%s failed: %s\n%s", source, e, traceback.format_exc(limit=3))
        metrics.observe_run(source, False, time.monotonic() - t0)
        return runner.fail(eng, run_id, source, f"{type(e).__name__}: {e}", [worker])
    with Session(eng) as s, s.begin():
        db.plan_run(s, run_id, keys, adapter.cache)
    queue = context.job.queue or SOURCES
    par = max(1, cls.parallel)
    for i, key in enumerate(keys):
        source_chunk.configure(lock=f"{source}#{i % par}", queue=queue).defer(source=source, key=key, run_id=run_id)
    return f"planned {len(keys)} chunks (run {run_id}, {par} at a time)"


@app.task(name="source_chunk", queue=SOURCES, pass_context=True, retry=CHUNK_RETRY)
def source_chunk(context, source: str, key: str, run_id: int) -> str:
    eng, cls = engine(), load_all()[source]
    worker = context.worker_name or runner.WORKER
    with Session(eng) as s:
        plan = db.open_plan(s, run_id)
    if plan is None:
        return "run already finished"
    adapter = cls(copy.deepcopy(plan[1]))
    try:
        res = adapter.fetch_chunk(key)
    except NeedsByparr:
        _needs_byparr(source)
        raise
    except Exception as e:
        if CHUNK_RETRY.get_retry_decision(exception=e, job=context.job) is None:  # last attempt: the run fails
            with Session(eng) as s, s.begin():
                done = db.save_chunk(s, run_id, key, ok=False, worker=worker, error=f"{type(e).__name__}: {e}")
            if done:
                _defer_merge(source, run_id)
        raise
    with Session(eng) as s, s.begin():
        done = db.save_chunk(
            s, run_id, key, ok=True, result=res.to_json(), cache=runner.cache_delta(plan[1], adapter.cache),
            worker=worker,
        )  # fmt: skip
    if done:
        _defer_merge(source, run_id)
    return f"chunk {key!r}: {len(res.listings)}L/{len(res.snapshots)}P"


def _defer_merge(source: str, run_id: int) -> None:
    _defer(source_merge, queueing_lock=f"merge:{run_id}", lock=f"source:{source}", source=source, run_id=run_id)


@app.task(name="source_merge", queue=SOURCES, retry=RUN_RETRY)
def source_merge(source: str, run_id: int) -> str:
    eng = engine()
    with Session(eng) as s:
        run = s.get(db.RunRow, run_id)
        started = run.started if run else None
    out = runner.merge(eng, run_id, source)
    if out is None:
        return "run already finished"
    ok = out[0].startswith("ok")
    metrics.observe_run(source, ok, (db.utcnow() - started).total_seconds() if started else 0)
    _after_record(source, out[1])
    return out[0]


@app.task(name="digest", queue=DIGEST, retry=Retry(retries=3, wait=300))
def digest(scheduled: bool = False) -> str:
    """The scheduled digest, or (scheduled=False, queued after a run recorded events) mail now if urgent."""
    from . import digest as dg

    eng = engine()
    if not scheduled and not dg.urgent(eng):
        return "nothing urgent"
    return dg.send(eng)


@app.periodic(cron="*/5 * * * *")
@app.task(name="retry_stalled", queue=SOURCES, queueing_lock="retry_stalled", pass_context=True)
async def retry_stalled(context, timestamp: int) -> str:
    """Jobs whose worker stopped heartbeating go back to todo (Procrastinate's documented pattern)."""
    jobs = list(await context.app.job_manager.get_stalled_jobs())
    for job in jobs:
        await context.app.job_manager.retry_job(job)
    return f"{len(jobs)} stalled jobs retried"


@app.periodic(cron="23 4 * * *")
@app.task(name="cleanup", queue=SOURCES, queueing_lock="cleanup", pass_context=True)
async def cleanup(context, timestamp: int) -> str:
    """Finished jobs are history only: keep successes 2 days and failures a week."""
    jm = context.app.job_manager
    await jm.delete_old_jobs(nb_hours=48)
    await jm.delete_old_jobs(nb_hours=24 * 7, include_failed=True, include_cancelled=True, include_aborted=True)
    return "ok"


def run_worker(name: str, concurrency: int, worker_queues: list[str]) -> None:
    app.run_worker(name=name, concurrency=concurrency, queues=worker_queues)
