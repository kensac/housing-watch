"""Turn un-notified events into one email. It goes out only when an eligible listing is new, changed or closed.

Possible verdicts and watched-page changes are read in the web UI, not mailed; source failures ride along
in a digest that is going out anyway. A source's first run (events marked `baseline`) is its starting
inventory, not news: it becomes one summary line with counts, and only its eligible listings are shown.
"""

from __future__ import annotations

import html
import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from sqlalchemy import func, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import queries
from .criteria import ELIGIBLE, EXCLUDED, POSSIBLE, PROFILE, Profile, listing_verdict, unit_verdict
from .models import Listing
from .orm import EventRow

SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

TITLES = {"new": "New", "reopened": "Reopened", "changed": "Changed", "closed": "Closed", "gone": "Removed"}


def _units(lst: Listing, p: Profile) -> list[str]:
    out = []
    for unit in lst.units:
        v, why = unit_verdict(unit, p)
        band = next((b for b in unit.bands if b.household == p.household), None)
        inc = f"${band.income_min or 0:,}-${band.income_max or 0:,}" if band else f"no {p.household}-person band"
        rent = f"${unit.rent:,.0f}" if unit.rent else "rent ?"
        out.append(
            f"{v.upper():8} {unit.size} {('#' + unit.unit_no) if unit.unit_no else ''} {rent}"
            f" {str(unit.ami) + '% AMI' if unit.ami else ''} {inc} ({why})".replace("  ", " ")
        )
    return out


def _names(xs: set[str], n: int = 6) -> str:
    xs = sorted(xs)
    return ", ".join(xs[:n]) + (f" and {len(xs) - n} more" if len(xs) > n else "")


def urgent(engine: Engine, p: Profile = PROFILE) -> bool:
    """An open listing became eligible and nobody was told yet: first come, first served, so mail now."""
    with Session(engine) as s:
        keys = s.scalars(
            select(EventRow.key)
            .distinct()
            .where(EventRow.notified.is_(None), EventRow.type.in_(("new", "reopened", "changed")))
        ).all()
        lsts = queries.load_listings(s, keys).values()
    return any(lst.status == "open" and listing_verdict(lst, p).verdict == ELIGIBLE for lst in lsts)


def build(engine: Engine, p: Profile = PROFILE) -> tuple[str, str, str, list[int]] | None:
    """(subject, plain, html, event ids) for the pending events; subject "" means mark read, send nothing."""
    with Session(engine) as s:
        rows = queries.pending_events(s)
        if not rows:
            return None
        listings = queries.load_listings(s, (r.key for r in rows if r.type in queries.LISTING_EVENTS))
    ids = [r.id for r in rows]
    sections: dict[str, list[tuple[str, list[str]]]] = {
        k: [] for k in ("eligible", "possible", "pages", "failed", "recovered", "closed")
    }
    excluded_new = watching = 0
    base = {"sources": set(), "listings": 0, ELIGIBLE: 0, POSSIBLE: 0, EXCLUDED: 0, "pages": 0}
    seen: set[str] = set()
    for r in reversed(rows):  # newest event per key wins
        if r.key:
            if r.key in seen:
                continue
            seen.add(r.key)
        typ, d, baseline = r.type, r.detail or {}, queries.is_baseline(r.detail)
        if baseline:
            base["sources"].add(r.source)
        if typ in ("watching", "page_changed") and baseline:  # a first run's pages: counted, not listed
            base["pages"] += 1
            continue
        if typ == "watching":
            watching += 1
            continue
        if typ == "source_failed":
            sections["failed"].append((f"{r.source}: {d.get('fails')} failures in a row: {d.get('error')}", []))
            continue
        if typ == "source_recovered":
            sections["recovered"].append((f"{r.source}: working again", []))
            continue
        if typ == "page_changed":
            lines = [f"+ {x}" for x in d.get("added", [])[:15]] + [f"new link: {x}" for x in d.get("new_links", [])]
            sections["pages"].append((f"{r.source}: {r.summary}", lines))
            continue
        lst = listings.get(r.key)
        if lst is None:
            continue
        v = listing_verdict(lst, p)  # business rule applied now, to stored data
        if baseline:
            base["listings"] += 1
            base[v.verdict] += 1
            if v.verdict != ELIGIBLE:
                continue
        label = "First seen" if baseline else TITLES.get(typ, typ)
        head = f"[{label}] {lst.title} ({lst.borough or 'borough ?'}) {lst.url}"
        if typ in ("closed", "gone"):
            if listing_verdict(queries.as_open(lst), p).verdict == ELIGIBLE:  # was it worth knowing about?
                sections["closed"].append((head, []))
            continue
        detail = [f"source: {r.source}  verdict: {v.verdict} ({v.reason})  status: {lst.status}"]
        if lst.deadline:
            detail.append(f"deadline: {lst.deadline}")
        if lst.notes:
            detail.append(lst.notes)
        detail += _units(lst, p)
        if v.verdict == ELIGIBLE:
            sections["eligible"].append((head, detail))
        elif typ == "new":
            excluded_new += 1
    if not (sections["eligible"] or sections["closed"]):
        return "", "", "", ids  # nothing eligible happened (possible, page changes, failures alone): mark read
    subject = f"Housing watch: {len(sections['eligible'])} eligible"
    if sections["closed"]:
        subject += f", {len(sections['closed'])} closed"
    if sections["failed"]:
        n = len(sections["failed"])
        subject += f", {n} source{'s' if n != 1 else ''} failing"
    order = [
        ("eligible", "Eligible"),
        ("closed", "Closed or removed (were eligible)"),
        ("failed", "Sources failing"),
        ("recovered", "Sources recovered"),
    ]
    plain, htm = [subject, ""], [f"<h2>{html.escape(subject)}</h2>"]
    for k, title in order:
        if not sections[k]:
            continue
        plain += [f"== {title} ({len(sections[k])})"]
        htm += [f"<h3>{html.escape(title)} ({len(sections[k])})</h3><ul>"]
        for head, lines in sections[k]:
            plain += [f"* {head}"] + [f"    {x}" for x in lines] + [""]
            htm += [
                f"<li><b>{html.escape(head)}</b><br>"
                + "<br>".join(f"<code>{html.escape(x)}</code>" for x in lines)
                + "</li>"
            ]
        htm += ["</ul>"]
    tail = []
    if base["sources"]:
        tail.append(
            f"First run of {len(base['sources'])} source(s) ({_names(base['sources'])}):"
            f" {base['listings']} listings ({base[ELIGIBLE]} eligible, shown above; {base[POSSIBLE]} possible;"
            f" {base[EXCLUDED]} excluded), {base['pages']} pages now watched."
        )
    if excluded_new or watching:
        tail.append(f"Also: {excluded_new} new listings filtered out, {watching} pages now watched.")
    plain += tail
    htm += [f"<p><small>{html.escape(t)}</small></p>" for t in tail]
    return subject, "\n".join(plain), "\n".join(htm), ids


def send(engine: Engine, dry_run: bool = False) -> str:
    built = build(engine)
    if built is None:
        return "no events"
    subject, plain, htm, ids = built
    if subject and not dry_run:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = os.environ.get("HW_DIGEST_FROM") or os.environ["SMTP_USER"]
        msg["To"] = os.environ["HW_DIGEST_TO"]
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=msg["From"].rpartition("@")[2].strip("> ") or None)
        msg.set_content(plain)
        msg.add_alternative(htm, subtype="html")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as s:
            s.starttls()
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.send_message(msg)
    if not dry_run:
        with Session(engine) as s, s.begin():
            s.execute(update(EventRow).where(EventRow.id.in_(ids)).values(notified=func.now()))
    return plain if dry_run else (f"sent: {subject}" if subject else "nothing notable; events marked read")
