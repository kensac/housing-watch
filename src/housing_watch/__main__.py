"""CLI.

python -m housing_watch run [--source NAME ...] [--force]   run due (or named) adapters once
python -m housing_watch digest [--dry-run]                  email un-notified events
python -m housing_watch loop                                run + digest forever (the container's command)
python -m housing_watch list                                adapters and their state
python -m housing_watch show NAME                           what one adapter returns, nothing stored
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict

from . import db, digest, runner
from .adapters import load_all
from .criteria import PROFILE, listing_verdict

TICK = int(os.environ.get("HW_TICK_SECONDS", "900"))  # how often the loop checks what is due
DIGEST_HOURS = [int(h) for h in os.environ.get("HW_DIGEST_HOURS", "8,13,19").split(",")]  # local time


def main(argv=None) -> int:
    logging.basicConfig(level=os.environ.get("HW_LOG", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="housing_watch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--source", nargs="*")
    r.add_argument("--force", action="store_true")
    d = sub.add_parser("digest")
    d.add_argument("--dry-run", action="store_true")
    sub.add_parser("loop")
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("name")
    a = ap.parse_args(argv)
    if PROFILE.income <= 0:
        ap.error("set HW_INCOME (annual household income); see criteria.Profile for the other HW_* vars")

    if a.cmd == "show":
        res = load_all()[a.name]().fetch()
        for lst in res.listings:
            v = listing_verdict(lst)
            print(json.dumps({"verdict": v.verdict, "reason": v.reason, **asdict(lst)}, default=str))
        for snap in res.snapshots:
            print(json.dumps({"snapshot": snap.url, "links": snap.links, "text": snap.text[:500]}))
        return 0
    c = db.connect()
    if a.cmd == "list":
        for name, cls in sorted(load_all().items()):
            st = c.execute("SELECT * FROM source_state WHERE source=?", (name,)).fetchone()
            print(f"{name:28} every {cls.every_hours:>4}h  {dict(st) if st else 'never run'}")
    elif a.cmd == "run":
        for k, v in runner.run(c, a.source, a.force or bool(a.source)).items():
            print(f"{k}: {v}")
    elif a.cmd == "digest":
        print(digest.send(c, a.dry_run))
    elif a.cmd == "loop":
        last_digest_hour = None
        while True:
            runner.run(c)
            hour = time.localtime().tm_hour
            slot = (time.strftime("%Y-%m-%d"), hour)
            urgent = digest.urgent(c)
            if urgent or (hour in DIGEST_HOURS and slot != last_digest_hour):
                try:
                    logging.info("digest: %s", digest.send(c))
                    last_digest_hour = slot
                except Exception as e:  # noqa: BLE001 - keep watching; retry next tick
                    logging.error("digest failed: %s", e)
            time.sleep(TICK)
    return 0


if __name__ == "__main__":
    sys.exit(main())
