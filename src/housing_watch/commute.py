"""Transit commute time from eligible listings to HW_COMMUTE_TO, by Google's Routes API (GOOGLE_MAPS_API_KEY).

The API is paid, so only eligible, still-listed listings are routed, each origin address once per destination
(again after STALE). Routes are for a weekday 8:30 AM New York departure. Results are stored raw in `commutes`;
queries.py attaches them to listings at read time.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import fetch
from .orm import CommuteRow

log = logging.getLogger("housing_watch")

DESTINATION = os.environ.get("HW_COMMUTE_TO", "")
KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = ",".join(
    ["routes.duration", "routes.legs.steps.travelMode", "routes.legs.steps.staticDuration"]
    + [f"routes.legs.steps.transitDetails.transitLine.{f}" for f in ("nameShort", "name", "vehicle.type")]
)
NY = ZoneInfo("America/New_York")
DEPART = (8, 30)  # weekday, New York time
STALE = timedelta(days=60)  # timetables change; re-route after this
BATCH = 100  # routes per refresh


def origin(address: str | None, borough: str | None) -> str | None:
    """The address to route from: as published when it names the city, else with its borough; None when
    there is no address or no way to place it."""
    a = " ".join((address or "").split()).strip(" -–—,")
    if not a:
        return None
    if re.search(r"\bNY\b|New York", a, re.I):
        return a
    return f"{a}, {borough}, NY" if borough else None


def departure(now: datetime) -> datetime:
    """The next weekday DEPART after now (the API routes transit only for future departures)."""
    t = now.astimezone(NY)
    d = t.replace(hour=DEPART[0], minute=DEPART[1], second=0, microsecond=0)
    while d <= t or d.weekday() >= 5:
        d = (d + timedelta(days=1)).replace(hour=DEPART[0], minute=DEPART[1])
    return d


def _secs(s: str | None) -> int:
    return int(float((s or "0s").rstrip("s")))


def summarize(resp: dict) -> tuple[int | None, dict]:
    """(minutes, {"lines", "walk_min", "transfers"}) of the first route; (None, {}) when there is none."""
    routes = resp.get("routes") or []
    if not routes:
        return None, {}
    r = routes[0]
    steps = [s for leg in r.get("legs") or [] for s in leg.get("steps") or []]
    rides = [
        s["transitDetails"]["transitLine"]
        for s in steps
        if s.get("travelMode") == "TRANSIT" and s.get("transitDetails")
    ]
    lines = [x.get("nameShort") or x.get("name") or "?" for x in rides]
    walk = sum(_secs(s.get("staticDuration")) for s in steps if s.get("travelMode") == "WALK")
    return round(_secs(r.get("duration")) / 60), {
        "lines": lines,
        "walk_min": round(walk / 60),
        "transfers": max(len(rides) - 1, 0),
    }


def route(src: str, dest: str, depart: datetime) -> dict:
    body = {
        "origin": {"address": src},
        "destination": {"address": dest},
        "travelMode": "TRANSIT",
        "departureTime": depart.isoformat(),
    }
    return fetch.post_json(URL, body, headers={"X-Goog-Api-Key": KEY, "X-Goog-FieldMask": FIELD_MASK})


def wanted(engine: Engine) -> set[str]:
    """Origins of the eligible, listed listings."""
    from . import queries  # queries imports this module

    return {o for x in queries.judged(engine) if x.verdict == "eligible" and not x.gone_since
            if (o := origin(x.address, x.borough))}  # fmt: skip


def refresh(engine: Engine, now: datetime | None = None, limit: int = BATCH) -> int:
    """Route the wanted origins that have no fresh route yet; returns how many were sent to the API."""
    if not (DESTINATION and KEY):
        return 0
    now = now or datetime.now(NY)
    with Session(engine) as s:
        fresh = set(
            s.scalars(
                select(CommuteRow.origin).where(
                    CommuteRow.destination == DESTINATION,
                    CommuteRow.status != "failed",
                    CommuteRow.computed > now - STALE,
                )
            )
        )
    todo = sorted(wanted(engine) - fresh)[:limit]
    depart = departure(now)
    for src in todo:
        try:
            minutes, info = summarize(route(src, DESTINATION, depart))
            status = "ok" if minutes is not None else "no_route"
        except fetch.FetchError as e:
            log.warning("commute: %s: %s", src, e)
            minutes, info, status = None, {"error": str(e)[:300]}, "failed"
        stmt = pg_insert(CommuteRow).values(origin=src, destination=DESTINATION, minutes=minutes, status=status,
                                            route=info, depart=depart, computed=now)  # fmt: skip
        stmt = stmt.on_conflict_do_update(
            index_elements=["origin", "destination"],
            set_={k: stmt.excluded[k] for k in ("minutes", "status", "route", "depart", "computed")},
        )
        with engine.begin() as c:
            c.execute(stmt)
    return len(todo)


def table(engine: Engine) -> dict[str, CommuteRow]:
    """origin -> route for the configured destination."""
    if not DESTINATION:
        return {}
    with Session(engine) as s:
        return {r.origin: r for r in s.scalars(select(CommuteRow).where(CommuteRow.destination == DESTINATION))}
