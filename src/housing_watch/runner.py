"""Run due adapters, record what they found, and keep going when one breaks."""

from __future__ import annotations

import logging
import traceback

from . import db
from .adapters import load_all

log = logging.getLogger("housing_watch")


def run(c, only: list[str] | None = None, force: bool = False) -> dict[str, str]:
    results: dict[str, str] = {}
    for name, cls in sorted(load_all().items()):
        if only and name not in only:
            continue
        if not force and not db.due(c, name, cls.every_hours):
            continue
        run_id = db.start_run(c, name)
        c.commit()
        try:
            adapter = cls(db.load_cache(c, name))
            res = adapter.fetch()
            if len(res.listings) + len(res.snapshots) < cls.min_listings:
                raise RuntimeError(
                    f"only {len(res.listings)} listings / {len(res.snapshots)} pages, expected >= {cls.min_listings}"
                )
            n = db.record_listings(c, name, res.listings)  # an empty list marks the old ones gone
            n += db.record_snapshots(c, name, res.snapshots)
            db.save_cache(c, name, adapter.cache)
            db.finish_run(c, run_id, name, True, len(res.listings), len(res.snapshots))
            results[name] = f"ok {len(res.listings)}L/{len(res.snapshots)}P, {n} events"
        except Exception as e:  # noqa: BLE001 - one broken source must not stop the others
            err = f"{type(e).__name__}: {e}"
            log.warning("%s failed: %s\n%s", name, err, traceback.format_exc(limit=3))
            db.finish_run(c, run_id, name, False, error=err)
            results[name] = f"FAIL {err[:200]}"
        c.commit()
        log.info("%s: %s", name, results[name])
    return results
