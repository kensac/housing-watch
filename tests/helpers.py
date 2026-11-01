"""Small helpers shared by several test modules. Not a test module."""

from datetime import UTC, datetime


def bands(u) -> list[tuple]:
    """A unit's income bands as (household, income_min, income_max) tuples."""
    return [(b.household, b.income_min, b.income_max) for b in u.bands]


def T(n: int) -> datetime:
    """A fixed UTC instant, n seconds after a base far from now."""
    return datetime.fromtimestamp(1_800_000_000 + n, UTC)
