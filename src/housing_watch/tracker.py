"""The owner's application tracker (a Google Sheet): synced read-only, matched to listings at read time.

The sheet is the source of truth for application status and notes. `sync` copies its rows into
`tracker_rows` (replacing them) through a service account; `write` and `add_row` change the sheet itself
(Status, Notes, a new row) and sync at once, so they need that account to be an editor of it. `match` pairs
rows with listings deterministically: the row's Apply / Source link is the listing's URL, else a
normalized street address matches, else the building name does. A manual link set in the UI
(`tracker_links`) always wins, including "not this row".

The sheet holds only each row's current Status, so `sync` records every change it sees in
`tracker_events`; a row's stage history, and how long it has been in its stage, come from there.
"""

from __future__ import annotations

import builtins
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import fetch
from .models import NotFound, Refused, values
from .orm import ListingRow, TrackerEventRow, TrackerLinkRow, TrackerRow
from .parse import addr_keys, name_key, units, url_key

SHEET = os.environ.get("HW_TRACKER_SHEET", "")
TAB = os.environ.get("HW_TRACKER_TAB", "Properties")
KEY_FILE = os.environ.get("HW_SHEETS_KEY_FILE", "")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]  # read and write: see write()
API = "https://sheets.googleapis.com/v4/spreadsheets/{id}"
FIELDS = "sheets(data(rowData(values(formattedValue,hyperlink,textFormatRuns(format(link(uri)))))))"


# --- application stages -----------------------------------------------------------------------------------

type StageGroup = Literal["your move", "their move", "done"]  # whose move an application stage is
YOU, THEM, DONE = values(StageGroup)
# The sheet's Status dropdown, in order, with whose move it is. A blank Status means not pursuing (yet).
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
    """Sort order: the stages in sheet order, then unknown statuses, then no stage."""
    return _RANK.get(st, len(STAGES)) if st else len(STAGES) + 1


# --- reading the sheet -----------------------------------------------------------------------------------


def _token() -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
    creds.refresh(Request())
    return creds.token


def _cell(v: dict) -> tuple[str, str | None]:
    """(text, link) of one cell; the link from HYPERLINK() or from rich-text runs."""
    link = v.get("hyperlink") or next(
        (r["format"]["link"]["uri"] for r in v.get("textFormatRuns") or [] if r.get("format", {}).get("link")), None
    )
    return (v.get("formattedValue") or "").strip(), link


def parse(data: dict) -> list[dict]:
    """The sheet's rows as dicts: header -> text, plus `<header>__link` for linked cells and `_row`."""
    rows = [r.get("values") or [] for s in data.get("sheets") or [] for d in s.get("data") or []
            for r in d.get("rowData") or []]  # fmt: skip
    if not rows:
        return []
    head = [_cell(v)[0] for v in rows[0]]
    if "Property" not in head:
        raise ValueError(f"tracker sheet header has no Property column: {head}")
    out = []
    for n, r in enumerate(rows[1:], start=2):
        d: dict = {"_row": n}
        for h, v in zip(head, r, strict=False):
            if not h:
                continue
            text, link = _cell(v)
            d[h] = text
            if link:
                d[f"{h}__link"] = link
        if d.get("Property"):
            out.append(d)
    return out


@dataclass(frozen=True)
class Cell:
    """One column of a sheet row."""

    name: str  # the column header
    value: str
    link: str | None = None  # the cell's hyperlink, if it has one


def cells(d: dict | None) -> list[Cell]:
    """A stored row's cells (header -> text, `<header>__link` -> link) as Cells, in sheet order."""
    d = d or {}
    return [Cell(k, v or "", d.get(f"{k}__link")) for k, v in d.items() if not k.endswith("__link")]


def row_key(prop: str) -> str:
    return " ".join(prop.lower().split())


def sync(engine: Engine) -> int:
    """Replace tracker_rows with the sheet's current rows; returns how many. Off unless configured."""
    if not (SHEET and KEY_FILE and os.path.isfile(KEY_FILE)):
        return 0
    url = API.format(id=SHEET) + f"?ranges={quote(TAB)}&fields={quote(FIELDS)}"
    rows = parse(fetch.get(url, headers={"Authorization": f"Bearer {_token()}"}).json())
    now = datetime.now(UTC)
    seen: dict[str, TrackerRow] = {}
    for d in rows:
        k = row_key(d["Property"])
        if k in seen:  # the same building twice: keep the first row, as the sheet reads top-down
            continue
        seen[k] = TrackerRow(
            row_key=k, position=d["_row"], property=d["Property"], status=d.get("Status") or None,
            notes=d.get("Notes") or None, source_url=d.get("Apply / Source__link"),
            page_url=d.get("Property Page__link"), cells={h: v for h, v in d.items() if not h.startswith("_")},
            synced=now,
        )  # fmt: skip

    def facts(r: TrackerRow) -> tuple:
        return (r.row_key, r.position, r.property, r.status, r.notes, r.source_url, r.page_url, r.cells)

    with Session(engine) as s, s.begin():
        stored = list(s.scalars(select(TrackerRow)))
        if sorted(facts(r) for r in stored) != sorted(facts(r) for r in seen.values()):  # else keep the read cache
            s.execute(delete(TrackerRow))
            s.add_all(seen.values())
        s.add_all(_events(s, seen, {r.row_key for r in stored}, now))
    return len(seen)


def _events(s: Session, rows: dict[str, TrackerRow], stored: set[str], now: datetime) -> list[TrackerEventRow]:
    """An event for each row whose Status differs from its last event, or that has none yet. A row stored
    before it had any event predates tracking (the first sync after deploy), so its event is a baseline.
    A removed row records nothing and keeps its events, in case it comes back."""
    q = select(TrackerEventRow.row_key, TrackerEventRow.status_to).order_by(TrackerEventRow.id)
    last = dict(s.execute(q).all())  # each row's newest event wins
    return [
        TrackerEventRow(row_key=k, status_from=last.get(k), status_to=r.status, baseline=k not in last and k in stored,
                        at=now)
        for k, r in rows.items()
        if k not in last or last[k] != r.status
    ]  # fmt: skip


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
    status: str | None  # as written in the sheet
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


# --- writing the sheet -----------------------------------------------------------------------------------

_KEEP = object()  # write(): leave this cell as it is


def _configured() -> None:
    if not (SHEET and KEY_FILE and os.path.isfile(KEY_FILE)):
        raise Refused("the tracker sheet is not configured (HW_TRACKER_SHEET, HW_SHEETS_KEY_FILE)")


def _range(a1: str) -> str:
    return quote("'" + TAB.replace("'", "''") + "'!" + a1, safe="")


def _col(n: int) -> str:
    """A1 column letters of the 0-based column n."""
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def _values(a1: str, auth: dict) -> list[list[str]]:
    return fetch.get(API.format(id=SHEET) + f"/values/{_range(a1)}", headers=auth).json().get("values") or []


def _header(auth: dict) -> list[str]:
    head = [h.strip() for h in (_values("1:1", auth) or [[]])[0]]
    if "Property" not in head:
        raise Refused(f"the tracker sheet's header has no Property column: {head}")
    return head


def _status(v: str | None) -> str:
    st = " ".join((v or "").split())
    if st and st not in STAGES:
        raise Refused(f"status: {st!r} is not one of {', '.join(STAGES)} (or empty to clear)")
    return st


def write(engine: Engine, key: str, status=_KEEP, notes=_KEEP) -> int:
    """Set a row's Status and/or Notes in the sheet, then sync; returns the rows synced. Refused when the row has
    moved in the sheet since the last sync (sync, and try again)."""
    _configured()
    with Session(engine) as s:
        row = s.get(TrackerRow, key)
        if row is None:
            raise NotFound("No such tracker row")
        position, prop = row.position, row.property
    want = {}
    if status is not _KEEP:
        want["Status"] = _status(status)
    if notes is not _KEEP:
        want["Notes"] = (notes or "").strip()
    if not want:
        raise Refused("nothing to write: give status and/or notes")
    auth = {"Authorization": f"Bearer {_token()}"}
    head = _header(auth)
    if missing := [h for h in want if h not in head]:
        raise Refused(f"the tracker sheet has no {', '.join(missing)} column")
    pc = _col(head.index("Property"))
    live = _values(f"{pc}{position}", auth)
    if row_key(live[0][0] if live and live[0] else "") != key:
        sync(engine)
        raise Refused(f"{prop!r} is no longer on sheet row {position}; synced, try again")
    data = [{"range": f"'{TAB}'!{_col(head.index(h))}{position}", "values": [[v]]} for h, v in want.items()]
    fetch.post_json(API.format(id=SHEET) + "/values:batchUpdate",
                    {"valueInputOption": "USER_ENTERED", "data": data}, headers=auth)  # fmt: skip
    return sync(engine)


def _link_formula(url: str, label: str) -> str:
    q = lambda x: x.replace('"', '""')  # noqa: E731
    return f'=HYPERLINK("{q(url)}","{q(label)}")'


def add_row(engine: Engine, listing_key: str, status: str | None = None, notes: str | None = None) -> str:
    """Append a sheet row for a listing (Property = its title, Apply / Source = its link), sync, and link the
    listing to it; returns the row's key. Refused when a row of that name exists."""
    _configured()
    with Session(engine) as s:
        lst = s.get(ListingRow, listing_key)
        if lst is None:
            raise NotFound("No such listing")
        title, url = (lst.title or lst.address or listing_key).strip(), lst.url
    key = row_key(title)
    with Session(engine) as s:
        if s.get(TrackerRow, key) is not None:
            raise Refused(f"the sheet already has a row {title!r}; link the listing to it instead")
    st = _status(status)
    auth = {"Authorization": f"Bearer {_token()}"}
    head = _header(auth)
    cells = {"Property": title, "Status": st, "Notes": (notes or "").strip()}
    if url and "Apply / Source" in head:
        cells["Apply / Source"] = _link_formula(url, "Listing")
    values_row = [cells.get(h, "") for h in head]
    fetch.post_json(API.format(id=SHEET) + f"/values/{_range('A1')}:append"
                    "?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
                    {"values": [values_row]}, headers=auth)  # fmt: skip
    sync(engine)
    link(engine, listing_key, key)
    return key
