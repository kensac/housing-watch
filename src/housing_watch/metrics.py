"""Prometheus metrics on HW_METRICS_PORT (default 9091).

Run duration and failures are this worker's own. Job counts and per-source state are read from the
shared database at scrape time, so every worker reports the same fleet-wide values: aggregate those
with max(), not sum().
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from prometheus_client import REGISTRY, Counter, Histogram, start_http_server
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import Engine, text

log = logging.getLogger("housing_watch")

RUN_SECONDS = Histogram(
    "hw_source_run_seconds", "Source run duration on this worker", ["source", "ok"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 3600),
)  # fmt: skip
RUN_FAILURES = Counter("hw_source_failures", "Failed source runs on this worker", ["source"])


def observe_run(source: str, ok: bool, seconds: float) -> None:
    RUN_SECONDS.labels(source, str(ok).lower()).observe(seconds)
    if not ok:
        RUN_FAILURES.labels(source).inc()


class DbCollector(Collector):
    def __init__(self, engine: Callable[[], Engine]):
        self.engine = engine

    def collect(self):
        jobs = GaugeMetricFamily("hw_jobs", "Procrastinate jobs by task and status", labels=["task", "status"])
        last_ok = GaugeMetricFamily(
            "hw_source_last_success_timestamp_seconds", "Last successful run per source", labels=["source"]
        )
        fails = GaugeMetricFamily("hw_source_consecutive_failures", "Consecutive failed runs", labels=["source"])
        try:
            with self.engine().connect() as c:
                for task, status, n in c.execute(
                    text("SELECT task_name, status::text, count(*) FROM procrastinate_jobs GROUP BY 1, 2")
                ):
                    jobs.add_metric([task, status], n)
                for source, ok, nfail in c.execute(
                    text("SELECT source, extract(epoch FROM last_ok), consecutive_failures FROM source_state")
                ):
                    if ok is not None:
                        last_ok.add_metric([source], float(ok))
                    fails.add_metric([source], nfail or 0)
        except Exception as e:  # noqa: BLE001 - a scrape must not crash the worker
            log.warning("metrics: database unavailable: %s", e)
            return
        yield from (jobs, last_ok, fails)


_started = False


def serve(engine: Callable[[], Engine], port: int | None = None, addr: str = "0.0.0.0") -> None:
    """Start the exporter once per process; port 0 or HW_METRICS_PORT=0 disables it."""
    global _started
    port = int(os.environ.get("HW_METRICS_PORT", "9091")) if port is None else port
    if _started or not port:
        return
    REGISTRY.register(DbCollector(engine))
    start_http_server(port, addr=addr)
    _started = True
    log.info("metrics on %s:%d", addr, port)
