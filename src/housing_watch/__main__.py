"""CLI.

python -m housing_watch serve                  migrate + worker (+ web on HW_WEB_PORT, MCP on HW_MCP_PORT); hetzner's
python -m housing_watch worker                 migrate + Procrastinate worker; every other host's command
python -m housing_watch migrate                alembic upgrade head (advisory-locked)
python -m housing_watch run --source NAME ...  run sources now, inline, without the queue
python -m housing_watch digest [--dry-run]     email un-notified events (--dry-run prints, marks nothing)
python -m housing_watch list                   adapters and their state
python -m housing_watch show NAME              what one adapter returns, nothing stored
python -m housing_watch schema [--check] PATH   write the GraphQL SDL to PATH (--check: fail if PATH is stale)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from dataclasses import asdict

from . import db, runner
from .adapters import load_all
from .criteria import PROFILE, listing_verdict

log = logging.getLogger("housing_watch")


def _serve_in_thread(name: str, asgi_app, port: int) -> None:
    """An ASGI app on HW_WEB_HOST:port in a daemon thread of this process."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(asgi_app, host=os.environ.get("HW_WEB_HOST", "0.0.0.0"), port=port))
    threading.Thread(target=server.run, name=name, daemon=True).start()
    log.info("%s on :%d", name, port)


def _worker(eng) -> None:
    from . import metrics, tasks

    tasks.set_engine(eng)
    metrics.serve(tasks.engine)
    name = runner.WORKER
    concurrency = int(os.environ.get("HW_WORKER_CONCURRENCY", "4"))
    queues = tasks.queues()
    log.info("worker %s: queues %s, concurrency %d", name, queues, concurrency)
    tasks.run_worker(name, concurrency, queues)


def _list(eng) -> None:
    """One line per adapter: cadence, last success, failure streak, next due."""
    from sqlalchemy.orm import Session

    with Session(eng) as s:
        states = db.source_states(s)
    for name, cls in sorted(load_all().items()):
        st = states.get(name)
        line = f"{name:28} every {cls.every_hours:>4}h  "
        line += f"last ok {st.last_ok:%Y-%m-%d %H:%M}" if st and st.last_ok else "never ok"
        if st and st.consecutive_failures:
            line += f", {st.consecutive_failures} failing"
        if st and st.next_due:
            line += f", due {st.next_due:%Y-%m-%d %H:%M}"
        print(line)


def _schema(path: str, check: bool) -> int:
    """The web API's SDL, as frontend/codegen.ts reads it."""
    from pathlib import Path

    from .web.schema import schema

    sdl, f = schema.as_str() + "\n", Path(path)
    if not check:
        f.write_text(sdl)
        return 0
    if f.is_file() and f.read_text() == sdl:
        return 0
    print(f"{path} is stale: run `python -m housing_watch schema {path}` and commit it", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    logging.basicConfig(level=os.environ.get("HW_LOG", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="housing_watch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    sub.add_parser("worker")
    sub.add_parser("migrate")
    r = sub.add_parser("run")
    r.add_argument("--source", nargs="+", required=True)
    d = sub.add_parser("digest")
    d.add_argument("--dry-run", action="store_true")
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("name")
    g = sub.add_parser("schema")
    g.add_argument("path", nargs="?", default="schema.graphql")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd in ("show", "digest", "serve") and PROFILE.income <= 0:
        ap.error("set HW_INCOME (annual household income); see criteria.Profile for the other HW_* vars")

    if a.cmd == "show":
        res, _ = runner.fetch_all(load_all()[a.name]())
        for lst in res.listings:
            v = listing_verdict(lst)
            print(json.dumps({"verdict": v.verdict, "reason": v.reason, **asdict(lst)}, default=str))
        for snap in res.snapshots:
            print(json.dumps({"snapshot": snap.url, "links": snap.links, "text": snap.text[:500]}))
        return 0
    if a.cmd == "schema":
        return _schema(a.path, a.check)
    eng = db.engine()
    if a.cmd == "migrate":
        db.migrate(eng)
    elif a.cmd == "worker":
        db.migrate(eng)
        _worker(eng)
    elif a.cmd == "serve":
        db.migrate(eng)  # before the web starts reading
        if web_port := int(os.environ.get("HW_WEB_PORT") or 0):
            from .web.app import create_app

            _serve_in_thread("web", create_app(eng), web_port)
        if mcp_port := int(os.environ.get("HW_MCP_PORT") or 0):
            from .mcp_server import app as mcp_app

            _serve_in_thread("mcp", mcp_app(eng), mcp_port)
        _worker(eng)
    elif a.cmd == "list":
        _list(eng)
    elif a.cmd == "run":
        unknown = set(a.source) - set(load_all())
        if unknown:
            ap.error(f"unknown source(s): {', '.join(sorted(unknown))}")
        for name in a.source:
            print(f"{name}: {runner.run_inline(eng, name)}")
    elif a.cmd == "digest":
        from . import digest

        print(digest.send(eng, dry_run=a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
