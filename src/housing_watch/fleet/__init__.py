"""The worker fleet: who fetches what, when, from which IP. Contract: docs/ARCHITECTURE.md (Fleet).

`runner` runs and records one source, `tasks` is the Procrastinate app and its tasks, `metrics` is the
Prometheus exporter. Code outside the fleet imports from here.
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import Engine

from .runner import WORKER, fetch_all, run_inline

log = logging.getLogger("housing_watch")


def work(eng: Engine) -> None:
    """Serve metrics and run this process as a Procrastinate worker until it stops."""
    from . import metrics, tasks

    tasks.set_engine(eng)
    metrics.serve(tasks.engine)
    name = WORKER
    concurrency = int(os.environ.get("HW_WORKER_CONCURRENCY", "4"))
    queues = tasks.queues()
    log.info("worker %s: queues %s, concurrency %d", name, queues, concurrency)
    tasks.run_worker(name, concurrency, queues)


__all__ = ["fetch_all", "run_inline", "work"]
