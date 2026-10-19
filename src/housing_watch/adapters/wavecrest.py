"""Wavecrest Rentals: one page of headings, each linking a PDF (page 1 = flyer table, rest = application).

Flyer tables come out of `pdftotext -layout` as one line per household size, with the size label and
rent vertically centred on whichever line is in the middle. Rows are grouped into units by the household
size resetting (1,2 | 1,2,3 | 2,3,4,5), and label/rent-only lines join the unit they sit inside. The
application's "Mail ... completed application to:" block says how to submit. PDF text is cached by sha256.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from ._page import PageAdapter

INDEX = "https://wavecrestrentals.com/opportunities.php"
OFFICE = re.compile(r"87-14\s*116\s*(?:th)?\s*Street,?\s*(?:\n\s*)?Richmond Hill,?\s*NY\s*11418", re.I)
INCOME = r"\$\d{2,3},\d{3}(?:\.\d\d)?"
HH_ROW = re.compile(
    rf"(?<![\d$,.])([1-9])\s*(?:persons?|people)?\s+(?:\$(?:\d,\d{{3}}|\d{{3,4}})(?:\.\d\d)?\s+)?{INCOME}", re.I
)
RENT = re.compile(r"\$(\d,\d{3}|\d{3,4})(?:\.\d\d)?(?![\d,])")
OUTSIDE_NYC = re.compile(r"\b(Hempstead|Mt\.? Vernon|Mount Vernon|Yonkers|White Plains|New Rochelle)\b", re.I)


def _pdf_text(data: bytes) -> str:
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.pdf")
        with open(p, "wb") as f:
            f.write(data)
        out = subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, text=True, timeout=120).stdout
        if len(out.strip()) < 80 and shutil.which("pdftoppm") and shutil.which("tesseract"):
            subprocess.run(["pdftoppm", "-r", "200", "-png", "-l", "4", p, os.path.join(d, "pg")], timeout=180)
            out = "\n".join(fetch._ocr_file(os.path.join(d, x)) for x in sorted(os.listdir(d)) if x.startswith("pg"))
        return out


def pdf_cached(cache: dict, url: str) -> str:
    data = fetch.get(url).content
    h = hashlib.sha256(data).hexdigest()
    store = cache.setdefault("pdf", {})
    if store.get(url, {}).get("sha") != h:
        store[url] = {"sha": h, "text": _pdf_text(data)}
    return store[url]["text"]


def index_links(html: str) -> list[tuple[str, str, str]]:
    """(heading, pdf url, blurb) per opportunity."""
    s = parse.soup(html)
    out = []
    for a in s.find_all("a", href=True):
        if not a["href"].lower().endswith(".pdf"):
            continue
        blurb = a.find_next(string=re.compile(r"\w{4}"))
        nxt = blurb.find_next(string=re.compile(r"pleased to announce", re.I)) if blurb else None
        out.append((a.get_text(" ", strip=True), urljoin(INDEX, a["href"]), str(nxt or "").strip()))
    return out


def table_units(page1: str, senior: bool) -> list[Unit]:
    lines = page1.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.search(r"household", ln, re.I)), None)
    if start is None:
        return []
    blocks: list[dict] = []
    pending: list[str] = []
    prev_hh = 99
    for ln in lines[start + 1 :]:
        if ln.strip().startswith("*"):
            break
        m = HH_ROW.search(ln)
        if not m:
            if ln.strip() and not re.search(r"minimum|maximum|available|size", ln, re.I):
                pending.append(ln)
            continue
        hh = int(m.group(1))
        if hh <= prev_hh:  # household size reset: a new unit starts
            if blocks and blocks[-1]["rent"] is None:
                blocks[-1]["lines"] += pending
                pending = []
            blocks.append({"rows": [], "lines": [], "rent": None})
        blk = blocks[-1]
        blk["lines"] += pending + [ln]
        pending = []
        incomes = [int(float(x[1:].replace(",", ""))) for x in re.findall(INCOME, ln)]
        blk["rows"].append((hh, incomes[0] if incomes else None, incomes[1] if len(incomes) > 1 else None))
        prev_hh = hh
        for b in blocks:
            b["rent"] = b["rent"] or _rent(b["lines"])
    if blocks:
        blocks[-1]["lines"] += pending
    units = []
    for b in blocks:
        words = []
        for ln in b["lines"]:
            t = re.sub(INCOME, " ", ln)
            t = RENT.sub(" ", t)
            t = re.sub(r"\b\d\b(?!\s*-?\s*(bed|br))", " ", t, flags=re.I)  # counts/household sizes, keep "1 Bedroom"
            t = re.sub(r"\b(persons?|people)\b", " ", t, flags=re.I)
            words += t.split()
        units.append(
            Unit(
                size=parse.size(" ".join(words)),
                rent=_rent(b["lines"]),
                bands=[Band(h, lo, hi) for h, lo, hi in b["rows"]],
                set_aside="senior" if senior else None,
            )
        )
    return units


def _rent(lines: list[str]) -> float | None:
    for ln in lines:
        m = RENT.search(re.sub(INCOME, " ", ln))
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def _block_after(full: str, pat: str) -> str | None:
    """Text right of `pat` plus the indented continuation lines under it (an address block)."""
    m = re.search(pat, full, re.I)
    if not m:
        return None
    first = full[m.end() : full.find("\n", m.end())].strip()
    out = [first] if first else []
    for ln in full[full.find("\n", m.end()) + 1 :].splitlines()[:4]:
        t = ln.strip()
        if not t or t.startswith("*") or re.match(r"(no payment|please|\d+\.\s)", t, re.I):
            break
        out.append(t)
    return re.sub(r"\s{2,}", " ", " ".join(out)).strip() or None


def apply_notes(url: str, full: str) -> str:
    multi = len([pg for pg in full.split("\f") if pg.strip()]) > 1
    parts = [f"application is in the PDF {url} (after the flyer page)" if multi else f"flyer {url}"]
    block = _block_after(full, r"(?:mail|e-mail)[^\n]{0,30}completed applications? to:")
    if block:
        emails = re.findall(r"[\w.+-]+@[\w-]+\.\w+", block)
        addr = re.sub(r"\((e-)?mail\)\s*", "", re.sub(r"[\w.+-]+@[\w-]+\.\w+", "", block), flags=re.I).strip(" ,")
        if emails:
            parts.append("email " + ", ".join(emails))
        if addr:
            regular = " by regular mail only" if re.search(r"regular mail only", full, re.I) else ""
            parts.append(f"mail{regular} to {addr}")
    else:
        flat = re.sub(r"\s+", " ", full)
        m = re.search(r"SELF[- ]ADDRESSED (?:STAMPED )?ENVELOPE (?:to|from):?\s*(.+?\b\d{5})", flat, re.I)
        if m:
            parts.append(f"request one by mailing a self-addressed envelope to {m.group(1)}")
    sites = [
        w
        for w in re.findall(r"\bwww\.[\w.-]+\.\w+", full)
        if not re.search(r"nyshcr|nyhousingsearch|wavecrest", w, re.I)
    ]
    if sites:
        parts.append("online " + ", ".join(dict.fromkeys(sites)))
    q = re.findall(r"[\w.+-]+@[\w-]+\.\w+", full.split("\f")[0])
    if q:
        parts.append("questions " + ", ".join(dict.fromkeys(q)))
    return "Apply: " + "; ".join(parts)


def parse_flyer(heading: str, url: str, blurb: str, text: str) -> Listing:
    page1 = text.split("\f")[0]
    flat = re.sub(r"\s+", " ", page1)
    senior = bool(re.search(r"\bsenior\b|\b62 years", flat, re.I))
    units = table_units(page1, senior)
    where = OFFICE.sub(" ", f"{heading}\n{blurb}\n{page1}")
    outside = OUTSIDE_NYC.search(f"{heading} {blurb} {flat}")
    addr = heading if re.match(r"\d", heading) else None
    if addr is None:
        m = re.search(r"\b(\d{1,5}(?:-\d{1,3})? [A-Z][\w ]+? (?:Street|Avenue|Road|Place))\b", OFFICE.sub(" ", flat))
        addr = m.group(1) if m else None
    notes = apply_notes(url, text)
    if outside:
        notes = f"Outside NYC ({outside.group(1)}). " + notes
    return Listing(
        source="wavecrest",
        ext_id=url.rstrip("/").split("/")[-1].removesuffix(".pdf"),
        title=heading,
        url=url,
        address=addr,
        borough=None if outside else parse.borough(where),
        status="open",  # the page drops opportunities when they close
        kind="waitlist" if re.search(r"waiting list", flat, re.I) else "re-rental",
        units=units,
        senior_only=senior,
        notes=notes,
    )


class Wavecrest(PageAdapter):
    name = "wavecrest"
    title = "Wavecrest Rentals"
    url = INDEX
    pages = [INDEX]
    every_hours = 24
    min_listings = 1

    def fetch(self) -> Result:
        snaps = super().fetch().snapshots
        out, seen = [], set()
        for heading, url, blurb in index_links(fetch.html(INDEX)):
            seen.add(url)
            out.append(parse_flyer(heading, url, blurb, pdf_cached(self.cache, url)))
        for u in [u for u in self.cache.get("pdf", {}) if u not in seen]:
            del self.cache["pdf"][u]
        return Result(listings=out, snapshots=snaps)
