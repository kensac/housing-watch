"""SOIS Real Estate Consulting: GoDaddy project cards, each with a status line and an HPD ad PDF (Attachment F-2).

Cards stay up after a lottery closes, so the card's status heading ("APPLICATIONS ARE CLOSED") decides status,
not the presence of the PDF. The ad's unit/income table is read from `pdftotext -layout` by column position
(`parse_hpd_ad`, also used by langsam: every HPD "Attachment F-2" ad has the same layout).
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from datetime import date, datetime
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from ._page import PageAdapter

URL = "https://soisrealestateconsulting.com/current-projects-1"

# --- shared PDF + HPD ad helpers -------------------------------------------------------------------------------


def pdf_bytes_text(data: bytes) -> str:
    """pdftotext -layout of PDF bytes; OCR of the first pages when the PDF is a scan."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.pdf")
        with open(p, "wb") as f:
            f.write(data)
        out = subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, text=True, timeout=120).stdout
        if len(out.strip()) >= 80:
            return out
        subprocess.run(["pdftoppm", "-r", "200", "-png", "-l", "4", p, os.path.join(d, "pg")], timeout=180)
        pages = sorted(x for x in os.listdir(d) if x.startswith("pg"))
        return "\n".join(fetch._ocr_file(os.path.join(d, x)) for x in pages)


def _pdf_text(cache: dict, url: str) -> str:
    """PDF text, re-extracted only when the file's bytes change (cache: url -> {sha, text})."""
    data = fetch.get(url).content
    h = hashlib.sha256(data).hexdigest()
    store = cache.setdefault("pdf", {})
    hit = store.get(url)
    if hit and hit.get("sha") == h:
        return hit["text"]
    t = pdf_bytes_text(data)
    store[url] = {"sha": h, "text": t}
    return t


def prune_pdf_cache(cache: dict, seen: set[str]) -> None:
    store = cache.get("pdf", {})
    for u in [u for u in store if u not in seen]:
        del store[u]


_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
_DATE_RE = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", re.I)


def deadline_iso(text: str) -> str | None:
    """'no later than April 7, 2025' / 'postmarked no later than March 20th, 2026' -> ISO date."""
    m = re.search(r"no later than\s+(?:the\s+)?(.{0,40})", text or "", re.I | re.S)
    d = _DATE_RE.search(m.group(1)) if m else None
    if not d:
        return None
    return datetime.strptime(f"{d.group(1)} {d.group(2)} {d.group(3)}", "%B %d %Y").date().isoformat()


def past(iso: str | None, today: date | None = None) -> bool:
    return bool(iso) and date.fromisoformat(iso) < (today or date.today())


def mail_to(text: str) -> str | None:
    """The address block after 'Mail completed application to:' in an HPD paper application."""
    m = re.search(r"mail completed application to:?\s*\n((?:.*\n){1,8})", text or "", re.I)
    if not m:
        return None
    lines = []
    for ln in m.group(1).splitlines():
        ln = ln.strip()
        if not ln:
            if lines:
                break
            continue
        lines.append(ln)
    return ", ".join(lines) if lines else None


_AMI_HDR = re.compile(r"(\d{2,3})\s*%\s*AREA\b", re.I)
_HH = re.compile(r"(?<![\d,$])([1-9])\s*(?:persons?|people)?\s+\$\s?([\d,]{4,})\s*[-–]\s*\$\s?([\d,]{4,})", re.I)
_RENT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d\d)?)\s+(\d{1,3})\b(?!,)")
_SIZE = re.compile(r"\b(studio|[1-6]\s*-?\s*(?:bedrooms?|br|bdr))\b", re.I)


def parse_hpd_ad(text: str) -> list[Unit]:
    """Units of an HPD/HCR ad table. Handles two tables side by side and AMI headers split over lines.

    Each household line is (size, income min-max); a row is a run of household sizes that restarts when the
    size goes down. Rent + unit-count lines anchor a row and carry its unit size; the AMI is the nearest
    header above the rent line on the same side of the page.
    """
    lines = text.splitlines()
    hdrs = [(i, m.start(), int(m.group(1))) for i, ln in enumerate(lines) for m in _AMI_HDR.finditer(ln)]
    if not hdrs:
        return []
    # header groups: headers within 12 lines of the group's first header share a table band
    groups: list[list[tuple[int, int, int]]] = []
    for h in hdrs:
        if groups and h[0] - groups[-1][0][0] <= 12:
            groups[-1].append(h)
        else:
            groups.append([h])

    def group_at(i: int):
        g = None
        for gr in groups:
            if gr[0][0] <= i + 3:  # a header may sit a line or two below its first row
                g = gr
        return g

    def split_of(gr) -> int:
        cols = sorted({c for _, c, _ in gr})
        return cols[-1] if len(cols) > 1 and cols[-1] - cols[0] > 40 else 10**6

    def side(gr, col: int) -> int:
        return 1 if col >= split_of(gr) else 0

    def ami_for(i: int, sd: int) -> int | None:
        best = None
        for li, col, pct in hdrs:
            gr = group_at(li)
            if li <= i and gr and side(gr, col) == sd:
                best = pct
        if best is None:  # header printed just below the first row
            below = [(li, pct) for li, col, pct in hdrs if li > i and side(group_at(li), col) == sd]
            best = min(below)[1] if below else None
        return best

    rows: dict[int, list[dict]] = {0: [], 1: []}
    rents = []
    for i, ln in enumerate(lines):
        gr = group_at(i)
        if gr is None:
            continue
        for m in _HH.finditer(ln):
            sd = side(gr, m.start())
            h, lo, hi = int(m.group(1)), parse.whole(m.group(2)), parse.whole(m.group(3))
            cur = rows[sd][-1] if rows[sd] else None
            if cur is None or h <= cur["bands"][-1].household:
                cur = {"first": i, "last": i, "bands": []}
                rows[sd].append(cur)
            cur["bands"].append(Band(h, lo, hi))
            cur["last"] = i
        sz = _SIZE.search(ln)
        seen_sides = set()
        hh_spans = [m.span() for m in _HH.finditer(ln)]
        for m in _RENT.finditer(ln):
            if any(a <= m.start() < b for a, b in hh_spans):
                continue
            sd = side(gr, m.start())
            if sd in seen_sides:  # first money on each side is the rent; later ones are asset limits
                continue
            seen_sides.add(sd)
            rents.append({"line": i, "side": sd, "rent": parse.money(m.group(1)), "size": sz.group(1) if sz else None})
    units = []
    for r in rents:
        size_txt = r["size"]
        if not size_txt:
            near = [(abs(j - r["line"]), _SIZE.search(lines[j])) for j in range(r["line"] - 3, r["line"] + 4)]
            near = [(d, m) for d, m in near if 0 <= d and m]
            size_txt = min(near, key=lambda x: x[0])[1].group(1) if near else ""
        cand = rows[r["side"]]
        row = next((x for x in cand if x["first"] <= r["line"] <= x["last"]), None)
        if row is None and cand:
            row = min(cand, key=lambda x: min(abs(x["first"] - r["line"]), abs(x["last"] - r["line"])))
        units.append(
            Unit(
                size=parse.size(size_txt),
                rent=r["rent"],
                ami=ami_for(r["line"], r["side"]),
                bands=list(row["bands"]) if row else [],
            )
        )
    return units


def ad_meta(text: str) -> dict:
    """Deadline, senior flag and how-to-apply sentence of an HPD ad."""
    flat = re.sub(r"\s+", " ", text or "")
    m = re.search(r"How Do You Apply\?\s*(.{0,400}?)(?:When is the Deadline|Only send one|$)", flat, re.I)
    return {
        "deadline": deadline_iso(flat),
        "senior": bool(re.search(r"\b62\s*(\+|years|or older)|senior housing", flat, re.I)),
        "apply": m.group(1).strip() if m else None,
    }


# --- SOIS ------------------------------------------------------------------------------------------------------


def _aid(el, prefix: str) -> bool:
    return (el.get("data-aid") or "").startswith(prefix)


def parse_cards(html: str) -> list[dict]:
    """Project cards in page order: title, status line, address, ad PDF url."""
    s = parse.soup(html)
    cards: list[dict] = []
    for el in s.find_all(["h2", "h4", "p", "a"]):
        if _aid(el, "CONTACT_SECTION_TITLE"):
            cards.append({"title": el.get_text(" ", strip=True), "status": "", "address": "", "pdf": None})
        elif not cards:
            continue
        elif _aid(el, "CONTACT_INTRO_HEADING"):
            cards[-1]["status"] = el.get_text(" ", strip=True)
        elif _aid(el, "CONTACT_INFO_ADDRESS"):
            cards[-1]["address"] = el.get_text(" ", strip=True)
        elif _aid(el, "PDF_DOWNLOAD_LINK") and el.get("href") and not cards[-1]["pdf"]:
            cards[-1]["pdf"] = urljoin(URL, el["href"])
    return cards


def build_listing(card: dict, ad_text: str | None, today: date | None = None) -> Listing:
    meta = ad_meta(ad_text or "")
    closed = bool(re.search(r"closed", card["status"], re.I)) or past(meta["deadline"], today)
    title = card["title"]
    notes = meta["apply"] or "See HPD ad PDF"
    if card["pdf"]:
        notes += f" | Ad: {card['pdf']}"
    return Listing(
        source="sois",
        ext_id=re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
        title=title,
        url=URL,
        address=card["address"] or None,
        borough=parse.borough(card["address"], ad_text[:600] if ad_text else None),
        status="closed" if closed else "open",
        kind="lottery",
        units=parse_hpd_ad(ad_text) if ad_text else [],
        deadline=meta["deadline"],
        senior_only=meta["senior"],
        notes=notes,
    )


class Sois(PageAdapter):
    name = "sois"
    title = "SOIS Real Estate Consulting"
    url = URL
    pages = [URL]
    every_hours = 24
    min_listings = 1

    def fetch(self) -> Result:
        snaps = super().fetch().snapshots
        out, seen = [], set()
        for card in parse_cards(fetch.html(URL)):
            txt = None
            if card["pdf"]:
                seen.add(card["pdf"])
                txt = _pdf_text(self.cache, card["pdf"])
            out.append(build_listing(card, txt))
        prune_pdf_cache(self.cache, seen)
        return Result(listings=out, snapshots=snaps)
