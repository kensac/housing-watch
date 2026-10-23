"""Shared seed data for the read-side tests (queries, digest, web). Not a test module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session  # noqa: F401 - re-exported for tests

from housing_watch.mapping import apply_listing
from housing_watch.models import Band, Listing, Unit
from housing_watch.orm import EventRow, ListingRow, RunRow, SourceStateRow

NOW = datetime.now(UTC)
GOOD = Unit("studio", 2400.0, 130, "4B", [Band(1, 82557, 154440), Band(2, 82557, 176410)])


def L(source, ext_id, borough, status="open", units=None, **kw) -> Listing:
    return Listing(source, ext_id, f"{source} {ext_id}", f"https://{source}.example/{ext_id}", borough=borough,
                   status=status, units=[GOOD] if units is None else units, **kw)  # fmt: skip


def add_listing(s: Session, lst: Listing, age=timedelta(hours=1), gone=False) -> None:
    t = NOW - age
    row = ListingRow(fingerprint="x", first_seen=t, last_seen=t, last_changed=t, gone_since=t if gone else None)
    s.add(apply_listing(lst, row))


def event(s: Session, key, typ, source=None, age=timedelta(minutes=30), detail=None, notified=None) -> None:
    s.add(EventRow(at=NOW - age, source=source or key.split(":")[0], key=key, type=typ, summary=key,
                   detail=detail, notified=notified))  # fmt: skip


def seed(engine) -> None:
    """a:1 eligible (Queens), a:2 excluded (Bronx), b:1 possible (no unit details), b:2 closed, b:3 gone."""
    with Session(engine) as s, s.begin():
        add_listing(s, L("a", "1", "Queens", address="1 Main St", notes="Apply by mail"))
        add_listing(s, L("a", "2", "Bronx"))
        add_listing(s, L("b", "1", "Brooklyn", status="waitlist", units=[]), age=timedelta(days=3))
        add_listing(s, L("b", "2", "Manhattan", status="closed"))
        add_listing(s, L("b", "3", "Queens"), gone=True)
        event(s, "a:1", "new")
        event(s, "a:2", "new")
        event(s, "b:1", "new", age=timedelta(days=3), detail={"baseline": True})
        event(s, "b:2", "closed", age=timedelta(minutes=20))
        event(s, "b:3", "gone", age=timedelta(minutes=10))
        s.add(EventRow(at=NOW - timedelta(minutes=5), source="b", key=None, type="source_failed", summary="b",
                       detail={"fails": 2, "error": "TimeoutError: boom"}))  # fmt: skip
        s.add(SourceStateRow(source="a", last_ok=NOW - timedelta(hours=1), last_attempt=NOW - timedelta(hours=1),
                             consecutive_failures=0, next_due=NOW + timedelta(hours=5)))  # fmt: skip
        s.add(SourceStateRow(source="b", last_ok=NOW - timedelta(days=1), last_attempt=NOW - timedelta(minutes=5),
                             failing_since=NOW - timedelta(hours=6), consecutive_failures=3))  # fmt: skip
        s.add(RunRow(source="a", job_id=1, started=NOW - timedelta(hours=1, seconds=12),
                     finished=NOW - timedelta(hours=1), ok=True, listings=2, workers=["oci"]))  # fmt: skip
        s.add(RunRow(source="b", job_id=2, started=NOW - timedelta(minutes=6), finished=NOW - timedelta(minutes=5),
                     ok=False, error="TimeoutError: boom", workers=["hetzner"]))  # fmt: skip


def seed_jobs(engine) -> None:
    """A worker and a few jobs in Procrastinate's tables (created by the migrations)."""
    with engine.begin() as c:
        c.execute(text("TRUNCATE procrastinate_jobs, procrastinate_workers RESTART IDENTITY CASCADE"))
        w = c.execute(text("INSERT INTO procrastinate_workers(last_heartbeat) VALUES (now()) RETURNING id")).scalar()
        jobs = [(1, "succeeded", '{"source": "a"}'), (2, "failed", '{"source": "b"}'), (3, "doing", '{"source": "a"}')]
        for jid, st, args in [*jobs, (4, "todo", "{}")]:
            c.execute(
                text(
                    "INSERT INTO procrastinate_jobs(id, queue_name, task_name, args, status, attempts, worker_id)"
                    " VALUES (:i, 'sources', 'source_run', CAST(:a AS jsonb),"
                    " CAST(:s AS procrastinate_job_status), 1, :w)"
                ),
                {"i": jid, "a": args, "s": st, "w": w if st != "todo" else None},
            )
        c.execute(text("INSERT INTO procrastinate_events(job_id, type, at) VALUES (2, 'failed', now())"))


def gql(client, query: str, headers: dict | None = None, **variables) -> dict:
    """POST a GraphQL document to the web app; the whole response ({"data", "errors"})."""
    return client.post("/graphql", json={"query": query, "variables": variables}, headers=headers).json()


def gql_error(res: dict) -> str | None:
    return res["errors"][0]["message"] if res.get("errors") else None
