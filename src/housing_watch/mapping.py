"""The only translation between domain dataclasses (models.py) and ORM rows (orm.py)."""

from __future__ import annotations

from .models import Band, Borough, Kind, Listing, PublishedStatus, Size, Unit, values
from .orm import BandRow, ListingRow, UnitRow


def _check(lst: Listing) -> None:
    """The closed value sets hold: the API serves them as enums, so a stray value is an adapter bug, caught here."""
    bad = [(name, v, alias) for name, v, alias in [("status", lst.status, PublishedStatus), ("kind", lst.kind, Kind),
           ("borough", lst.borough, Borough), *[("size", u.size, Size) for u in lst.units]]
           if v is not None and v not in values(alias)]  # fmt: skip
    if bad:
        raise ValueError(f"{lst.key()}: " + "; ".join(f"{n} {v!r} is not one of {values(a)}" for n, v, a in bad))


def listing_from_row(r: ListingRow) -> Listing:
    return Listing(
        source=r.source,
        ext_id=r.ext_id,
        title=r.title or "",
        url=r.url or "",
        address=r.address,
        borough=r.borough,
        status=r.status,
        kind=r.kind,
        deadline=r.deadline,
        senior_only=r.senior_only,
        notes=r.notes,
        units=[
            Unit(
                size=u.size,
                rent=u.rent,
                ami=u.ami,
                unit_no=u.unit_no,
                set_aside=u.set_aside,
                available=u.available,
                chart_year=u.chart_year,
                bands=[Band(b.household, b.income_min, b.income_max) for b in u.bands],
            )
            for u in r.units
        ],
    )


def apply_listing(lst: Listing, r: ListingRow, units: bool = True) -> ListingRow:
    """Copy a Listing's published fields (not timestamps or fingerprint) onto a row; units=True replaces its units."""
    _check(lst)
    r.key, r.source, r.ext_id = lst.key(), lst.source, lst.ext_id
    r.title, r.url, r.address, r.borough = lst.title, lst.url, lst.address, lst.borough
    r.status, r.kind, r.deadline, r.senior_only, r.notes = (
        lst.status,
        lst.kind,
        lst.deadline,
        lst.senior_only,
        lst.notes,
    )
    if not units:
        return r
    r.units = [
        UnitRow(
            pos=i,
            size=u.size,
            rent=u.rent,
            ami=u.ami,
            unit_no=u.unit_no,
            set_aside=u.set_aside,
            available=u.available,
            chart_year=u.chart_year,
            bands=[BandRow(household=b.household, income_min=b.income_min, income_max=b.income_max) for b in u.bands],
        )
        for i, u in enumerate(lst.units)
    ]
    return r
