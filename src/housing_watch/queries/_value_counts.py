"""Value counts for the filter builder."""

from __future__ import annotations

import time

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .. import filters as F
from ..criteria import PROFILE, Profile
from ..orm import BandRow, EventRow, ListingRow, UnitRow
from ._events import event_where
from ._judging import judged, judged_by_key
from ._search import COMPUTED, UNIT_COMPUTED, _name, filter_listings, filter_units, hides_gone

ENUM_MAX = 40  # a text field with at most this many distinct values is offered as a checkbox list
_distinct: dict[tuple, tuple[float, int]] = {}


def is_enum(engine: Engine, fld: F.Field, p: Profile = PROFILE) -> bool:
    if fld.choices or fld.order:
        return True
    if fld.type != "text":
        return False
    ck = (str(engine.url), fld.name)
    hit = _distinct.get(ck)
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1] <= ENUM_MAX
    if fld.scope == "computed":
        n = len({COMPUTED[_name(fld)](x) for x in judged(engine, p)})
    else:
        with Session(engine) as s:
            n = s.scalar(select(func.count(fld.column.distinct())))
    _distinct[ck] = (time.monotonic(), n)
    return n <= ENUM_MAX


def _values_by_key(s: Session, fld: F.Field, keys: list[str], p: Profile, engine: Engine):
    """(listing key, value) pairs for one listing/unit/band/computed field over the given listings."""
    if fld.scope == "computed":
        by = judged_by_key(engine, p)
        return [(k, COMPUTED[_name(fld)](by[k])) for k in keys if k in by]
    u, b, ls = UnitRow.__table__, BandRow.__table__, ListingRow.__table__
    if fld.scope == "listing":
        q = select(ls.c.key, fld.column).where(ls.c.key.in_(keys))
    elif fld.scope == "unit":
        q = select(u.c.listing_key, fld.column).distinct().where(u.c.listing_key.in_(keys))
    else:
        q = select(u.c.listing_key, fld.column).distinct().join(b, b.c.unit_id == u.c.id)
        q = q.where(u.c.listing_key.in_(keys))
    return s.execute(q).all()


def value_counts(
    engine: Engine, target: str, fld: F.Field, conds: list[F.Cond], q: str = "", p: Profile = PROFILE
) -> list[tuple[str | None, int]]:
    """Each value of an enum field with how many results it would give, under the other filters."""
    others = [c for c in conds if c.field is not fld]
    counts: dict = {}
    if target == "units":
        return _sorted_counts(fld, _unit_value_counts(engine, fld, others, q, p, hides_gone(conds)))
    with Session(engine) as s:
        if target == "events" and fld.scope == "event":
            q = select(fld.column, func.count()).where(*event_where(engine, others, p)).group_by(fld.column)
            counts = dict(s.execute(q).all())
        else:
            if target == "events":
                w = select(EventRow.key, func.count()).where(*event_where(engine, others, p), EventRow.key.isnot(None))
                weights = dict(s.execute(w.group_by(EventRow.key)).all())
            else:
                weights = {x.key: 1 for x in filter_listings(engine, others, q, p, hides_gone(conds))}
            seen: set = set()
            for k, v in _values_by_key(s, fld, list(weights), p, engine):
                if (k, v) not in seen:
                    seen.add((k, v))
                    counts[v] = counts.get(v, 0) + weights[k]
    return _sorted_counts(fld, counts)


def _sorted_counts(fld: F.Field, counts: dict) -> list[tuple[str | None, int]]:
    known = fld.choices or fld.order
    for v in known:
        counts.setdefault(v, 0)
    order = {v: i for i, v in enumerate(known)}
    return sorted(counts.items(), key=lambda kv: (order.get(kv[0], 99), -kv[1], str(kv[0])))


def _unit_value_counts(engine: Engine, fld: F.Field, others: list[F.Cond], q: str, p: Profile, hide: bool) -> dict:
    """How many units each value of the field would give on the Units page."""
    units = filter_units(engine, others, q, p, hide)
    if fld.scope == "computed":
        by_key = judged_by_key(engine, p)
        vals = [(u.id, UNIT_COMPUTED[_name(fld)](u, by_key[u.key])) for u in units]
    else:
        ids = [u.id for u in units]
        u, b, ls = UnitRow.__table__, BandRow.__table__, ListingRow.__table__
        q_ = select(u.c.id, fld.column).distinct().where(u.c.id.in_(ids))
        if fld.scope == "listing":
            q_ = q_.join(ls, ls.c.key == u.c.listing_key)
        elif fld.scope == "band":
            q_ = q_.join(b, b.c.unit_id == u.c.id)
        with Session(engine) as s:
            vals = s.execute(q_).all() if ids else []
    counts: dict = {}
    for _, v in set(vals):
        counts[v] = counts.get(v, 0) + 1
    return counts
