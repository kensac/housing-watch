"""The fleet: hosts from runs, and Procrastinate's tables (absent until a worker has applied its schema)."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..models import Serial
from ._common import JobStatus, now


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
class JobArgs:
    """A task's keyword arguments, as queued: every parameter a task in tasks.py takes (each takes some of them)."""

    source: str | None = None
    key: str | None = None  # the chunk's key
    run_id: int | None = None
    timestamp: int | None = None  # the periodic tick that deferred it, as a Unix time
    scheduled: bool | None = None

    @classmethod
    def of(cls, d: dict | None) -> JobArgs:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})


@dataclass
class FleetFailure:
    """A failed Procrastinate job."""

    id: Serial
    queue_name: str
    task_name: str
    args: JobArgs
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
class QueueCount:
    queue: str
    status: JobStatus
    count: int


@dataclass
class Fleet:
    available: bool  # Procrastinate's tables exist
    workers: list[FleetWorker] = field(default_factory=list)
    queues: list[QueueCount] = field(default_factory=list)  # jobs by queue and status
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
        queues = [
            QueueCount(q, st, n)
            for q, st, n in c.execute(
                text("SELECT queue_name, status::text, count(*) FROM procrastinate_jobs GROUP BY 1, 2 ORDER BY 1, 2")
            )
        ]
        failures = [
            FleetFailure(**{**r._mapping, "args": JobArgs.of(r.args)})
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
