"""The owner's controls: run a source now, pause or resume it, and override what a listing reads as.

Source controls write source_state only; the tick does the rest.
`run_now` makes the source due, so the next tick (every minute) queues it on its learned queue, as any due
run; it also ends a refusal backoff (db.finish_run). A paused source is skipped by `db.claim_due` until resumed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import db, overrides
from .models import NotFound, Refused
from .orm import SourceStateRow


def _known(source: str) -> None:
    from .adapters import load_all

    if source not in load_all():
        raise NotFound(f"No such source: {source}")


def run_now(engine: Engine, source: str) -> None:
    """Queue a run at the next tick, ending any backoff. Refused while the source is paused."""
    _known(source)
    with Session(engine) as s, s.begin():
        st = db.source_state(s, source)
        if st.paused:
            raise Refused(f"{source} is paused; resume it first")
        st.next_due = db.utcnow()


def set_paused(engine: Engine, source: str, paused: bool) -> None:
    """Pause (no more runs) or resume (due at once) a source."""
    _known(source)
    with Session(engine) as s, s.begin():
        st = db.source_state(s, source)
        st.paused = paused
        if not paused:
            st.next_due = db.utcnow()


def state(engine: Engine, source: str) -> tuple[bool, datetime | None]:
    """(paused, next_due) of a source."""
    _known(source)
    with Session(engine) as s:
        st = s.get(SourceStateRow, source)
        return (bool(st and st.paused), st.next_due if st else None)


def set_override(engine: Engine, key: str, fields: dict | None, units: list | None, reason: str | None) -> None:
    """Replace a listing's override (see overrides.py); it is stored and judged at once, and kept on every run."""
    patch = overrides.validate(fields, units)
    with Session(engine) as s, s.begin():
        db.set_override(s, key, patch, reason)


def clear_override(engine: Engine, key: str) -> None:
    """Drop a listing's override: it reads as its source publishes it again."""
    with Session(engine) as s, s.begin():
        db.clear_override(s, key)
