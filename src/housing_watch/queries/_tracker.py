"""The Tracker board and a listing's tracker link."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.engine import Engine

from .. import tracker as T
from ..criteria import PROFILE, Profile
from ..models import Key
from ._judging import ListingSummary, judged, judged_listing


@dataclass
class TrackerRowView:
    """A tracker sheet row with its stage history and the listings matched to it."""

    row_key: Key
    position: int  # the sheet row number
    property: str
    status: str | None  # as written in the sheet
    stage: str | None  # the stage that status means (tracker.stage)
    group: T.StageGroup | None  # whose move that stage is
    stage_since: datetime | None  # when the row entered its stage
    stage_since_baseline: bool  # the stage predates tracking: it began before stage_since
    events: list[T.Step]  # stage changes, oldest first
    notes: str | None
    source_url: str | None
    page_url: str | None
    cells: list[T.Cell]  # every column of the row, as the sheet has them
    listings: list[ListingSummary]  # empty: unmatched, link one by hand


@dataclass
class TrackerBoard:
    configured: bool  # HW_TRACKER_SHEET is set
    synced: datetime | None  # when the sheet last changed (read every 5 minutes; an unchanged read keeps this)
    rows: list[TrackerRowView]  # by stage, then longest in it


def tracker_board(engine: Engine, p: Profile = PROFILE) -> TrackerBoard:
    rows, _ = T.load(engine)
    steps = T.history(engine)
    by_row: dict[str, list[ListingSummary]] = {}
    for x in judged(engine, p):
        if x.tracker_row:
            by_row.setdefault(T.row_key(x.tracker_row), []).append(x)
    synced = max((r.synced for r in rows), default=None)
    out = []
    for r in rows:
        st, hist = T.stage(r.status), steps.get(r.row_key, [])
        since = T.since(hist, r.status)
        out.append(TrackerRowView(
            r.row_key, r.position, r.property, r.status, st, T.group(st), since and since.at,
            bool(since and since.baseline), hist, r.notes, r.source_url, r.page_url, T.cells(r.cells),
            by_row.get(r.row_key, []),
        ))  # fmt: skip
    out.sort(key=lambda d: (T.rank(d.stage), d.stage_since is None, d.stage_since or synced, d.position))
    return TrackerBoard(bool(T.SHEET), synced, out)


@dataclass
class TrackerLink:
    """Where a listing stands in the tracker after a link change."""

    listing_key: Key
    row_key: Key | None  # the row it is matched to now; null: none
    listing: ListingSummary | None  # the listing as it reads after the change


def tracker_link(engine: Engine, key: str, p: Profile = PROFILE) -> TrackerLink:
    x = judged_listing(engine, key, p)
    return TrackerLink(key, T.row_key(x.tracker_row) if x and x.tracker_row else None, x)
