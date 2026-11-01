"""The Listings and Units pages: filters, quick search, sorts and batches."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .. import filters as F
from .. import tracker as T
from ..criteria import EXCLUDED, PROFILE, Profile
from ..orm import ListingRow, UnitRow
from ._common import _SIZE_ORDER, _VRANK, VERDICTS
from ._judging import ListingSummary, UnitSummary, judged, judged_by_key, judged_units

COMPUTED = {  # read-time values behind filters.COMPUTED
    "verdict": lambda x: x.verdict,
    "reason": lambda x: x.reason,
    "eligible_units": lambda x: x.eligible_units,
    "unit_count": lambda x: x.units,
    "commute": lambda x: x.commute,
    "application": lambda x: x.application,
    "application_group": lambda x: x.application_group,
    "application_notes": lambda x: x.application_notes,
}


type Direction = Literal["asc", "desc"]


@dataclass(frozen=True)
class SortSpec:
    label: str
    key: Callable[[Any], Any]
    natural: Direction = "asc"  # the direction a sort starts in: newest first for times


@dataclass(frozen=True)
class Sort:
    field: str  # a key of LISTING_SORTS or UNIT_SORTS
    direction: Direction = "asc"


def order[T](rows: list[T], sorts: tuple[Sort, ...], specs: dict[str, SortSpec]) -> list[T]:
    """Sorted by each Sort in turn: the first decides, the later ones break its ties (Python's sort is stable)."""
    for s in reversed(sorts):
        rows.sort(key=specs[s.field].key, reverse=s.direction == "desc")
    return rows


DEFAULT_SORT = (Sort("verdict"),)


@dataclass
class ListingQuery:
    filters: list[F.Cond] = field(default_factory=list)
    q: str = ""  # quick search over title, address, borough and notes
    sort: tuple[Sort, ...] = DEFAULT_SORT
    offset: int = 0  # the list scrolls: each batch starts where the last one ended
    limit: int = 200


@dataclass
class ListingPage:
    items: list[ListingSummary]
    total: int
    next_offset: int | None  # None once the last batch is out
    verdict_counts: dict[str, int]  # over everything matching the other filters, for the verdict tabs


def _name(f: F.Field) -> str:
    """A computed field's name, also when the events page reaches it through `listing`."""
    return f.name.removeprefix("listing.")


def hides_gone(conds: list[F.Cond]) -> bool:
    """Removed listings stay out of the way unless a filter asks about removal."""
    return not any(_name(c.field) == "gone_since" for c in conds)


def filter_listings(
    engine: Engine, conds: list[F.Cond], q: str = "", p: Profile = PROFILE, hide_gone: bool | None = None
) -> list[ListingSummary]:
    """SQL for the column conditions, then the computed ones in Python over the cached verdicts."""
    allx = judged(engine, p)
    where = F.listing_where(conds)
    if where:
        with Session(engine) as s:
            keys = set(s.scalars(select(ListingRow.key).where(*where)))
        allx = [x for x in allx if x.key in keys]
    hide = hides_gone(conds) if hide_gone is None else hide_gone
    comp = [c for c in conds if c.field.scope == "computed"]
    words = q.strip().lower().split()
    return [
        x
        for x in allx
        if not (hide and x.gone_since)
        and all(F.match(c, COMPUTED[_name(c.field)](x)) for c in comp)
        and all(w in x.haystack for w in words)
    ]


def _by_stage(x: ListingSummary | UnitSummary) -> tuple:
    """Stage order (tracker.rank), then longest in its stage first."""
    since = x.application_since.timestamp() if x.application_since else float("inf")
    return (T.rank(x.application), since, x.title.lower())


LISTING_SORTS = {
    "verdict": SortSpec("Verdict", lambda x: (_VRANK[x.verdict], -x.last_changed.timestamp())),
    "changed": SortSpec("Last changed", lambda x: x.last_changed, "desc"),
    "first_seen": SortSpec("First seen", lambda x: x.first_seen, "desc"),
    "rent": SortSpec("Lowest rent that fits", lambda x: (x.rent_min is None, x.rent_min or 0)),
    "commute": SortSpec("Commute", lambda x: (x.commute is None, x.commute or 0)),
    "application": SortSpec("Application stage", _by_stage),
    "deadline": SortSpec("Deadline", lambda x: (x.deadline is None, x.deadline or date.min)),
    "title": SortSpec("Title", lambda x: x.title.lower()),
    "source": SortSpec("Source", lambda x: (x.source, x.title.lower())),
    "borough": SortSpec("Borough", lambda x: (x.borough or "~", x.title.lower())),
}


def _ranges_over(rows: list[ListingSummary], units: list[UnitSummary]) -> list[ListingSummary]:
    by: dict[str, list[UnitSummary]] = {}
    for u in units:
        by.setdefault(u.key, []).append(u)
    out = []
    for x in rows:
        us = [u for u in by.get(x.key, []) if u.verdict != EXCLUDED] or by.get(x.key, [])
        rents, amis = [u.rent for u in us if u.rent], [u.ami for u in us if u.ami]
        out.append(replace(x, rent_min=min(rents, default=None), rent_max=max(rents, default=None),
                           ami_min=min(amis, default=None), ami_max=max(amis, default=None)))  # fmt: skip
    return out


def search_listings(engine: Engine, f: ListingQuery, p: Profile = PROFILE) -> ListingPage:
    verdict = [c for c in f.filters if c.field.name == "verdict"]
    base = filter_listings(engine, [c for c in f.filters if c not in verdict], f.q, p, hides_gone(f.filters))
    counts = {v: 0 for v in VERDICTS}
    for x in base:
        counts[x.verdict] += 1
    rows = [x for x in base if all(F.match(c, x.verdict) for c in verdict)]
    unit_conds = [c for c in f.filters if c.field.scope in ("unit", "band")]
    if unit_conds:  # ranges (and the rent sort) over the units the filters picked, of those that fit
        rows = _ranges_over(rows, filter_units(engine, unit_conds, p=p, hide_gone=False))
    order(rows, f.sort, LISTING_SORTS)
    start, end = max(0, f.offset), max(0, f.offset) + max(1, min(f.limit, 1000))
    return ListingPage(rows[start:end], len(rows), end if end < len(rows) else None, counts)


UNIT_COMPUTED = {  # the same computed fields on the Units page: verdict and reason are the unit's
    "verdict": lambda u, x: u.verdict,
    "reason": lambda u, x: u.reason,
    "eligible_units": lambda u, x: x.eligible_units,
    "unit_count": lambda u, x: x.units,
    "commute": lambda u, x: x.commute,
    "application": lambda u, x: x.application,
    "application_group": lambda u, x: x.application_group,
    "application_notes": lambda u, x: x.application_notes,
}


def filter_units(
    engine: Engine, conds: list[F.Cond], q: str = "", p: Profile = PROFILE, hide_gone: bool | None = None
) -> list[UnitSummary]:
    """Like filter_listings, but every unit and band condition must hold on the row's own unit."""
    by_key = judged_by_key(engine, p)
    allu = judged_units(engine, p)
    where = F.unit_where(conds)
    if where:
        with Session(engine) as s:
            ids = set(
                s.scalars(select(UnitRow.id).join(ListingRow, ListingRow.key == UnitRow.listing_key).where(*where))
            )
        allu = [u for u in allu if u.id in ids]
    hide = hides_gone(conds) if hide_gone is None else hide_gone
    comp = [c for c in conds if c.field.scope == "computed"]
    words = q.strip().lower().split()
    return [
        u
        for u in allu
        if not (hide and u.gone_since)
        and all(F.match(c, UNIT_COMPUTED[_name(c.field)](u, by_key[u.key])) for c in comp)
        and all(w in u.haystack for w in words)
    ]


UNIT_SORTS = {
    "verdict": SortSpec("Verdict", lambda u: (_VRANK[u.verdict], u.rent is None, u.rent or 0)),
    "rent": SortSpec("Rent", lambda u: (u.rent is None, u.rent or 0)),
    "commute": SortSpec("Commute", lambda u: (u.commute is None, u.commute or 0)),
    "application": SortSpec("Application stage", _by_stage),
    "ami": SortSpec("AMI", lambda u: (u.ami is None, u.ami or 0)),
    "size": SortSpec("Size", lambda u: (_SIZE_ORDER.get(u.size, 99), u.rent or 0)),
    "income_min": SortSpec("Income min", lambda u: (u.income_min is None, u.income_min or 0)),
    "income_max": SortSpec("Income max", lambda u: (u.income_max is None, u.income_max or 0)),
    "changed": SortSpec("Last changed", lambda u: u.last_changed, "desc"),
    "first_seen": SortSpec("First seen", lambda u: u.first_seen, "desc"),
    "deadline": SortSpec("Deadline", lambda u: (u.deadline is None, u.deadline or date.min)),
    "title": SortSpec("Building", lambda u: (u.title.lower(), u.unit_no or "")),
    "borough": SortSpec("Borough", lambda u: (u.borough or "~", u.title.lower())),
    "source": SortSpec("Source", lambda u: (u.source, u.title.lower())),
}


@dataclass
class UnitPage:
    items: list[UnitSummary]
    total: int
    next_offset: int | None
    verdict_counts: dict[str, int]


def search_units(engine: Engine, f: ListingQuery, p: Profile = PROFILE) -> UnitPage:
    verdict = [c for c in f.filters if c.field.name == "verdict"]
    base = filter_units(engine, [c for c in f.filters if c not in verdict], f.q, p, hides_gone(f.filters))
    counts = {v: 0 for v in VERDICTS}
    for u in base:
        counts[u.verdict] += 1
    rows = [u for u in base if all(F.match(c, u.verdict) for c in verdict)]
    order(rows, f.sort, UNIT_SORTS)
    start, end = max(0, f.offset), max(0, f.offset) + max(1, min(f.limit, 1000))
    return UnitPage(rows[start:end], len(rows), end if end < len(rows) else None, counts)
