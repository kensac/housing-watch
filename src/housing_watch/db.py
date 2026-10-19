"""SQLite store and change detection. Pure data: no eligibility logic lives here.

Every listing a source publishes is stored, normalized into listings -> units -> bands, whatever it
would be judged. Change events (new, changed, closed, gone, reopened, page_changed, source_failed,
source_recovered) come only from the data. Filtering is business logic applied at read time
(criteria.py, used by digest.py), so changing the profile re-judges all history without re-scraping.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time

from .models import Band, Listing, Snapshot, Unit

DB_PATH = os.environ.get("HW_DB", "/data/housing-watch.db")
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, started REAL NOT NULL, finished REAL,
  ok INTEGER, listings INTEGER, snapshots INTEGER, error TEXT
);
CREATE INDEX IF NOT EXISTS runs_source ON runs(source, started);
CREATE TABLE IF NOT EXISTS listings (
  key TEXT PRIMARY KEY, source TEXT NOT NULL, ext_id TEXT NOT NULL, title TEXT, url TEXT,
  address TEXT, borough TEXT, status TEXT, kind TEXT, deadline TEXT, senior_only INTEGER NOT NULL DEFAULT 0,
  notes TEXT, fingerprint TEXT NOT NULL,
  first_seen REAL NOT NULL, last_seen REAL NOT NULL, last_changed REAL NOT NULL, gone_since REAL
);
CREATE INDEX IF NOT EXISTS listings_source ON listings(source);
CREATE TABLE IF NOT EXISTS units (
  id INTEGER PRIMARY KEY, listing_key TEXT NOT NULL REFERENCES listings(key) ON DELETE CASCADE,
  pos INTEGER NOT NULL, size TEXT NOT NULL, rent REAL, ami INTEGER, unit_no TEXT, set_aside TEXT, available INTEGER
);
CREATE INDEX IF NOT EXISTS units_listing ON units(listing_key);
CREATE TABLE IF NOT EXISTS bands (
  unit_id INTEGER NOT NULL REFERENCES units(id) ON DELETE CASCADE,
  household INTEGER NOT NULL, income_min INTEGER, income_max INTEGER
);
CREATE INDEX IF NOT EXISTS bands_unit ON bands(unit_id);
CREATE TABLE IF NOT EXISTS snapshots (
  key TEXT PRIMARY KEY, source TEXT NOT NULL, url TEXT, digest TEXT, text TEXT, links TEXT,
  first_seen REAL NOT NULL, last_seen REAL NOT NULL, changed REAL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, at REAL NOT NULL, source TEXT NOT NULL, key TEXT, type TEXT NOT NULL,
  summary TEXT, detail TEXT, notified REAL
);
CREATE INDEX IF NOT EXISTS events_pending ON events(notified);
CREATE TABLE IF NOT EXISTS adapter_cache (source TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL);
CREATE TABLE IF NOT EXISTS source_state (
  source TEXT PRIMARY KEY, last_ok REAL, last_attempt REAL, failing_since REAL, consecutive_failures INTEGER
);
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA)
    c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return c


def _fp(lst: Listing) -> str:
    """Hash of everything that, if it moves, is a change in the listing itself."""
    units = sorted(
        (
            (u.key(), u.rent, u.available, tuple((b.household, b.income_min, b.income_max) for b in u.bands))
            for u in lst.units
        ),
        key=repr,  # fields can be None, which tuples won't compare
    )
    return hashlib.sha256(
        json.dumps([lst.status, lst.deadline, lst.borough, lst.senior_only, lst.kind, units], default=str).encode()
    ).hexdigest()


def _event(c, source, key, typ, summary="", detail=None, now=None):
    c.execute(
        "INSERT INTO events(at, source, key, type, summary, detail) VALUES (?,?,?,?,?,?)",
        (now or time.time(), source, key, typ, summary, json.dumps(detail, default=str) if detail else None),
    )


def _write_units(c, lst: Listing) -> None:
    c.execute("DELETE FROM units WHERE listing_key=?", (lst.key(),))
    for pos, u in enumerate(lst.units):
        cur = c.execute(
            "INSERT INTO units(listing_key, pos, size, rent, ami, unit_no, set_aside, available)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                lst.key(),
                pos,
                u.size,
                u.rent,
                u.ami,
                u.unit_no,
                u.set_aside,
                None if u.available is None else int(u.available),
            ),
        )
        c.executemany(
            "INSERT INTO bands(unit_id, household, income_min, income_max) VALUES (?,?,?,?)",
            [(cur.lastrowid, b.household, b.income_min, b.income_max) for b in u.bands],
        )


def load_listing(c, key: str) -> Listing | None:
    r = c.execute("SELECT * FROM listings WHERE key=?", (key,)).fetchone()
    if r is None:
        return None
    units = []
    for u in c.execute("SELECT * FROM units WHERE listing_key=? ORDER BY pos", (key,)).fetchall():
        bands = [
            Band(b["household"], b["income_min"], b["income_max"])
            for b in c.execute("SELECT * FROM bands WHERE unit_id=? ORDER BY household", (u["id"],))
        ]
        units.append(
            Unit(
                size=u["size"],
                rent=u["rent"],
                ami=u["ami"],
                unit_no=u["unit_no"],
                bands=bands,
                set_aside=u["set_aside"],
                available=None if u["available"] is None else bool(u["available"]),
            )
        )
    return Listing(
        source=r["source"],
        ext_id=r["ext_id"],
        title=r["title"],
        url=r["url"],
        address=r["address"],
        borough=r["borough"],
        status=r["status"],
        kind=r["kind"],
        units=units,
        deadline=r["deadline"],
        senior_only=bool(r["senior_only"]),
        notes=r["notes"],
    )


def record_listings(c: sqlite3.Connection, source: str, listings: list[Listing], now: float | None = None) -> int:
    now = now or time.time()
    seen = set()
    n = 0
    for lst in listings:
        if lst.key() in seen:
            continue  # an adapter listing one thing twice must not flap
        seen.add(lst.key())
        fp = _fp(lst)
        row = c.execute("SELECT * FROM listings WHERE key=?", (lst.key(),)).fetchone()
        cols = (
            lst.title,
            lst.url,
            lst.address,
            lst.borough,
            lst.status,
            lst.kind,
            lst.deadline,
            int(lst.senior_only),
            lst.notes,
            fp,
        )
        if row is None:
            c.execute(
                "INSERT INTO listings(key, source, ext_id, title, url, address, borough, status, kind, deadline,"
                " senior_only, notes, fingerprint, first_seen, last_seen, last_changed)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (lst.key(), source, lst.ext_id, *cols, now, now, now),
            )
            _write_units(c, lst)
            _event(c, source, lst.key(), "new", lst.title, None, now)
            n += 1
            continue
        typ = None
        if row["gone_since"] is not None or (row["status"] == "closed" and lst.status != "closed"):
            typ = "reopened"
        elif row["fingerprint"] != fp:
            typ = "closed" if lst.status == "closed" and row["status"] != "closed" else "changed"
        before = load_listing(c, lst.key()) if typ == "changed" else None
        c.execute(
            "UPDATE listings SET title=?, url=?, address=?, borough=?, status=?, kind=?, deadline=?, senior_only=?,"
            " notes=?, fingerprint=?, last_seen=?, gone_since=NULL"
            + (", last_changed=?" if typ else "")
            + " WHERE key=?",
            (*cols, now, *([now] if typ else []), lst.key()),
        )
        if row["fingerprint"] != fp:
            _write_units(c, lst)
        if typ:
            detail = {"before": _brief(before)} if before else None
            _event(c, source, lst.key(), typ, lst.title, detail, now)
            n += 1
    for row in c.execute("SELECT key, title FROM listings WHERE source=? AND gone_since IS NULL", (source,)).fetchall():
        if row["key"] not in seen:
            c.execute("UPDATE listings SET gone_since=? WHERE key=?", (now, row["key"]))
            _event(c, source, row["key"], "gone", row["title"], None, now)
            n += 1
    return n


def _brief(lst: Listing) -> dict:
    return {"status": lst.status, "units": [[u.size, u.unit_no, u.rent, u.ami] for u in lst.units]}


def record_snapshots(c: sqlite3.Connection, source: str, snaps: list[Snapshot], now: float | None = None) -> int:
    now = now or time.time()
    n = 0
    for s in snaps:
        dg = hashlib.sha256((s.text + "\n" + "\n".join(sorted(s.links))).encode()).hexdigest()
        row = c.execute("SELECT * FROM snapshots WHERE key=?", (s.key(),)).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO snapshots(key, source, url, digest, text, links, first_seen, last_seen)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (s.key(), source, s.url, dg, s.text, json.dumps(sorted(s.links)), now, now),
            )
            _event(c, source, s.key(), "watching", s.url, None, now)
            n += 1
            continue
        if row["digest"] != dg:
            old_lines = set((row["text"] or "").splitlines())
            added = [x for x in s.text.splitlines() if x not in old_lines][:40]
            new_lines = set(s.text.splitlines())
            removed = [x for x in (row["text"] or "").splitlines() if x not in new_lines][:20]
            old_links = set(json.loads(row["links"] or "[]"))
            new_links = sorted(set(s.links) - old_links)
            c.execute(
                "UPDATE snapshots SET digest=?, text=?, links=?, last_seen=?, changed=? WHERE key=?",
                (dg, s.text, json.dumps(sorted(s.links)), now, now, s.key()),
            )
            _event(
                c,
                source,
                s.key(),
                "page_changed",
                s.url,
                {"added": added, "removed": removed, "new_links": new_links},
                now,
            )
            n += 1
        else:
            c.execute("UPDATE snapshots SET last_seen=? WHERE key=?", (now, s.key()))
    return n


def start_run(c, source) -> int:
    cur = c.execute("INSERT INTO runs(source, started) VALUES (?,?)", (source, time.time()))
    return cur.lastrowid


def finish_run(c, run_id, source, ok, listings=0, snapshots=0, error=None):
    now = time.time()
    c.execute(
        "UPDATE runs SET finished=?, ok=?, listings=?, snapshots=?, error=? WHERE id=?",
        (now, int(ok), listings, snapshots, error, run_id),
    )
    st = c.execute("SELECT * FROM source_state WHERE source=?", (source,)).fetchone()
    if ok:
        if st and st["failing_since"]:
            _event(c, source, None, "source_recovered", source, None, now)
        c.execute(
            "INSERT INTO source_state(source, last_ok, last_attempt, failing_since, consecutive_failures)"
            " VALUES (?,?,?,NULL,0) ON CONFLICT(source) DO UPDATE SET last_ok=excluded.last_ok,"
            " last_attempt=excluded.last_attempt, failing_since=NULL, consecutive_failures=0",
            (source, now, now),
        )
    else:
        fails = (st["consecutive_failures"] if st else 0) + 1
        since = st["failing_since"] if st and st["failing_since"] else now
        c.execute(
            "INSERT INTO source_state(source, last_ok, last_attempt, failing_since, consecutive_failures)"
            " VALUES (?,NULL,?,?,?) ON CONFLICT(source) DO UPDATE SET last_attempt=excluded.last_attempt,"
            " failing_since=excluded.failing_since, consecutive_failures=excluded.consecutive_failures",
            (source, now, since, fails),
        )
        if fails in (2, 10):  # one transient failure is noise; report on the 2nd and again on the 10th
            _event(c, source, None, "source_failed", source, {"error": (error or "")[:500], "fails": fails}, now)


def due(c, source: str, every_hours: float, now: float | None = None) -> bool:
    now = now or time.time()
    st = c.execute("SELECT last_attempt FROM source_state WHERE source=?", (source,)).fetchone()
    return st is None or st["last_attempt"] is None or now - st["last_attempt"] >= every_hours * 3600 - 60


def load_cache(c, source: str) -> dict:
    row = c.execute("SELECT value FROM adapter_cache WHERE source=?", (source,)).fetchone()
    return json.loads(row["value"]) if row else {}


def save_cache(c, source: str, value: dict) -> None:
    c.execute(
        "INSERT INTO adapter_cache(source, value, updated) VALUES (?,?,?)"
        " ON CONFLICT(source) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        (source, json.dumps(value, default=str), time.time()),
    )
