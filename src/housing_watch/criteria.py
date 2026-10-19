"""Eligibility rules as code (profile from HW_* env vars). Every unit gets eligible / possible / excluded plus a reason.

"possible" is deliberately generous: anything not provably excluded is reported, so no rental is lost.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import ami
from .models import Listing, Unit


@dataclass(frozen=True)
class Profile:
    household: int = int(os.environ.get("HW_HOUSEHOLD", "1"))
    income: int = int(os.environ.get("HW_INCOME", "0"))  # required; __main__ refuses to run without it
    sizes: frozenset[str] = frozenset(os.environ.get("HW_SIZES", "studio,1br,2br,3br").split(","))
    excluded_boroughs: frozenset[str] = frozenset(b for b in os.environ.get("HW_EXCLUDED_BOROUGHS", "").split(",") if b)
    max_rent: float | None = None


PROFILE = Profile()

ELIGIBLE, POSSIBLE, EXCLUDED = "eligible", "possible", "excluded"


@dataclass
class Verdict:
    verdict: str
    reason: str
    units: list[tuple[Unit, str, str]] = field(default_factory=list)  # (unit, verdict, reason)


def unit_verdict(u: Unit, p: Profile = PROFILE) -> tuple[str, str]:
    if u.available is False:
        return EXCLUDED, "unit marked rented/closed"
    if u.set_aside:
        return EXCLUDED, f"set aside ({u.set_aside})"
    if u.size not in p.sizes and u.size != "unknown":
        return EXCLUDED, f"{u.size} not wanted"
    if u.rent and p.max_rent and u.rent > p.max_rent:
        return EXCLUDED, f"rent ${u.rent:,.0f} over max"
    band = next((b for b in u.bands if b.household == p.household), None)
    if u.bands and band is None:
        smallest = min(b.household for b in u.bands)
        if smallest > p.household:
            return EXCLUDED, f"needs household of {smallest}+"
        return POSSIBLE, "no band for household size"
    if band is None:
        # no income table: AMI alone can still rule a unit out (current chart, the most generous)
        if (c := ami.cap(u.ami, p.household)) and c < p.income:
            who = "1 person" if p.household == 1 else f"{p.household} people"
            return EXCLUDED, f"{u.ami}% AMI caps {who} below ${p.income:,}"
        why = "size unknown" if u.size == "unknown" else "no income table"
        return POSSIBLE, why
    lo, hi = band.income_min, band.income_max
    old = ""
    if hi is not None and p.income > hi:
        # an ad on an older HUD chart: the same AMI % on the current chart decides
        cur = u.chart_year and u.chart_year < ami.CURRENT_YEAR and ami.current_cap(hi, p.household, u.chart_year, u.ami)
        if not cur or p.income > cur:
            return EXCLUDED, f"over income (max ${hi:,})"
        old = f"on {ami.CURRENT_YEAR} limits (ad prints {u.chart_year} limits: max ${hi:,})"
    if lo is not None and p.income < lo:
        return EXCLUDED, f"under income (min ${lo:,})"
    if u.size == "unknown":
        return POSSIBLE, "income fits" + (f" {old}" if old else "") + ", size unknown"
    if hi is None or lo is None:
        return POSSIBLE, "income range incomplete" + (f", fits {old}" if old else "")
    if old:
        return ELIGIBLE, f"eligible {old}"
    return ELIGIBLE, f"${lo:,}-${hi:,} for {p.household}"


def listing_verdict(lst: Listing, p: Profile = PROFILE) -> Verdict:
    if lst.status == "closed":
        return Verdict(EXCLUDED, "closed")
    if lst.borough in p.excluded_boroughs:
        return Verdict(EXCLUDED, f"in {lst.borough}")
    if lst.senior_only:
        return Verdict(EXCLUDED, "senior only")
    if lst.kind == "homeownership":
        return Verdict(EXCLUDED, "homeownership, not a rental")
    if not lst.units:
        return Verdict(POSSIBLE, "no unit details published")
    judged = [(u, *unit_verdict(u, p)) for u in lst.units]
    if any(v == ELIGIBLE for _, v, _ in judged):
        if lst.borough is None and p.excluded_boroughs:
            return Verdict(POSSIBLE, "eligible unit(s), borough unknown", judged)
        return Verdict(ELIGIBLE, "eligible unit(s)", judged)
    if any(v == POSSIBLE for _, v, _ in judged):
        return Verdict(POSSIBLE, next(r for _, v, r in judged if v == POSSIBLE), judged)
    return Verdict(EXCLUDED, judged[0][2] if len(judged) == 1 else "no qualifying unit", judged)
