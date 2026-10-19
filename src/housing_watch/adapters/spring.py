"""Spring Leasing and Management: one WordPress page, a collapsed <details> per building.

Rent/income tables are screenshots, so they are OCR'd (tesseract TSV, sparse mode) and rebuilt by
geometry: each household-size cell anchors a row; its income is the income cell nearest in y to its
right, its size and rent the nearest cells to its left. Side-by-side tables (RADROC's 70% | 80%) are
split by the x position of their household columns. AMI only comes from horizontal "NN% AREA MEDIAN"
bands; the vertical AMI labels are not OCR-readable, so those rows carry ami=None (bands still judge).
A table whose rows do not all reassemble is dropped (units=[] -> "possible") rather than guessed.
OCR text is cached per image URL + sha256, so unchanged screenshots are not re-read.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from ._page import PageAdapter

URL = "https://springmanagement.net/apartments-for-rent/"
APPLY = "Applications by request: email marketing@springleasing.net or call 516-665-0015"
_MARK = "@@SPRING-DETAILS-{}@@"


def best_src(img) -> str:
    """Largest candidate in srcset (the original upload), else src."""
    best, width = img.get("src"), 0
    for part in (img.get("srcset") or "").split(","):
        bits = part.strip().split()
        if len(bits) == 2 and bits[1].endswith("w") and int(bits[1][:-1]) > width:
            best, width = bits[0], int(bits[1][:-1])
    return best


@dataclass
class Section:
    building: str
    label: str  # "AVAILABLE UNITS" / "IMMEDIATE LEASING" / "WAITING LIST"
    images: list[str]
    info: str  # building text above the <details>


def sections(html: str) -> list[Section]:
    s = parse.soup(html)
    details = s.find_all("details")
    found = []
    for i, d in enumerate(details):
        building = re.sub(r"\s*UNIT\s*\(?S?\s*\)?\s*AVAILABLE.*$", "", d.summary.get_text(" ", strip=True), flags=re.I)
        building = re.sub(r"\s+", " ", building).strip()
        label, secs = "AVAILABLE UNITS", []
        for el in d.find_all(["p", "figure", "img"]):
            if el.name == "p" and el.get_text(strip=True):
                label = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).upper()
            elif el.name == "img":
                if secs and secs[-1][0] == label:
                    secs[-1][1].append(best_src(el))
                else:
                    secs.append((label, [best_src(el)]))
        found.append((building, secs))
        d.replace_with(_MARK.format(i))
    chunks = re.split(r"@@SPRING-DETAILS-\d+@@", parse.text(s))
    out = []
    for i, (building, secs) in enumerate(found):
        info = chunks[i]
        if "Address:" in info:  # keep from the building name (the line before "Address:") on
            info = info[info.rfind("\n", 0, max(0, info.rfind("Address:") - 1)) + 1 :]
        if not secs:
            secs = [("AVAILABLE UNITS", [])]
        out += [Section(building, label, imgs, info) for label, imgs in secs]
    return out


# --- OCR table reconstruction -------------------------------------------------------------------------

_HH = re.compile(r"^\W*(\d)\s*(?:(?:-|–|—|to)\s*(\d))?\s*(person|people|persons)\b", re.I)
_RANGE = re.compile(r"\$\s?(\d[\d,]*)(?:\.\d\d)?\s*[-–—~]\s*\$?\s?(\d[\d,]*)")
_ONE = re.compile(r"\$\s?(\d[\d,]*)(?:\.\d\d)?")
_AMI = re.compile(r"(\d{2,3})\s*%\s*AREA\s*MEDIAN", re.I)
_SIZE_RANGE = re.compile(r"studio\s*[-–—]\s*(\d)\s*bed", re.I)


@dataclass
class Cell:
    text: str
    x: int
    y: int  # vertical centre
    w: int


def cells_from_tsv(tsv: str) -> list[Cell]:
    """Tesseract TSV -> one Cell per OCR line (sparse mode puts one table cell per line)."""
    groups: dict[tuple, list] = {}
    for row in tsv.splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12 or f[0] != "5" or not f[11].strip():
            continue
        left, top, w, h = (int(v) for v in f[6:10])
        groups.setdefault((f[2], f[3], f[4]), []).append((left, top, w, h, f[11].strip()))
    out = []
    for words in groups.values():
        x0 = min(a[0] for a in words)
        x1 = max(a[0] + a[2] for a in words)
        y = sum(a[1] + a[3] / 2 for a in words) / len(words)
        out.append(Cell(" ".join(a[4] for a in words), x0, int(y), x1 - x0))
    return out


def _num(s: str) -> int:
    return int(s.replace(",", ""))


def _kind(c: Cell) -> str | None:
    t = c.text
    if _AMI.search(t):
        return "ami"
    if _HH.search(t):
        return "hh"
    m = _RANGE.search(t)
    if m:
        return "income" if _num(m.group(2)) >= 10_000 else "rent"
    m = _ONE.search(t)
    if m and _num(m.group(1)) < 10_000:
        return "rent"
    if re.search(r"studio|bdrm|bedroom|\bbr\b", t, re.I) and _size(t) != "unknown":
        return "size"
    return None


def _size(text: str) -> str:
    return parse.size(re.sub(r"\bBDRMS?\b", "bedroom", text, flags=re.I))  # "1 BDRM"


def _sizes(text: str) -> list[str]:
    m = _SIZE_RANGE.search(text)
    if m:
        return ["studio"] + [f"{n}br" for n in range(1, int(m.group(1)) + 1)]
    return [_size(text)]


def _bands(hh: str, income: str, ami: int | None) -> list[Band]:
    m = _RANGE.search(income)
    lo, hi = _num(m.group(1)), _num(m.group(2))
    # money_range ignores numbers under 4 digits, so a "$0" minimum is passed as a placeholder and restored
    bands = parse.bands_from_row(hh, f"${max(lo, 1000):,} - ${hi:,}", ami)
    return [Band(b.household, lo, b.income_max) for b in bands]


# HUD 100% AMI for NYC, households 1-4: FY2025 (Spring's screenshots are dated 2025-06) and parse's FY2026.
_AMI100 = [{1: 113_400, 2: 129_600, 3: 145_800, 4: 162_000}, {h: parse.AMI100[h] for h in (1, 2, 3, 4)}]


def infer_ami(bands: list[Band]) -> int | None:
    """AMI % from a per-household cap when the label is unreadable: only an exact HUD multiple of 5 counts."""
    for b in bands:
        for table in _AMI100:
            if b.income_max and b.household in table:
                pct = b.income_max / table[b.household] * 100
                if abs(pct - round(pct / 5) * 5) < 0.05:
                    return int(round(pct / 5) * 5)
    return None


def units_from_tsv(tsv: str) -> list[Unit]:
    """Rebuild a rent/income screenshot into Units; [] when any row fails to reassemble."""
    cells = [(c, k) for c in cells_from_tsv(tsv) if (k := _kind(c))]
    hhs = sorted((c for c, k in cells if k == "hh"), key=lambda c: (c.y, c.x))
    if not hhs:
        return []
    cols: list[int] = []  # x of each household column (one per side-by-side table)
    for x in sorted(c.x for c in hhs):
        if not cols or x - cols[-1] > 150:
            cols.append(x)

    def panel(c: Cell, side: str) -> int | None:
        if side == "right":  # incomes sit right of their household column
            left = [i for i, x in enumerate(cols) if x <= c.x + 20]
            return left[-1] if left else None
        right = [i for i, x in enumerate(cols) if x >= c.x]  # sizes and rents sit left of it
        return right[0] if right else None

    amis = sorted((c for c, k in cells if k == "ami"), key=lambda c: c.y)
    by: dict[tuple[str, int], list[Cell]] = {}
    for c, k in cells:
        if k in ("income", "size", "rent"):
            p = panel(c, "right" if k == "income" else "left")
            if p is not None:
                by.setdefault((k, p), []).append(c)

    def nearest(k: str, p: int, y: int, cap: int | None = None) -> Cell | None:
        cand = [c for c in by.get((k, p), []) if cap is None or abs(c.y - y) <= cap]
        return min(cand, key=lambda c: abs(c.y - y)) if cand else None

    tiers: dict[tuple, Unit] = {}
    for h in hhs:
        p = panel(h, "right")
        inc = nearest("income", p, h.y, cap=40)
        size = nearest("size", p, h.y)
        if inc is None or size is None:
            return []
        above = [a for a in amis if a.y < h.y]
        a = int(_AMI.search(above[-1].text).group(1)) if above else None
        rent_cell = nearest("rent", p, h.y)
        rent_txt = rent_cell.text if rent_cell is not None and abs(rent_cell.y - size.y) <= 60 else ""
        rents = [_num(x) for x in _ONE.findall(rent_txt)]
        sizes = _sizes(size.text)
        for i, sz in enumerate(sizes):
            key = (p, size.y, sz, a)
            if key not in tiers:
                rent = None
                if len(rents) == 1:
                    rent = float(rents[0])
                elif len(rents) == 2 and len(sizes) > 1 and i in (0, len(sizes) - 1):
                    rent = float(rents[0] if i == 0 else rents[1])  # range: smallest size pays the low end
                tiers[key] = Unit(size=sz, rent=rent, ami=a)
            tiers[key].bands += _bands(h.text, inc.text, a)
    units = list(tiers.values())
    for u in units:
        if u.ami is None:
            u.ami = infer_ami(u.bands)
        if any(b.income_max is not None and b.income_min is not None and b.income_min > b.income_max for b in u.bands):
            return []
    return units


def ocr_tsv(path: str) -> str:
    return subprocess.run(
        ["tesseract", path, "-", "--psm", "11", "tsv"], capture_output=True, text=True, timeout=180
    ).stdout


def _ocr_cached(cache: dict, url: str, seen: set[str]) -> str:
    data = fetch.get(url).content
    h = hashlib.sha256(data).hexdigest()
    store = cache.setdefault("ocr", {})
    seen.add(url)
    hit = store.get(url)
    if hit and hit.get("sha") == h:
        return hit["text"]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "img" + (os.path.splitext(url.split("?")[0])[1] or ".png"))
        with open(p, "wb") as f:
            f.write(data)
        text = ocr_tsv(p)
    store[url] = {"sha": h, "text": text}
    return text


def build_listings(html: str, tsv_for) -> list[Listing]:
    """tsv_for(image_url) -> tesseract TSV text (live: OCR; tests: fixture files)."""
    out = []
    for sec in sections(html):
        m = re.search(r"Address:\s*(.+?)\s*(?:\n[A-Z][a-z]+:|$)", sec.info, re.S)
        addr = re.sub(r"\s+", " ", m.group(1)).replace(" th ", "th ").strip() if m else None
        waitlist = "WAIT" in sec.label
        units: list[Unit] = []
        for img in sec.images:
            got = units_from_tsv(tsv_for(img))
            if not got:  # one unreadable table makes the section's units untrustworthy
                units = []
                break
            units += got
        senior = bool(re.search(r"62 years|\bsenior", sec.info, re.I))
        slug = re.sub(r"[^a-z0-9]+", "-", f"{sec.building} {sec.label}".lower()).strip("-")
        out.append(
            Listing(
                source="spring",
                ext_id=slug,
                title=f"{sec.building.title()} ({sec.label.title()})",
                url=URL,
                address=addr,
                borough=parse.borough(addr),
                status="waitlist" if waitlist else "open",
                kind="waitlist" if waitlist else "re-rental",
                units=units,
                senior_only=senior,
                notes=APPLY,
            )
        )
    return out


class Spring(PageAdapter):
    name = "spring"
    title = "Spring Leasing and Management"
    url = URL
    pages = [URL]
    link_re = re.compile(r"/wp-content/uploads/.*\.(png|jpe?g|webp)", re.I)
    every_hours = 12
    min_listings = 1

    def fetch(self) -> Result:
        snaps = super().fetch().snapshots
        seen: set[str] = set()
        listings = build_listings(fetch.html(URL), lambda u: _ocr_cached(self.cache, u, seen))
        store = self.cache.setdefault("ocr", {})
        for u in [u for u in store if u not in seen]:
            del store[u]
        return Result(listings=listings, snapshots=snaps)
