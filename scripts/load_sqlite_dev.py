"""Dev only: copy an old-schema SQLite store into a Postgres database so the UI can run against real data.

    HW_DATABASE_URL=postgresql+psycopg://... python scripts/load_sqlite_dev.py hw.db [--fake-fleet]

Migrates the target to head, empties it, and copies every table. Timestamps are shifted so the newest
one is an hour ago (pass --no-shift to keep them). Every `new` event is marked as baseline, since the
SQLite file is one first run of every source. --fake-fleet adds Procrastinate workers and jobs so the
Fleet page has something to show. Not part of the app or the tests.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import time
from datetime import UTC, datetime, timedelta

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, insert, text

from housing_watch.orm import (
    AdapterCacheRow,
    BandRow,
    Base,
    EventRow,
    ListingRow,
    RunRow,
    SnapshotRow,
    SourceStateRow,
    UnitRow,
)

ROOT = os.path.join(os.path.dirname(__file__), "..")
HOSTS = ["hetzner", "oci", "transmission", "gcp-vps", "desktop"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sqlite")
    ap.add_argument("--no-shift", action="store_true")
    ap.add_argument("--fake-fleet", action="store_true")
    a = ap.parse_args()
    src = sqlite3.connect(a.sqlite)
    src.row_factory = sqlite3.Row
    newest = max(
        src.execute(f"SELECT MAX({c}) FROM {t}").fetchone()[0] or 0
        for t, c in [("listings", "last_seen"), ("events", "at"), ("runs", "finished")]
    )
    shift = 0 if a.no_shift else time.time() - 3600 - newest

    def ts(v):
        return None if v is None else datetime.fromtimestamp(v + shift, UTC)

    def rows(table):
        return [dict(r) for r in src.execute(f"SELECT * FROM {table}")]

    eng = create_engine(os.environ["HW_DATABASE_URL"])
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    with eng.begin() as c:
        cfg.attributes["connection"] = c
        command.upgrade(cfg, "head")
    with eng.begin() as c:
        c.execute(text("TRUNCATE " + ", ".join(t.name for t in Base.metadata.sorted_tables) + " CASCADE"))
        ls = rows("listings")
        for r in ls:
            for k in ("first_seen", "last_seen", "last_changed", "gone_since"):
                r[k] = ts(r[k])
            r["senior_only"] = bool(r["senior_only"])
        c.execute(insert(ListingRow), ls)
        us = rows("units")
        for r in us:
            r["available"] = None if r["available"] is None else bool(r["available"])
        c.execute(insert(UnitRow), us)
        c.execute(insert(BandRow), rows("bands"))
        sn = rows("snapshots")
        for r in sn:
            r["links"] = json.loads(r["links"] or "[]")
            r["first_seen"], r["last_seen"], r["changed"] = ts(r["first_seen"]), ts(r["last_seen"]), ts(r["changed"])
        if sn:
            c.execute(insert(SnapshotRow), sn)
        ev = rows("events")
        for r in ev:
            r["at"], r["notified"] = ts(r["at"]), ts(r["notified"])
            r["detail"] = json.loads(r["detail"]) if r["detail"] else None
            if r["type"] in ("new", "watching"):
                r["detail"] = {**(r["detail"] or {}), "baseline": True}
        c.execute(insert(EventRow), ev)
        rn = rows("runs")
        for r in rn:
            r["started"], r["finished"] = ts(r["started"]), ts(r["finished"])
            r["ok"] = None if r["ok"] is None else bool(r["ok"])
            r["workers"] = [random.choice(HOSTS)] if a.fake_fleet else []
        c.execute(insert(RunRow), rn)
        st = rows("source_state")
        for r in st:
            for k in ("last_ok", "last_attempt", "failing_since"):
                r[k] = ts(r[k])
            r["consecutive_failures"] = r["consecutive_failures"] or 0
            r["next_due"] = r["last_attempt"] and r["last_attempt"] + timedelta(hours=24)
        c.execute(insert(SourceStateRow), st)
        ac = rows("adapter_cache")
        for r in ac:
            r["value"], r["updated"] = json.loads(r["value"]), ts(r["updated"]) or datetime.now(UTC)
        if ac:
            c.execute(insert(AdapterCacheRow), ac)
        for t in ("units", "bands", "events", "runs"):  # explicit ids were copied: move the sequences past them
            c.execute(
                text(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), (SELECT COALESCE(MAX(id), 1) FROM {t}))")
            )
    if a.fake_fleet:
        fake_fleet(eng, rn)
    print(f"loaded {len(ls)} listings, {len(us)} units, {len(ev)} events, {len(rn)} runs")


def fake_fleet(eng, runs) -> None:
    """Procrastinate's schema plus a few workers and jobs; links each run to a job on its worker."""
    from procrastinate import SyncPsycopgConnector
    from procrastinate.schema import SchemaManager

    with eng.begin() as c:
        exists = c.execute(text("SELECT to_regclass('procrastinate_jobs')")).scalar()
    if not exists:
        url = eng.url.set(drivername="postgresql").render_as_string(hide_password=False)
        conn = SyncPsycopgConnector(conninfo=url)
        conn.open()
        SchemaManager(conn).apply_schema()
        conn.close()
    now = datetime.now(UTC)
    with eng.begin() as c:
        c.execute(text("TRUNCATE procrastinate_jobs, procrastinate_workers RESTART IDENTITY CASCADE"))
        wid = {}
        for i, h in enumerate(HOSTS):
            age = timedelta(seconds=[4, 9, 2, 14, 3600 * 7][i])
            wid[h] = c.execute(
                text("INSERT INTO procrastinate_workers(last_heartbeat) VALUES (:t) RETURNING id"), {"t": now - age}
            ).scalar()
        for r in runs:
            h = r["workers"][0]
            st = "succeeded" if r["ok"] else "failed"
            jid = c.execute(
                text(
                    "INSERT INTO procrastinate_jobs(queue_name, task_name, args, status, attempts, worker_id)"
                    " VALUES (:q, 'source_run', CAST(:a AS jsonb), CAST(:s AS procrastinate_job_status), 1, :w)"
                    " RETURNING id"
                ),
                {"q": "sources", "a": json.dumps({"source": r["source"]}), "s": st, "w": wid[h]},
            ).scalar()
            c.execute(
                text(
                    "INSERT INTO procrastinate_events(job_id, type, at) VALUES (:j, 'deferred', :a), (:j, 'started',"
                    " :a), (:j, CAST(:s AS procrastinate_job_event_type), :f)"
                ),
                {"j": jid, "a": r["started"], "s": st, "f": r["finished"] or r["started"]},
            )
            c.execute(text("UPDATE runs SET job_id=:j WHERE id=:i"), {"j": jid, "i": r["id"]})
        for q, n in [("sources", 6), ("byparr", 2)]:
            for k in range(n):
                c.execute(
                    text(
                        "INSERT INTO procrastinate_jobs(queue_name, task_name, args, status, worker_id)"
                        " VALUES (:q, 'source_chunk', CAST(:a AS jsonb), CAST(:s AS procrastinate_job_status), :w)"
                    ),
                    {
                        "q": q,
                        "a": json.dumps({"source": "reside", "key": str(k)}),
                        "s": "doing" if k < 2 else "todo",
                        "w": wid[HOSTS[k % 4]] if k < 2 else None,
                    },
                )
        jid = c.execute(
            text(
                "INSERT INTO procrastinate_jobs(queue_name, task_name, args, status, attempts)"
                " VALUES ('byparr', 'source_run', '{\"source\": \"sanel\"}', 'failed', 3) RETURNING id"
            )
        ).scalar()
        c.execute(
            text("INSERT INTO procrastinate_events(job_id, type, at) VALUES (:j, 'failed', :a)"),
            {"j": jid, "a": now - timedelta(minutes=42)},
        )
        # one source in a failure streak, so the health views have something to flag
        c.execute(
            text(
                "INSERT INTO runs(source, job_id, started, finished, ok, workers, error)"
                " VALUES ('sanel', :j, :s, :f, false, ARRAY['hetzner'], 'TimeoutError: byparr render timed out')"
            ),
            {"j": jid, "s": now - timedelta(minutes=44), "f": now - timedelta(minutes=42)},
        )
        c.execute(
            text(
                "UPDATE source_state SET consecutive_failures=3, failing_since=:f, last_attempt=:a WHERE source='sanel'"
            ),
            {"f": now - timedelta(hours=9), "a": now - timedelta(minutes=42)},
        )


if __name__ == "__main__":
    main()
