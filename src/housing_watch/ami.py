"""HUD NYC income limits: the 100% AMI chart per year, and the one way an ad's printed caps become Bands.

Ads print caps as a % of a year's chart (130% of 1 person = $154,440 in 2026, $147,420 in 2025) and many
still print last year's. A printed cap is matched exactly (HUD rounds to $10) to a (year, household, AMI %)
so the households it does not name get their caps from the same chart.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import Band, Unit

# HUD NYC 100% AMI by household size
CHARTS = {
    2026: {1: 118_800, 2: 135_700, 3: 152_700, 4: 169_600, 5: 183_200, 6: 196_800, 7: 210_400, 8: 224_000},
    2025: {1: 113_400, 2: 129_600, 3: 145_800, 4: 162_000, 5: 175_000, 6: 188_000, 7: 201_000, 8: 213_900},
}
CURRENT_YEAR = max(CHARTS)
AMI100 = CHARTS[CURRENT_YEAR]
PCTS = (130, *(p for p in range(30, 205, 5) if p != 130))  # 130% first: most ads are
TOL = 10  # $ slack when matching a printed cap


def cap(pct: float | None, household: int, year: int = CURRENT_YEAR) -> int | None:
    """Income cap for a household at an AMI % on a year's chart (HUD rounds to $10)."""
    chart = CHARTS.get(year, {})
    if not pct or household not in chart:
        return None
    return int(round(chart[household] * pct / 100 / 10) * 10)


def match(
    top_cap: int,
    household: int | None = None,
    ami: int | None = None,
    *,
    sizes: Iterable[int] | None = None,
    years: Iterable[int] | None = None,
    tol: int = TOL,
) -> tuple[int, int, int] | None:
    """A printed cap -> (chart year, household, AMI %) it is exactly; the printed household and AMI % first.

    `sizes` limits the households tried (default 8..1), `years` the charts (newest first)."""
    pcts = (ami, *(p for p in PCTS if p != ami)) if ami else PCTS
    hs = list(sizes) if sizes is not None else list(range(8, 0, -1))
    ys = sorted(years if years is not None else CHARTS, reverse=True)
    tries = [(household, p) for p in pcts] if household in hs else []
    for h, pct in tries + [(h, p) for p in pcts for h in hs if h != household]:
        for y in ys:
            c = cap(pct, h, y)
            if c and abs(c - top_cap) <= tol:
                return y, h, pct
    return None


def bands(
    households: Iterable[int], lo: int | None, hi: int | None, ami: int | None = None, *, sizes=None, tol: int = TOL
) -> tuple[list[Band], int | None]:
    """One income range printed over several households -> (per-household Bands, chart year or None).

    The printed max is the cap of the largest household it covers, on whichever chart it matches (SVEN:
    '130%, 1-2 people, up to $168,480' is 2025, so 1 person caps at $147,420, not 2026's $154,440). It may
    match a smaller household than printed (larger ones keep the max) or, with no households
    printed, end the range. A minimum equal to the smallest household's cap is a cap, not a minimum. An
    unmatched max scales by the current chart (at the printed AMI % when there is one). `sizes` and `tol`
    go to match."""
    hh = sorted(set(households))
    if hi is None:
        return [Band(h, lo, None) for h in hh], None
    if len(hh) == 1:
        m = match(hi, hh[0], ami, sizes=hh, tol=tol)
        return [Band(hh[0], lo, hi)], m and m[0]
    m = match(hi, hh[-1] if hh else None, ami, sizes=sizes, tol=tol)
    if m is None:
        hh = hh or [1]
        pct = ami or (hi / AMI100[hh[-1]] * 100 if hh[-1] in AMI100 else None)
        return [Band(h, lo, hi if h == hh[-1] else min(cap(pct, h) or hi, hi)) for h in hh], None
    year, top, pct = m
    hh = hh or list(range(1, top + 1))
    if lo and len(hh) > 1 and abs((cap(pct, hh[0], year) or 0) - lo) <= tol:
        lo = None  # 'Income $45,360-$58,320' (The Bridge) runs from the smallest household's cap
    return [Band(h, lo, hi if h > top else min(cap(pct, h, year) or hi, hi)) for h in hh], year


def year_of(bs: list[Band], ami: int | None = None) -> int | None:
    """The one chart every printed per-household cap matches exactly, else None."""
    ys = set()
    for b in bs:
        if b.income_max:
            m = match(b.income_max, b.household, ami, sizes=[b.household])
            if m is None:
                return None
            ys.add(m[0])
    return ys.pop() if len(ys) == 1 else None


def rows(bs: list[Band], ami: int | None = None) -> tuple[list[Band], int | None]:
    """Per-household rows as printed -> (Bands, chart year). Rows that all repeat one range are the unit's
    top cap stamped on every household (Pronto does this), so they are re-derived as one range."""
    los, his = {b.income_min for b in bs}, {b.income_max for b in bs}
    if len(bs) > 1 and len(his) == 1 and None not in his and len(los) == 1:
        return bands([b.household for b in bs], los.pop(), his.pop(), ami)
    return bs, year_of(bs, ami)


def stamp(units: Iterable[Unit]) -> None:
    """Record the chart each unit's printed caps are on, where they match one exactly."""
    for u in units:
        if u.chart_year is None:
            u.chart_year = year_of(u.bands, u.ami)


def current_cap(hi: int, household: int, year: int, ami: int | None = None) -> int | None:
    """A cap printed on an older chart -> the same household and AMI %'s cap on the current chart."""
    m = match(hi, household, ami, sizes=[household], years=[year])
    return cap(m[2], household) if m else None
