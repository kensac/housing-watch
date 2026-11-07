"""The owner's application tracker: rows in `tracker_rows`, owned by the app, matched to listings at read time.

Each row is one building or opportunity being pursued, with its Status (an application stage) and Notes. The
app is the source of truth: rows are added and edited from the web UI or the MCP (`add_row`, `write`), and every
Status change is recorded in `tracker_events`, so a row's stage history, and how long it has been in its stage,
come from there. The rows began as an import of the owner's old Google Sheet, which nothing reads any more; its
columns are kept in `tracker_rows.cells`.

`match` pairs rows with listings deterministically: the row's Apply / Source link is the listing's URL, else a
normalized street address matches, else the building name does. A manual link (`tracker_links`) always wins,
including "not this row"; `add_row` makes one for the listing it was added from.
"""

from __future__ import annotations

import builtins
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .models import NotFound, Refused, values
from .orm import ListingRow, TrackerEventRow, TrackerLinkRow, TrackerRow
from .parse import addr_keys, name_key, units, url_key

# --- application stages -----------------------------------------------------------------------------------

type StageGroup = Literal["your move", "their move", "done"]  # whose move an application stage is
YOU, THEM, DONE = values(StageGroup)
# The application stages, in order, with whose move it is. A blank Status means not pursuing (yet).
STAGES = {
    "To Apply": YOU, "Ready to Send": YOU, "Action Needed": YOU,
    "Form Requested": THEM, "Applied": THEM, "Tour Scheduled": THEM, "In Processing": THEM,
    "Offer": DONE, "Closed": DONE,
}  # fmt: skip
# Statuses from before the dropdown had these stages (None: no stage)
LEGACY = {
    "interest form filled": "Applied", "contacted": "Form Requested", "under review": "In Processing",
    "appeal under review": "In Processing", "rejected": "Closed", "not interested": "Closed", "seen": None,
}  # fmt: skip
_BY_LOWER = {k.lower(): k for k in STAGES}
_RANK = {k: i for i, k in enumerate(STAGES)}


def stage(status: str | None) -> str | None:
    """The stage a Status means; one that is neither a stage nor a legacy status is kept as written."""
    s = " ".join((status or "").split())
    if not s:
        return None
    low = s.lower()
    return _BY_LOWER.get(low, LEGACY.get(low, s))


def group(st: str | None) -> StageGroup | None:
    return STAGES.get(st or "")


def rank(st: str | None) -> int:
    """Sort order: the stages in order, then unknown statuses, then no stage."""
    return _RANK.get(st, len(STAGES)) if st else len(STAGES) + 1


# --- rows ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """One column of a tracker row."""

    name: str  # the column header
    value: str
    link: str | None = None  # the cell's hyperlink, if it has one


def cells(d: dict | None) -> list[Cell]:
    """A stored row's cells (header -> text, `<header>__link` -> link) as Cells, in column order."""
    d = d or {}
    return [Cell(k, v or "", d.get(f"{k}__link")) for k, v in d.items() if not k.endswith("__link")]


def row_key(prop: str) -> str:
    return " ".join(prop.lower().split())


# --- stage history -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """A row entering a stage. `baseline`: it was already there when tracking began, so it entered earlier."""

    at: datetime
    stage_from: str | None
    stage_to: str | None
    baseline: bool = False  # already in this stage when tracking began

    @property
    def group(self) -> StageGroup | None:
        return group(self.stage_to)


def history(engine: Engine) -> dict[str, list[Step]]:
    """row key -> its stage changes, oldest first. A Status edit that keeps the stage (a legacy name replaced
    by its new one) is not a change."""
    out: dict[str, list[Step]] = {}
    with Session(engine) as s:
        for e in s.scalars(select(TrackerEventRow).order_by(TrackerEventRow.at, TrackerEventRow.id)):
            steps = out.setdefault(e.row_key, [])
            to = stage(e.status_to)
            if not steps:
                steps.append(Step(e.at, stage(e.status_from), to, e.baseline))
            elif to != steps[-1].stage_to:
                steps.append(Step(e.at, steps[-1].stage_to, to))
    return out


def since(steps: list[Step], status: str | None) -> Step | None:
    """The step that entered the row's current stage."""
    return steps[-1] if steps and steps[-1].stage_to == stage(status) else None


# --- matching ----------------------------------------------------------------------------------------------


type Match = Literal["url", "address", "name", "manual"]  # how a listing was matched to its tracker row
MATCHES = values(Match)


@dataclass(frozen=True)
class Ref:
    row_key: str
    property: str
    status: str | None  # as written
    notes: str | None
    how: Match
    stage: str | None = None
    since: Step | None = None

    @builtins.property  # the `property` field above shadows the builtin in this class body
    def group(self) -> StageGroup | None:
        return group(self.stage)


def _ref(r: TrackerRow, how: str, steps: dict[str, list[Step]]) -> Ref:
    return Ref(
        r.row_key, r.property, r.status, r.notes, how, stage(r.status), since(steps.get(r.row_key, []), r.status)
    )


def load(engine: Engine) -> tuple[list[TrackerRow], dict[str, str | None]]:
    with Session(engine) as s:
        rows = list(s.scalars(select(TrackerRow).order_by(TrackerRow.position)))
        links = {r.listing_key: r.row_key for r in s.scalars(select(TrackerLinkRow))}
    return rows, links


def _row_units(r: TrackerRow) -> set[str]:
    """The units a row names: in its Property, or on a Notes line that starts with "Unit" or "Units"."""
    lines = [line for line in (r.notes or "").splitlines() if re.match(r"\s*units?\b", line, re.I)]
    return units(r.property).union(*(units(line) for line in lines))


def match(
    listings: list, rows: list[TrackerRow], links: dict[str, str | None], steps: dict[str, list[Step]] | None = None
) -> dict[str, Ref]:
    """listing key -> its tracker row, for listings with (key, url, title, address) attributes."""
    steps = steps or {}
    by_key = {r.row_key: r for r in rows}
    # a link identifies a listing only when it is that one listing's own page: agent home pages and portals
    # (shared by many rows or many listings) would pin every listing that uses them to one row
    row_urls = Counter(k for r in rows if (k := url_key(r.source_url)))
    listing_urls = Counter(url_key(x.url) for x in listings)
    by_url = {k: r for r in rows if (k := url_key(r.source_url)) and row_urls[k] == 1 and listing_urls[k] == 1}
    by_addr: dict[str, list[TrackerRow]] = {}
    by_name: dict[str, list[TrackerRow]] = {}
    row_units = {r.row_key: _row_units(r) for r in rows}
    for r in rows:
        for a in addr_keys(r.property):
            by_addr.setdefault(a, []).append(r)
        if n := name_key(r.property):
            by_name.setdefault(n, []).append(r)

    def pick(cands: list[TrackerRow], unit: set[str]) -> TrackerRow | None:
        """For a listing that names its unit: a row naming that unit, else a building-wide row (a row for
        other units says nothing about this one: applying for #218 is not applying for #302). A listing
        that names no unit is the building (or an agent's single unit page): its building's first row."""
        if not unit:
            return cands[0] if cands else None
        exact = [r for r in cands if row_units[r.row_key] & unit]
        return exact[0] if exact else next((r for r in cands if not row_units[r.row_key]), None)

    out: dict[str, Ref] = {}
    for x in listings:
        if x.key in links:
            r = by_key.get(links[x.key] or "")
            if r:
                out[x.key] = _ref(r, "manual", steps)
            continue  # a manual "not this row" (or a row since renamed) beats every automatic match
        if (u := url_key(x.url)) and u in by_url:
            out[x.key] = _ref(by_url[u], "url", steps)
            continue
        unit = units(x.title)
        cands = [r for a in sorted(addr_keys(x.address) | addr_keys(x.title)) for r in by_addr.get(a, [])]
        if r := pick(cands, unit):
            out[x.key] = _ref(r, "address", steps)
        elif (n := name_key(x.title)) and (r := pick(by_name.get(n, []), unit)):
            out[x.key] = _ref(r, "name", steps)
    return out


def link(engine: Engine, listing_key: str, key: str | None) -> None:
    """Link a listing to a row by hand (key None: to no row), overriding automatic matching."""
    stmt = pg_insert(TrackerLinkRow).values(listing_key=listing_key, row_key=key)
    with Session(engine) as s, s.begin():
        if not s.scalar(select(ListingRow.key).where(ListingRow.key == listing_key)):
            raise NotFound("No such listing")
        if key is not None and not s.scalar(select(TrackerRow.row_key).where(TrackerRow.row_key == key)):
            raise NotFound("No such tracker row")
        s.execute(
            stmt.on_conflict_do_update(index_elements=["listing_key"], set_={"row_key": key, "created": func.now()})
        )


def unlink(engine: Engine, listing_key: str) -> None:
    """Drop a manual link: the listing goes back to automatic matching."""
    with Session(engine) as s, s.begin():
        if not s.execute(delete(TrackerLinkRow).where(TrackerLinkRow.listing_key == listing_key)).rowcount:
            raise NotFound("No manual link for that listing")


# --- writing -----------------------------------------------------------------------------------------------

_KEEP = object()  # write(): leave this field as it is


def _status(v: str | None) -> str | None:
    st = " ".join((v or "").split())
    if st and st not in STAGES:
        raise Refused(f"status: {st!r} is not one of {', '.join(STAGES)} (or empty to clear)")
    return st or None


def _event(s: Session, key: str, before: str | None, after: str | None, now: datetime, first: bool = False) -> None:
    if first or before != after:
        s.add(TrackerEventRow(row_key=key, status_from=None if first else before, status_to=after, at=now))


def write(engine: Engine, key: str, status=_KEEP, notes=_KEEP) -> None:
    """Set a row's Status (an application stage; empty clears it) and/or Notes. A Status change is an event."""
    want = {k: v for k, v in (("Status", status), ("Notes", notes)) if v is not _KEEP}
    if not want:
        raise Refused("nothing to write: give status and/or notes")
    now = datetime.now(UTC)
    with Session(engine) as s, s.begin():
        row = s.get(TrackerRow, key, with_for_update=True)
        if row is None:
            raise NotFound("No such tracker row")
        if "Status" in want:
            st = _status(want["Status"])
            _event(s, key, row.status, st, now)
            row.status = st
        if "Notes" in want:
            row.notes = (want["Notes"] or "").strip() or None
        row.cells = {**(row.cells or {}), "Status": row.status or "", "Notes": row.notes or ""}
        row.synced = now  # last changed: the read cache's key


def add_row(engine: Engine, listing_key: str, status: str | None = None, notes: str | None = None) -> str:
    """Add a row for a listing (Property: its title; Apply / Source: its link), linked to it; returns its key.
    Refused when a row of that name exists."""
    st, now = _status(status), datetime.now(UTC)
    with Session(engine) as s, s.begin():
        lst = s.get(ListingRow, listing_key)
        if lst is None:
            raise NotFound("No such listing")
        prop = (lst.title or lst.address or listing_key).strip()
        key = row_key(prop)
        if s.get(TrackerRow, key) is not None:
            raise Refused(f"the tracker already has a row {prop!r}; link the listing to it instead")
        note = (notes or "").strip() or None
        cells = {"Property": prop, "Status": st or "", "Apply / Source": "Listing", "Apply / Source__link": lst.url,
                 "Notes": note or ""}  # fmt: skip
        pos = (s.scalar(select(func.max(TrackerRow.position))) or 1) + 1
        s.add(TrackerRow(row_key=key, position=pos, property=prop, status=st, notes=note, source_url=lst.url,
                         cells=cells, synced=now))  # fmt: skip
        _event(s, key, None, st, now, first=True)
        link_row = pg_insert(TrackerLinkRow).values(listing_key=listing_key, row_key=key)
        s.execute(link_row.on_conflict_do_update(index_elements=["listing_key"], set_={"row_key": key}))
    return key
