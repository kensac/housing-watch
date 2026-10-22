"""A `changed` event's before/after (db._brief) in words, and whether the change is news.

A status that moves to or from "unknown" with nothing else changed is the source failing to show a label
(Reside pages flap like this), not the listing changing. Back to the status it had before is noise; a
first known status after "unknown" is news.
"""

from __future__ import annotations

UNKNOWN = "unknown"


def _money(v) -> str:
    return f"${v:,.0f}" if v is not None else "?"


def _ukey(u: list) -> tuple:
    return (u[0], u[1], u[3])  # size, unit_no, ami: a rent change keeps the unit, a re-label does not


def _label(u: list) -> str:
    size = {"studio": "Studio"}.get(u[0], (u[0] or "?").upper())
    return " ".join(x for x in (size, f"#{u[1]}" if u[1] else "", f"{u[3]}% AMI" if u[3] else "") if x)


def _avail(v) -> str:
    return {True: "available", False: "taken", None: "not stated"}[v]


def _bands(u: list) -> str:
    bands = u[6] if len(u) > 6 else None
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
        y = next((y for y in new if (x[1] and x[1] == y[1]) or (x[0] == y[0] and x[2] == y[2])), None)
        if y:
            gone.remove(x), new.remove(y)
            out.append(f"Relabeled: {_label(x)} {_money(x[2])} → {_label(y)} {_money(y[2])}")
    out += [f"Unit removed: {_label(u)} {_money(u[2])}" for u in gone]
    out += [f"Unit added: {_label(u)} {_money(u[2])}" for u in new]
    for k in b.keys() & a.keys():
        x, y = b[k], a[k]
        if x[2] != y[2]:
            out.append(f"Rent: {_label(y)} {_money(x[2])} → {_money(y[2])}")
        if x[4] != y[4]:
            out.append(f"{_label(y)}: {_avail(x[4])} → {_avail(y[4])}")
        if len(x) > 6 and len(y) > 6 and x[6] != y[6]:
            out.append(f"Income limits, {_label(y)}: {_bands(x)} → {_bands(y)}")
    return out or ["Details changed (income limits or set-asides)"]


def _rest(d: dict) -> dict:
    """Everything but the status, units in a stable order (a stored row's rent is a float, a fresh one may not be)."""
    units = sorted(repr([*u[:2], None if u[2] is None else float(u[2]), *u[3:]]) for u in d.get("units") or [])
    return {k: v for k, v in d.items() if k not in ("status", "units")} | {"units": units}


def is_noise(before: dict, after: dict, prior: str | None = None) -> bool:
    """Only the status moved, and it moved to "unknown" or back from it to `prior`, the status it had before."""
    bs, as_ = before.get("status"), after.get("status")
    if bs == as_ or UNKNOWN not in (bs, as_):
        return False
    if _rest(before) != _rest(after):
        return False
    return as_ == UNKNOWN or (prior is not None and prior == as_)
