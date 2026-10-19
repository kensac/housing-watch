"""Turn un-notified events into one email. Nothing is sent when nothing worth reading happened."""

from __future__ import annotations

import html
import json
import os
import smtplib
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import db
from .criteria import ELIGIBLE, EXCLUDED, POSSIBLE, PROFILE, listing_verdict, unit_verdict
from .models import Listing

SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

LISTING_EVENTS = ("new", "reopened", "changed", "closed", "gone")
TITLES = {"new": "New", "reopened": "Reopened", "changed": "Changed", "closed": "Closed", "gone": "Removed"}


def _units(lst: Listing) -> list[str]:
    out = []
    for unit in lst.units:
        v, why = unit_verdict(unit)
        band = next((b for b in unit.bands if b.household == PROFILE.household), None)
        inc = f"${band.income_min or 0:,}-${band.income_max or 0:,}" if band else f"no {PROFILE.household}-person band"
        rent = f"${unit.rent:,.0f}" if unit.rent else "rent ?"
        out.append(
            f"{v.upper():8} {unit.size} {('#' + unit.unit_no) if unit.unit_no else ''} {rent}"
            f" {str(unit.ami) + '% AMI' if unit.ami else ''} {inc} ({why})".replace("  ", " ")
        )
    return out


def urgent(c) -> bool:
    """An open listing became eligible and nobody was told yet: first come, first served, so mail now."""
    for r in c.execute(
        "SELECT DISTINCT key FROM events WHERE notified IS NULL AND type IN ('new','reopened','changed')"
    ).fetchall():
        lst = db.load_listing(c, r["key"])
        if lst and lst.status == "open" and listing_verdict(lst).verdict == ELIGIBLE:
            return True
    return False


def build(c) -> tuple[str, str, str, list[int]] | None:
    rows = c.execute("SELECT * FROM events WHERE notified IS NULL ORDER BY at").fetchall()
    if not rows:
        return None
    ids = [r["id"] for r in rows]
    listing_rows = {r["key"]: r for r in c.execute("SELECT * FROM listings").fetchall()}
    sections: dict[str, list[tuple[str, str, list[str]]]] = {
        "eligible": [],
        "possible": [],
        "pages": [],
        "failed": [],
        "recovered": [],
        "closed": [],
    }
    counts = {"excluded_new": 0, "watching": 0}
    seen_keys: set[str] = set()
    for r in reversed(rows):  # newest event per key wins
        if r["key"] and r["key"] in seen_keys:
            continue
        if r["key"]:
            seen_keys.add(r["key"])
        typ = r["type"]
        if typ == "watching":
            counts["watching"] += 1
            continue
        if typ == "source_failed":
            d = json.loads(r["detail"] or "{}")
            sections["failed"].append((r["source"], f"{d.get('fails')} failures in a row: {d.get('error')}", []))
            continue
        if typ == "source_recovered":
            sections["recovered"].append((r["source"], "working again", []))
            continue
        if typ == "page_changed":
            d = json.loads(r["detail"] or "{}")
            lines = [f"+ {x}" for x in d.get("added", [])[:15]] + [f"new link: {x}" for x in d.get("new_links", [])]
            sections["pages"].append((r["source"], r["summary"], lines))
            continue
        lst = db.load_listing(c, r["key"])
        lr = listing_rows.get(r["key"])
        if lst is None or lr is None:
            continue
        v = listing_verdict(lst)  # business rule applied now, to stored data
        head = f"[{TITLES.get(typ, typ)}] {lst.title} ({lst.borough or 'borough ?'}) {lst.url}"
        detail = [f"source: {r['source']}  verdict: {v.verdict} ({v.reason})  status: {lst.status}"]
        if lst.deadline:
            detail.append(f"deadline: {lst.deadline}")
        if lst.notes:
            detail.append(lst.notes)
        detail += _units(lst)
        if typ in ("closed", "gone"):
            if v.verdict != EXCLUDED or lr["status"] != "closed":
                sections["closed"].append((head, "", []))
            continue
        if v.verdict == ELIGIBLE:
            sections["eligible"].append((head, "", detail))
        elif v.verdict == POSSIBLE:
            sections["possible"].append((head, "", detail))
        elif typ == "new":
            counts["excluded_new"] += 1
    if not any(sections.values()):
        return "", "", "", ids  # only noise: mark read, send nothing
    n_e, n_p = len(sections["eligible"]), len(sections["possible"])
    subject = f"Housing watch: {n_e} eligible, {n_p} to check"
    if sections["pages"]:
        subject += f", {len(sections['pages'])} page changes"
    if sections["failed"]:
        subject += f", {len(sections['failed'])} sources failing"
    order = [
        ("eligible", "Eligible"),
        ("possible", "Possible: check these"),
        ("pages", "Watched pages changed"),
        ("closed", "Closed or removed (were eligible/possible)"),
        ("failed", "Sources failing"),
        ("recovered", "Sources recovered"),
    ]
    plain, htm = [subject, ""], [f"<h2>{html.escape(subject)}</h2>"]
    for k, title in order:
        if not sections[k]:
            continue
        plain += [f"== {title} ({len(sections[k])})"]
        htm += [f"<h3>{html.escape(title)} ({len(sections[k])})</h3><ul>"]
        for head, sub, lines in sections[k]:
            plain += [f"* {head} {sub}".rstrip()] + [f"    {x}" for x in lines] + [""]
            htm += [
                f"<li><b>{html.escape(head)}</b> {html.escape(sub)}<br>"
                + "<br>".join(f"<code>{html.escape(x)}</code>" for x in lines)
                + "</li>"
            ]
        htm += ["</ul>"]
    tail = (
        f"Also: {counts['excluded_new']} new listings filtered out (over income, Bronx, 2BR+, set-aside),"
        f" {counts['watching']} pages now watched."
    )
    plain += [tail]
    htm += [f"<p><small>{html.escape(tail)}</small></p>"]
    return subject, "\n".join(plain), "\n".join(htm), ids


def send(c, dry_run: bool = False) -> str:
    built = build(c)
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
        c.executemany("UPDATE events SET notified=? WHERE id=?", [(time.time(), i) for i in ids])
        c.commit()
    return plain if dry_run else (f"sent: {subject}" if subject else "nothing notable; events marked read")
