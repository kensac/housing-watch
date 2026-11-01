"""The read model's value sets and small helpers, shared by every page."""

from __future__ import annotations

from dataclasses import asdict, field, fields, replace
from datetime import UTC, date, datetime
from typing import Literal

from ..criteria import ELIGIBLE, EXCLUDED, POSSIBLE, Verdict
from ..models import EventType, Listing, Size, values

type Status = Literal["open", "waitlist", "upcoming", "unknown", "closed", "gone"]  # PublishedStatus, or gone
type Health = Literal["ok", "failing", "never"]
type JobStatus = Literal["todo", "doing", "succeeded", "failed", "cancelled", "aborting", "aborted"]  # Procrastinate's
VERDICTS = values(Verdict)
STATUSES = values(Status)
HEALTH = values(Health)
EVENT_TYPES = values(EventType)
LISTING_EVENTS = EVENT_TYPES[:5]
_VRANK = {ELIGIBLE: 0, POSSIBLE: 1, EXCLUDED: 2}
_SIZE_ORDER = {z: i for i, z in enumerate(values(Size))}


def now() -> datetime:
    return datetime.now(UTC)


def internal(**kw):
    """A read-side field (search, sort) that the API and the MCP tools never show."""
    return field(repr=False, metadata={"internal": True}, **kw)


def public(x) -> dict:
    """A read-model dataclass as a dict, without its internal fields."""
    d = asdict(x)
    return {f.name: d[f.name] for f in fields(x) if not f.metadata.get("internal")}


def is_baseline(detail: dict | None) -> bool:
    """Events from a source's first run (db.py marks them) are the starting inventory, not news."""
    return bool(detail) and str(detail.get("baseline")).lower() == "true"


def day(s: str | None) -> date | None:
    """A stored ISO date (adapters write YYYY-MM-DD, some APIs a full timestamp) as a date; None if it is not one."""
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def known(v, alias):
    """A value recorded before the value sets were checked: kept when it is one of `alias`'s, else None."""
    return v if v in values(alias) else None


def as_open(lst: Listing) -> Listing:
    """The listing judged as if still open: 'was it eligible?' for closed/gone events."""
    return replace(lst, status="open")
