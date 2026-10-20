"""The owner's application tracker (a Google Sheet): synced read-only, matched to listings at read time.

The sheet is the source of truth for application status and notes. `sync` copies its rows into
`tracker_rows` (replacing them) through a service account that can only view the sheet; `match` pairs
rows with listings deterministically: the row's Apply / Source link is the listing's URL, else a
normalized street address matches, else the building name does. A manual link set in the UI
(`tracker_links`) always wins, including "not this row".
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import fetch
from .orm import TrackerLinkRow, TrackerRow

SHEET = os.environ.get("HW_TRACKER_SHEET", "")
TAB = os.environ.get("HW_TRACKER_TAB", "Properties")
KEY_FILE = os.environ.get("HW_SHEETS_KEY_FILE", "")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
API = "https://sheets.googleapis.com/v4/spreadsheets/{id}"
FIELDS = "sheets(data(rowData(values(formattedValue,hyperlink,textFormatRuns(format(link(uri)))))))"


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
        old = sorted(facts(r) for r in s.scalars(select(TrackerRow)))
        if old != sorted(facts(r) for r in seen.values()):  # unchanged: keep the rows (and the read cache)
            s.execute(delete(TrackerRow))
            s.add_all(seen.values())
    return len(seen)


# --- matching ----------------------------------------------------------------------------------------------

_STREET = {
    "street": "st", "avenue": "ave", "av": "ave", "boulevard": "blvd", "road": "rd", "place": "pl",
    "drive": "dr", "parkway": "pkwy", "lane": "ln", "court": "ct", "terrace": "ter", "plaza": "plz",
    "west": "w", "east": "e", "north": "n", "south": "s", "of": "", "the": "",
}  # fmt: skip
_SUFFIX = {"st", "ave", "blvd", "rd", "pl", "dr", "pkwy", "ln", "ct", "ter", "plz", "way", "sq", "cir", "hwy"}
_UNIT = re.compile(r"(?:\bunits?\s+|#|\bapt\.?\s+)((?:[a-z]*\d[\w-]*)(?:\s*(?:,|/|&|and)\s*#?[a-z]*\d[\w-]*)*)", re.I)
_ADDR = re.compile(
    r"\b(\d+(?:-\d+)?)\s+((?:[a-z0-9.']+\s?){1,5}?)(?=$|,|\(|\)|\s(?:apt|unit|#|aka|apartments?)\b|\s#)", re.I
)


def url_key(u: str | None) -> str | None:
    if not u:
        return None
    p = urlsplit(u.strip().lower())
    host = p.netloc.removeprefix("www.")
    return (host + p.path.rstrip("/")) if host else None


def addr_keys(text: str | None) -> set[str]:
    """'42-10 27th St' / '241 West 28th Street' -> {'42-10 27 st'} / {'241 w 28 st'}: house number + street."""
    out = set()
    for num, street in _ADDR.findall((text or "").lower()):
        words = [_STREET.get(w, w) for w in re.sub(r"[.']", "", street).split()]
        words = [re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", w) for w in words if w]
        cut = next((i for i, w in enumerate(words) if w in _SUFFIX and i > 0), None)
        words = words[: cut + 1] if cut is not None else words  # "69 amboy st 4b" -> "69 amboy st"
        if words and not all(w.isdigit() for w in words):
            out.add(f"{num} {' '.join(words)}")
    return out


def units(text: str | None) -> set[str]:
    """Unit numbers a row or listing names: 'Unit 218', 'Units 12N / 14N', '#13D', or a trailing '4B' / 'PH2'
    after the street ('69 Amboy St 4B')."""
    t = text or ""
    out = {u.upper().lstrip("#") for m in _UNIT.findall(t) for u in re.split(r"\s*(?:,|/|&|and)\s*", m) if u}
    tail = re.search(
        r"\b(?:" + "|".join(_SUFFIX) + r"|street|avenue)\.?\s+((?:ph)?\d+[a-z]{1,2}|ph\d*|[a-z]\d+)\s*$", t, re.I
    )
    if tail:
        out.add(tail.group(1).upper())
    return {u for u in out if re.search(r"\d", u) or u.startswith("PH")}


_GENERIC = {"apartments", "apartment", "apts", "the", "residences", "multiple", "units", "unit", "available"}


def name_key(text: str | None) -> str | None:
    """The building name before any '(address)', '#unit' or '|': 'The Cove (43-12 ...)' -> 'cove'."""
    base = re.split(r"[(|#–—]", (text or "").lower())[0]
    words = [w for w in re.findall(r"[a-z0-9]+", base) if w not in _GENERIC]
    key = " ".join(words)
    return key if len(key) >= 4 and not key.isdigit() else None


@dataclass(frozen=True)
class Ref:
    row_key: str
    property: str
    status: str | None
    notes: str | None
    how: str  # url | address | name | manual


def _ref(r: TrackerRow, how: str) -> Ref:
    return Ref(r.row_key, r.property, r.status, r.notes, how)


def load(engine: Engine) -> tuple[list[TrackerRow], dict[str, str | None]]:
    with Session(engine) as s:
        rows = list(s.scalars(select(TrackerRow).order_by(TrackerRow.position)))
        links = {r.listing_key: r.row_key for r in s.scalars(select(TrackerLinkRow))}
    return rows, links


def match(listings: list, rows: list[TrackerRow], links: dict[str, str | None]) -> dict[str, Ref]:
    """listing key -> its tracker row, for listings with (key, url, title, address) attributes."""
    by_key = {r.row_key: r for r in rows}
    # a link identifies a listing only when it is that one listing's own page: agent home pages and portals
    # (shared by many rows or many listings) would pin every listing that uses them to one row
    row_urls = Counter(k for r in rows if (k := url_key(r.source_url)))
    listing_urls = Counter(url_key(x.url) for x in listings)
    by_url = {k: r for r in rows if (k := url_key(r.source_url)) and row_urls[k] == 1 and listing_urls[k] == 1}
    by_addr: dict[str, list[TrackerRow]] = {}
    by_name: dict[str, list[TrackerRow]] = {}
    row_units = {r.row_key: units(r.property) | {u for line in (r.notes or "").splitlines()
                                                 if re.match(r"\s*units?\b", line, re.I) for u in units(line)}
                 for r in rows}  # fmt: skip
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
                out[x.key] = _ref(r, "manual")
            continue  # a manual "not this row" (or a row since renamed) beats every automatic match
        if (u := url_key(x.url)) and u in by_url:
            out[x.key] = _ref(by_url[u], "url")
            continue
        unit = units(x.title)
        cands = [r for a in sorted(addr_keys(x.address) | addr_keys(x.title)) for r in by_addr.get(a, [])]
        if r := pick(cands, unit):
            out[x.key] = _ref(r, "address")
        elif (n := name_key(x.title)) and (r := pick(by_name.get(n, []), unit)):
            out[x.key] = _ref(r, "name")
    return out


def set_link(engine: Engine, listing_key: str, key: str | None) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(TrackerLinkRow).values(listing_key=listing_key, row_key=key)
    with engine.begin() as c:
        c.execute(
            stmt.on_conflict_do_update(index_elements=["listing_key"], set_={"row_key": key, "created": func.now()})
        )


def clear_link(engine: Engine, listing_key: str) -> bool:
    with Session(engine) as s, s.begin():
        return s.execute(delete(TrackerLinkRow).where(TrackerLinkRow.listing_key == listing_key)).rowcount > 0
