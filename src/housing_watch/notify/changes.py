"""A `changed` event's before/after (db._brief) in words, and whether the change is news.

A status that moves to or from "unknown" with nothing else changed is the source failing to show a label
(Reside pages flap like this), not the listing changing. Back to the status it had before is noise; a
first known status after "unknown" is news.
"""

from __future__ import annotations

UNKNOWN = "unknown"
# A brief's unit is a list in this order (db._brief); events from before bands were kept have no BANDS.
SIZE, UNIT_NO, RENT, AMI, AVAILABLE, SET_ASIDE, BANDS = range(7)


def _money(v) -> str:
    return f"${v:,.0f}" if v is not None else "?"


def _ukey(u: list) -> tuple:
    return (u[SIZE], u[UNIT_NO], u[AMI])  # a rent change keeps the unit, a re-label does not


def _label(u: list) -> str:
    size = {"studio": "Studio"}.get(u[SIZE], (u[SIZE] or "?").upper())
    no = f"#{u[UNIT_NO]}" if u[UNIT_NO] else ""
    ami = f"{u[AMI]}% AMI" if u[AMI] else ""
    return " ".join(x for x in (size, no, ami) if x)


def _avail(v) -> str:
    return {True: "available", False: "taken", None: "not stated"}[v]


def _bands(u: list) -> str:
    bands = u[BANDS] if len(u) > BANDS else None
    return ", ".join(f"{h}p {_money(lo)}-{_money(hi)}" for h, lo, hi in bands or []) or "none"


def describe(before: dict, after: dict) -> list[str]:
    """One line per difference, most decisive first: status, deadline, units gone or added, rent, the rest."""
    out = []
    if before.get("status") != after.get("status"):
        out.append(f"Status: {before.get('status')} → {after.get('status')}")
    if before.get("deadline") != after.get("deadline"):
        out.append(f"Deadline: {before.get('deadline') or 'none'} → {after.get('deadline') or 'none'}")
    for k in ("kind", "borough", "senior_only"):
        if k in before and k in after and before[k] != after[k]:
            out.append(f"{k.replace('_', ' ').capitalize()}: {before[k]} → {after[k]}")
    b = {_ukey(u): u for u in before.get("units") or []}
    a = {_ukey(u): u for u in after.get("units") or []}
    gone = [u for k, u in b.items() if k not in a]
    new = [u for k, u in a.items() if k not in b]
    for x in list(gone):  # the same unit (number, or size and rent) under a new label: the source re-described it
        y = next((y for y in new if (x[UNIT_NO] and x[UNIT_NO] == y[UNIT_NO])
                  or (x[SIZE] == y[SIZE] and x[RENT] == y[RENT])), None)  # fmt: skip
        if y:
            gone.remove(x)
            new.remove(y)
            out.append(f"Relabeled: {_label(x)} {_money(x[RENT])} → {_label(y)} {_money(y[RENT])}")
    out += [f"Unit removed: {_label(u)} {_money(u[RENT])}" for u in gone]
    out += [f"Unit added: {_label(u)} {_money(u[RENT])}" for u in new]
    for k in b.keys() & a.keys():
        x, y = b[k], a[k]
        if x[RENT] != y[RENT]:
            out.append(f"Rent: {_label(y)} {_money(x[RENT])} → {_money(y[RENT])}")
        if x[AVAILABLE] != y[AVAILABLE]:
            out.append(f"{_label(y)}: {_avail(x[AVAILABLE])} → {_avail(y[AVAILABLE])}")
        if len(x) > BANDS and len(y) > BANDS and x[BANDS] != y[BANDS]:
            out.append(f"Income limits, {_label(y)}: {_bands(x)} → {_bands(y)}")
    return out or ["Details changed (income limits or set-asides)"]


def lines(detail: dict) -> list[str]:
    """A `changed` event's differences in words: as db.py recorded them, or described from its before/after
    (events from before 2026-10-22 have no `changes`)."""
    return detail.get("changes") or describe(detail.get("before") or {}, detail.get("after") or {})


def _rest(d: dict) -> dict:
    """Everything but the status, units in a stable order (a stored row's rent is a float, a fresh one may not be)."""
    units = sorted(repr([*u[:RENT], None if u[RENT] is None else float(u[RENT]), *u[RENT + 1 :]])
                   for u in d.get("units") or [])  # fmt: skip
    return {k: v for k, v in d.items() if k not in ("status", "units")} | {"units": units}


def is_noise(before: dict, after: dict, prior: str | None = None) -> bool:
    """Only the status moved, and it moved to "unknown" or back from it to `prior`, the status it had before."""
    bs, as_ = before.get("status"), after.get("status")
    if bs == as_ or UNKNOWN not in (bs, as_):
        return False
    if _rest(before) != _rest(after):
        return False
    return as_ == UNKNOWN or (prior is not None and prior == as_)
