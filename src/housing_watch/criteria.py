"""Eligibility rules as code (profile from HW_* env vars). Every unit gets eligible / possible / excluded plus a reason.

"possible" is for open or waitlisted listings whose data is missing or ambiguous. A listing that can't be applied
for yet (upcoming) or now (the source shows no status) is excluded unless it is plainly eligible; verdicts are
computed on read, so it comes back as soon as its source says more.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from . import ami
from .models import Borough, Listing, Size, Unit, values

type Verdict = Literal["eligible", "possible", "excluded"]
ELIGIBLE, POSSIBLE, EXCLUDED = values(Verdict)


def env_set(name: str, alias, default: str = "") -> frozenset:
    """A comma-separated env var as a set of `alias`'s values; an unknown value stops the program."""
    got = {v.strip() for v in os.environ.get(name, default).split(",") if v.strip()}
    if bad := got - set(values(alias)):
        raise SystemExit(f"{name}: {', '.join(sorted(bad))} not one of {', '.join(values(alias))}")
    return frozenset(got)


@dataclass(frozen=True)
class Profile:
    household: int = int(os.environ.get("HW_HOUSEHOLD", "1"))
    income: int = int(os.environ.get("HW_INCOME", "0"))  # annual household income; required to run
    sizes: frozenset[Size] = env_set("HW_SIZES", Size, "studio,1br,2br,3br")
    excluded_boroughs: frozenset[Borough] = env_set("HW_EXCLUDED_BOROUGHS", Borough)
    max_rent: float | None = None


PROFILE = Profile()


@dataclass
class Judgement:
    verdict: Verdict
    reason: str
    units: list[tuple[Unit, Verdict, str]] = field(default_factory=list)  # (unit, verdict, reason)


def unit_verdict(u: Unit, p: Profile = PROFILE) -> tuple[Verdict, str]:
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


def listing_verdict(lst: Listing, p: Profile = PROFILE) -> Judgement:
    if lst.status == "closed":
        return Judgement(EXCLUDED, "closed")
    if lst.borough in p.excluded_boroughs:
        return Judgement(EXCLUDED, f"in {lst.borough}")
    if lst.senior_only:
        return Judgement(EXCLUDED, "senior only")
    if lst.kind == "homeownership":
        return Judgement(EXCLUDED, "homeownership, not a rental")
    if not lst.units:
        if lst.status in ("upcoming", "unknown"):
            return Judgement(EXCLUDED, "upcoming, details not published yet" if lst.status == "upcoming"
                           else "not offered for application (the source shows no status)")  # fmt: skip
        return Judgement(POSSIBLE, "no unit details published")
    judged = [(u, *unit_verdict(u, p)) for u in lst.units]
    if lst.status == "unknown":
        return Judgement(EXCLUDED, "not offered for application (the source shows no status)", judged)
    if any(v == ELIGIBLE for _, v, _ in judged):
        if lst.borough is None and p.excluded_boroughs:
            return Judgement(POSSIBLE, "eligible unit(s), borough unknown", judged)
        return Judgement(ELIGIBLE, "eligible unit(s)", judged)
    if any(v == POSSIBLE for _, v, _ in judged):
        if lst.status == "upcoming":
            return Judgement(EXCLUDED, "upcoming, details not published yet", judged)
        return Judgement(POSSIBLE, next(r for _, v, r in judged if v == POSSIBLE), judged)
    return Judgement(EXCLUDED, judged[0][2] if len(judged) == 1 else "no qualifying unit", judged)
