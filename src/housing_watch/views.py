"""Saved views: a named filter + quick search + sort + columns for one UI page.

Stored as State.to_json() (the filter as its JSON tree, the sort as field/direction pairs) and re-validated
against today's registries on every load: a view that names a dropped field, sort or column still opens, minus
that part, with a warning. Nothing is seeded; views exist only once the user saves one. This is the only table
the web UI writes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import filters as F
from . import queries
from .criteria import PROFILE, Profile
from .models import Serial
from .orm import SavedViewRow

type Page = Literal["listings", "units", "events"]  # a list page of the app
FIELDS = {"listings": F.LISTING_FIELDS, "units": F.LISTING_FIELDS, "events": F.EVENT_FIELDS}
SORTS = {"listings": queries.LISTING_SORTS, "units": queries.UNIT_SORTS, "events": {}}
COLUMNS = {
    "listings": {"verdict": "Verdict", "sizes": "Sizes", "rent": "Rent", "ami": "AMI", "status": "Status",
                 "deadline": "Deadline", "borough": "Borough", "commute": "Commute", "application": "Application",
                 "kind": "Kind", "source": "Source",
                 "first_seen": "First seen", "changed": "Changed"},
    "units": {"verdict": "Verdict", "size": "Size", "unit_no": "Unit", "rent": "Rent", "ami": "AMI",
              "income": "Your income band", "commute": "Commute", "application": "Application", "status": "Status",
              "borough": "Borough",
              "deadline": "Deadline",
              "source": "Source", "changed": "Changed"},
    "events": {"verdict": "Verdict", "source": "Source", "mailed": "Mailed"},
}  # fmt: skip
DEFAULT_COLUMNS = {
    "listings": ("verdict", "sizes", "rent", "ami", "commute", "application", "status", "changed"),
    "units": ("verdict", "size", "rent", "ami", "income", "commute", "application", "status", "borough"),
    "events": ("verdict", "source", "mailed"),
}
DEFAULT_SORT: dict[str, tuple[queries.Sort, ...]] = {"listings": queries.DEFAULT_SORT, "units": queries.DEFAULT_SORT,
                                                     "events": ()}  # fmt: skip
VERSION = 2  # of State.to_json(); alembic 0010 moved version 1 (DSL strings) to it


class ViewError(ValueError):
    pass


@dataclass
class State:
    """What a list page shows: the part of its URL a saved view captures."""

    page: Page
    filters: list[F.Cond] = field(default_factory=list)
    q: str = ""
    sort: tuple[queries.Sort, ...] = ()  # empty: the page's default
    columns: tuple[str, ...] = ()  # empty: the page's default

    def __post_init__(self):
        if bad := [s.field for s in self.sort if s.field not in SORTS[self.page]]:
            raise ViewError(f"{self.page} can't be sorted by {', '.join(bad)}")
        if bad := [c for c in self.columns if c not in COLUMNS[self.page]]:
            raise ViewError(f"{self.page} has no column {', '.join(bad)}")
        self.sort = tuple(self.sort) or DEFAULT_SORT[self.page]
        self.columns = tuple(dict.fromkeys(self.columns)) or DEFAULT_COLUMNS[self.page]

    @property
    def filter(self) -> dict:
        """The filters as their JSON tree."""
        return F.to_tree(self.filters)

    def to_json(self) -> dict:
        return {"version": VERSION, "filter": self.filter, "q": self.q,
                "sort": [{"field": s.field, "direction": s.direction} for s in self.sort],
                "columns": list(self.columns)}  # fmt: skip


def state_from_json(page: Page, q: dict) -> tuple[State, list[str]]:
    conds, warnings = F.from_saved(q.get("filter"), FIELDS[page])
    sort = [queries.Sort(s.get("field", ""), s.get("direction", "asc")) for s in q.get("sort") or []]
    if dropped := [s.field for s in sort if s.field not in SORTS[page] or s.direction not in ("asc", "desc")]:
        warnings.append(f"Sort by {', '.join(dropped)} no longer exists and was dropped.")
    cols = tuple(q.get("columns") or ())
    if gone := [c for c in cols if c not in COLUMNS[page]]:
        warnings.append(f"Column(s) {', '.join(gone)} no longer exist and were dropped.")
    sort = [s for s in sort if s.field not in dropped]
    return State(page, conds, q.get("q") or "", tuple(sort), tuple(c for c in cols if c not in gone)), warnings


@dataclass
class View:
    """A saved view: a named filter, search, sort and columns."""

    id: Serial
    page: Page
    name: str
    position: int
    is_default: bool
    state: State
    warnings: list[str]  # what of the saved state no longer applies, and was dropped
    updated: datetime | None = None
    count: int | None = None  # what it matches now


def _view(r: SavedViewRow) -> View:
    st, warnings = state_from_json(r.page, r.query or {})
    return View(r.id, r.page, r.name, r.position, r.is_default, st, warnings, r.updated)


def list_views(engine: Engine, page: str | None = None) -> list[View]:
    with Session(engine) as s:
        q = select(SavedViewRow).order_by(SavedViewRow.page, SavedViewRow.position, SavedViewRow.id)
        return [_view(r) for r in s.scalars(q.where(SavedViewRow.page == page) if page else q)]


def get(engine: Engine, view_id: int) -> View | None:
    with Session(engine) as s:
        r = s.get(SavedViewRow, view_id)
        return _view(r) if r else None


def default(engine: Engine, page: str) -> View | None:
    with Session(engine) as s:
        r = s.scalar(select(SavedViewRow).where(SavedViewRow.page == page, SavedViewRow.is_default))
        return _view(r) if r else None


def _name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name or len(name) > 80:
        raise ViewError("A view needs a name of 1 to 80 characters.")
    return name


def _set_default(s: Session, page: str, view_id: int, on: bool) -> None:
    if on:  # one default per page (a partial unique index enforces it too)
        s.execute(update(SavedViewRow).where(SavedViewRow.page == page).values(is_default=False))
        s.flush()
    s.execute(update(SavedViewRow).where(SavedViewRow.id == view_id).values(is_default=on))


def create(engine: Engine, state: State, name: str, is_default: bool = False) -> View:
    with Session(engine) as s, s.begin():
        pos = s.scalar(
            select(func.coalesce(func.max(SavedViewRow.position), -1) + 1).where(SavedViewRow.page == state.page)
        )
        r = SavedViewRow(page=state.page, name=_name(name), query=state.to_json(), position=pos, is_default=False)
        s.add(r)
        s.flush()
        if is_default:
            _set_default(s, state.page, r.id, True)
        view_id = r.id
    return get(engine, view_id)


def change(engine: Engine, view_id: int, *, name: str | None = None, state: State | None = None,
           is_default: bool | None = None, move: int = 0) -> View | None:  # fmt: skip
    with Session(engine) as s, s.begin():
        r = s.get(SavedViewRow, view_id)
        if r is None:
            return None
        vals: dict = {"updated": func.now()}
        if name is not None:
            vals["name"] = _name(name)
        if state is not None:
            if state.page != r.page:
                raise ViewError("A view belongs to one page.")
            vals["query"] = state.to_json()
        s.execute(update(SavedViewRow).where(SavedViewRow.id == view_id).values(**vals))
        if is_default is not None:
            _set_default(s, r.page, view_id, is_default)
        if move:
            ids = list(s.scalars(select(SavedViewRow.id).where(SavedViewRow.page == r.page)
                                 .order_by(SavedViewRow.position, SavedViewRow.id)))  # fmt: skip
            i = ids.index(view_id)
            j = max(0, min(len(ids) - 1, i + move))
            ids.insert(j, ids.pop(i))
            for pos, vid in enumerate(ids):
                s.execute(update(SavedViewRow).where(SavedViewRow.id == vid).values(position=pos))
    return get(engine, view_id)


def duplicate(engine: Engine, view_id: int) -> View | None:
    v = get(engine, view_id)
    return create(engine, v.state, f"{v.name} (copy)"[:80]) if v else None


def delete(engine: Engine, view_id: int) -> bool:
    with Session(engine) as s, s.begin():
        r = s.get(SavedViewRow, view_id)
        if r is None:
            return False
        s.delete(r)
        return True


def matches(engine: Engine, st: State, p: Profile = PROFILE) -> int:
    if st.page == "events":
        return queries.events(engine, st.filters, limit=1, p=p).total
    if st.page == "units":
        return queries.search_units(engine, queries.ListingQuery(st.filters, st.q, st.sort, limit=1), p).total
    return queries.search_listings(engine, queries.ListingQuery(st.filters, st.q, st.sort, limit=1), p).total


_counts: dict[tuple, tuple[float, int]] = {}


def with_counts(engine: Engine, p: Profile = PROFILE) -> list[View]:
    """Every view with its current match count (cached for a minute; badges, not accounting)."""
    out = list_views(engine)
    for v in out:
        ck = (str(engine.url), v.id, v.updated)
        hit = _counts.get(ck)
        if not hit or time.monotonic() - hit[0] > 60:
            hit = _counts[ck] = (time.monotonic(), matches(engine, v.state, p))
        v.count = hit[1]
    return out
