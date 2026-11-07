"""The applicant profile in force: the `profile` row the owner set from the UI or MCP, else the HW_* env vars.

Verdicts are computed on read from it (eligibility/), so setting it re-judges every listing without a write;
the read cache keys on the profile itself. `current` reads one small row per call.
"""

from __future__ import annotations

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .eligibility import PROFILE, Profile
from .models import Borough, Refused, Size, values
from .orm import ProfileRow


def current(engine: Engine) -> Profile:
    with Session(engine) as s:
        r = s.get(ProfileRow, 1)
    if r is None:
        return PROFILE
    return Profile(r.household, r.income, frozenset(r.sizes), frozenset(r.excluded_boroughs))  # type: ignore[arg-type]


def is_set(engine: Engine) -> bool:
    """True when the owner set the profile (else it is the env vars')."""
    with Session(engine) as s:
        return s.get(ProfileRow, 1) is not None


def _set_of(alias, got, name: str) -> list[str]:
    got = list(dict.fromkeys(got))
    if bad := set(got) - set(values(alias)):
        raise Refused(f"{name}: {', '.join(sorted(bad))} not one of {', '.join(values(alias))}")
    return got


def update(engine: Engine, household: int | None = None, income: int | None = None,
           sizes: list[str] | None = None, excluded_boroughs: list[str] | None = None) -> Profile:  # fmt: skip
    """Change any of the profile's fields (the others keep their current values) and return the new profile."""
    p = current(engine)
    hh = p.household if household is None else household
    inc = p.income if income is None else income
    if hh < 1 or hh > 20:
        raise Refused("household: between 1 and 20")
    if inc <= 0:
        raise Refused("income: a positive annual household income")
    sz = _set_of(Size, sorted(p.sizes) if sizes is None else sizes, "sizes")
    if not sz:
        raise Refused("sizes: at least one")
    ex = _set_of(Borough, sorted(p.excluded_boroughs) if excluded_boroughs is None else excluded_boroughs,
                 "excluded_boroughs")  # fmt: skip
    vals = {"household": hh, "income": inc, "sizes": sz, "excluded_boroughs": ex}
    stmt = insert(ProfileRow).values(id=1, **vals)
    with Session(engine) as s, s.begin():
        s.execute(stmt.on_conflict_do_update(index_elements=["id"], set_={**vals, "updated": func.now()}))
    return current(engine)


def reset(engine: Engine) -> Profile:
    """Go back to the HW_* env vars' profile."""
    with Session(engine) as s, s.begin():
        s.execute(delete(ProfileRow))
    return PROFILE
