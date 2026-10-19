"""Rockrose affordable waitlists: WordPress index of cards + paper-waitlist PDF buttons.

Cards (The Cove, Lyra) link detail pages whose per-household income chart is only a PNG in the header
slider, so it is OCR'd (tesseract --psm 11 reads every "$154,440"). The chart's rents are "TBD", so only the
income maxima matter: the AMI % is the one that explains the most maxima under HUD's table, each maximum
then names its household size, and a household size that does not grow starts the next unit type (sized
by NYC occupancy: max 2 people = studio, 3 = 1BR, 5 = 2BR). Buttons with no card (Hayden, Tribeca
Pointe) are paper waitlists whose PDF states "Area Median Income (AMI): 60%". OCR and PDF results are
cached per URL + content hash, so a file is only re-read when it changes.
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
from . import Adapter

INDEX = "https://rockrose.com/affordable-availabilities/"
QUESTIONS = "Questions: Housing Partnership 646-217-3387. No fee to apply."
OCCUPANCY = {2: "studio", 3: "1br", 5: "2br", 7: "3br"}  # largest household allowed -> unit type
AMI_GRID = sorted({*range(20, 171, 5), 165})
KNOWN = {  # paper waitlists with no card: (address, borough); the PDFs print only the Manhattan office
    "hayden": ("43-25 Hunter St, Long Island City", "Queens"),
    "tribeca pointe": ("41 River Terrace, New York, NY 10282", "Manhattan"),
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def tesseract(data: bytes, suffix: str = ".png") -> str:
    if not shutil.which("tesseract"):
        raise fetch.FetchError("tesseract not installed")
    with tempfile.NamedTemporaryFile(suffix=suffix) as f:
        f.write(data)
        f.flush()
        return subprocess.run(
            ["tesseract", f.name, "-", "--psm", "11", "--dpi", "300"], capture_output=True, text=True, timeout=180
        ).stdout


def pdftotext(data: bytes) -> str:
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.pdf")
        with open(p, "wb") as f:
            f.write(data)
        return subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, text=True, timeout=120).stdout


def units_from_chart(ocr: str) -> list[Unit]:
    """OCR of one income chart -> units (rent TBD) with per-household maxima.

    A "$..." token that is no HUD maximum is an OCR misread: the unit types on both sides of it can no
    longer be told from their household sizes, so they become "unknown" (still reported, never guessed).
    """
    tokens = re.findall(r"\$\s?([\dIlOoSB][\dIlOoSB,.]{4,}\d)", ocr)
    caps = [int(t.replace(",", "").replace(".", "")) if re.fullmatch(r"\d{2,3}[,.]\d{3}", t) else None for t in tokens]
    if not any(caps):
        return []
    best = max(AMI_GRID, key=lambda p: sum(c in {parse.ami_max(p, h) for h in parse.AMI100} for c in caps))
    by_cap = {parse.ami_max(best, h): h for h in parse.AMI100}
    groups: list[list[tuple[int, int]]] = []
    tainted: set[int] = set()
    for c in caps:
        h = by_cap.get(c) if c else None
        if h is None:
            tainted |= {len(groups) - 1, len(groups)}  # this group and whichever starts next
            continue
        if not groups or h <= groups[-1][-1][0]:
            groups.append([])
        groups[-1].append((h, c))
    return [
        Unit(
            size="unknown" if i in tainted else OCCUPANCY.get(g[-1][0], "unknown"),
            ami=best,
            bands=[Band(h, None, c) for h, c in g],
        )
        for i, g in enumerate(groups)
    ]


def parse_detail(url: str, html: str, charts: dict[str, str]) -> Listing:
    """charts: image url -> OCR text, for the PNGs in the header slider."""
    s = parse.soup(html)
    side = s.select_one("div.sidebar") or s

    def field(cls: str) -> str:
        el = side.select_one(f".{cls}")
        return re.sub(r"\s+", " ", el.get_text(" ")).strip() if el else ""

    title = field("title") or (s.title.string if s.title else url)
    addr = field("address")
    flat = re.sub(r"\s+", " ", parse.text(s))
    units: list[Unit] = []
    for img in charts:
        units += units_from_chart(charts[img])
    emails = sorted({a["href"][7:].split("?")[0].lower() for a in s.select("a[href^=mailto]")})
    hp = [e for e in emails if "housingpartnership" in e]
    rr = [e for e in emails if "rockrose" in e] or (["affordablerental@rockrose.com"] if "rockrose.com" in flat else [])
    closed = re.search(r"(no longer|not currently) accepting|waiting list is closed|waitlist is closed", flat, re.I)
    notes = (
        f"Apply by email only: get the application (G-1 PDF on {INDEX} or from {', '.join(rr) or 'Rockrose'}), "
        f"email it to {', '.join(hp) or field('schedule_a_visit')}; logged in order received. "
        f"Card: {field('unit_type')}, rent {field('rent')}, income {field('income_range')}. {QUESTIONS}"
    )
    return Listing(
        source="rockrose",
        ext_id=url.rstrip("/").split("/")[-1],
        title=title,
        url=url,
        address=addr or None,
        borough=parse.borough(addr) or parse.borough(title),
        status="closed" if closed else "waitlist",
        kind="waitlist",
        units=units or [Unit(size="unknown")],
        senior_only=bool(re.search(r"\bsenior\b|62 (years|and older)", flat, re.I)),
        notes=notes,
    )


def parse_paper(name: str, pdf_url: str, pdf: str) -> Listing:
    """A waitlist button with no card: the PDF application is all there is."""
    a = parse.ami(m.group(0)) if (m := re.search(r"Area Median Income \(AMI\):\s*\d+\s*%", pdf)) else None
    known = next((v for k, v in KNOWN.items() if k in name.lower()), None)
    mail = ""
    if m := re.search(r"Mail completed application to:\s*\n((?:[ \t]*\S.*\n){1,7})", pdf):
        mail = ", ".join(x.strip() for x in m.group(1).splitlines() if x.strip())
    head = pdf.split("Mail completed", 1)[0]
    return Listing(
        source="rockrose",
        ext_id=_slug(name),
        title=name,
        url=pdf_url,
        address=known[0] if known else None,
        borough=known[1] if known else parse.borough(head),
        status="waitlist",
        kind="waitlist",
        units=[Unit(size="unknown", ami=a)],
        notes=f"Paper waitlist: mail one original application ({pdf_url}) by regular mail to {mail or 'Rockrose'}.",
    )


def parse_index(html: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """-> (detail page urls with card titles, [(button name, pdf url)])."""
    s = parse.soup(html)
    cards = []
    for a in s.select("article.affordable-card a[href]"):
        h = a.select_one("h2")
        cards.append((urljoin(INDEX, a["href"]), h.get_text(" ", strip=True) if h else ""))
    buttons = []
    for a in s.select("div.applicationbuttons a[href]"):
        name = re.sub(r"\s*(Affordable\s+)?(Waitlist|Waiting List)\s+Application\s*$", "", a.get_text(" ", strip=True))
        buttons.append((name, urljoin(INDEX, a["href"])))
    return cards, buttons


def _matches(name: str, title: str, url: str) -> bool:
    key = re.sub(r"^the\s+", "", name.lower()).strip()
    return bool(key) and (key in title.lower() or _slug(key) in url)


class Rockrose(Adapter):
    name = "rockrose"
    title = "Rockrose affordable waitlists"
    url = INDEX
    every_hours = 24  # waitlists, logged in order received
    min_listings = 1

    def _cached(self, url: str, kind: str, make) -> str:
        """Fetch url; re-use the OCR/PDF text while the file's content hash is unchanged.

        (A conditional GET would be cheaper, but fetch.get raises on 304.)
        """
        store = self.cache.setdefault(kind, {})
        data = fetch.get(url).content
        digest = hashlib.sha256(data).hexdigest()
        hit = store.get(url)
        if hit and hit.get("sha256") == digest:
            return hit["text"]
        text = make(data)
        store[url] = {"sha256": digest, "text": text}
        return text

    def fetch(self) -> Result:
        cards, buttons = parse_index(fetch.html(INDEX))
        if not cards and not buttons:
            raise RuntimeError(f"{INDEX}: no cards or waitlist buttons (layout changed?)")
        out = []
        used_imgs, used_pdfs = set(), set()
        for url, _card_title in cards:
            html = fetch.html(url)
            s = parse.soup(html)
            charts = {}
            for img in s.select("header .slider img"):
                src = img.get("data-src") or img.get("src") or ""
                if re.search(r"\.png(\?|$)", src, re.I) and src not in charts:
                    charts[src] = self._cached(src, "ocr", tesseract)
                    used_imgs.add(src)
            lst = parse_detail(url, html, charts)
            for name, pdf in buttons:
                if _matches(name, lst.title, url):
                    lst.notes += f" Application PDF: {pdf}"
            out.append(lst)
        for name, pdf in buttons:
            if any(_matches(name, lst.title, lst.url) for lst in out):
                continue
            out.append(parse_paper(name, pdf, self._cached(pdf, "pdf", pdftotext)))
            used_pdfs.add(pdf)
        for kind, used in (("ocr", used_imgs), ("pdf", used_pdfs)):
            store = self.cache.get(kind, {})
            for u in [u for u in store if u not in used]:
                del store[u]
        return Result(listings=out)
