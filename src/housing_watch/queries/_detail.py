"""One listing with its units and events: the detail page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..criteria import PROFILE, Profile, Verdict, unit_verdict
from ..mapping import listing_from_row
from ..models import Band, Size
from ..orm import EventRow, ListingOverrideRow, ListingRow
from ._events import EventView, _event_view
from ._judging import ListingSummary, _load, _summary, judged_listing


@dataclass
class UnitView:
    size: Size
    unit_no: str | None
    rent: float | None
    ami: int | None
    set_aside: str | None
    available: bool | None
    bands: list[Band]
    verdict: Verdict
    reason: str
    chart_year: int | None = None  # HUD chart the ad's printed income caps come from


@dataclass
class OverrideInfo:
    fields: list[str]  # the published fields the owner set
    units: bool  # the owner replaced the unit list
    reason: str | None
    updated: datetime


@dataclass
class ListingDetail:
    summary: ListingSummary
    notes: str | None
    ext_id: str
    last_seen: datetime
    units: list[UnitView]
    events: list[EventView]
    household: int
    override: OverrideInfo | None = None  # the owner's correction, applied over the source's values


def listing_detail(engine: Engine, key: str, p: Profile = PROFILE) -> ListingDetail | None:
    with Session(engine) as s:
        rows = _load(s, ListingRow.key == key)
        if not rows:
            return None
        r = rows[0]
        lst = listing_from_row(r)
        units = [
            UnitView(
                u.size,
                u.unit_no,
                u.rent,
                u.ami,
                u.set_aside,
                u.available,
                list(u.bands),
                *unit_verdict(u, p),
                u.chart_year,
            )
            for u in lst.units
        ]
        ov = s.get(ListingOverrideRow, key)
        evs = s.scalars(select(EventRow).where(EventRow.key == key).order_by(EventRow.at.desc()).limit(50))
        return ListingDetail(
            summary=judged_listing(engine, key, p) or _summary(r, lst, p),  # with commute + tracker
            notes=lst.notes,
            ext_id=r.ext_id,
            last_seen=r.last_seen,
            units=units,
            events=[_event_view(e) for e in evs],
            household=p.household,
            override=OverrideInfo(
                sorted(ov.patch.get("fields") or {}), ov.patch.get("units") is not None, ov.reason, ov.updated
            )
            if ov
            else None,  # fmt: skip
        )
