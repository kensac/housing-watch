"""St. Nicks Alliance: re-rentals are one-line links to flyer PDFs under "Option 2 ... Current openings:".

Each flyer (pdftotext -layout) gives size, AMI, rent and one income range for "1-2 persons". Its "CLICK HERE
TO APPLY" is a PDF link annotation (read with pdftohtml -xml) to the property's application PDF, whose
page 1 says where to mail it. PDF text is cached by URL + sha256 so unchanged flyers are not re-read.
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

INDEX = "https://stnicksalliance.org/get-help-affordable-housing/"


def _pdf_extract(data: bytes) -> dict:
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.pdf")
        with open(p, "wb") as f:
            f.write(data)
        txt = subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, text=True, timeout=120).stdout
        if len(txt.strip()) < 80 and shutil.which("pdftoppm") and shutil.which("tesseract"):
            subprocess.run(["pdftoppm", "-r", "200", "-png", "-l", "4", p, os.path.join(d, "pg")], timeout=180)
            txt = "\n".join(fetch._ocr_file(os.path.join(d, x)) for x in sorted(os.listdir(d)) if x.startswith("pg"))
        links: list[str] = []
        if shutil.which("pdftohtml"):
            xml = subprocess.run(
                ["pdftohtml", "-xml", "-stdout", "-i", "-q", p], capture_output=True, text=True, timeout=120
            ).stdout
            links = list(dict.fromkeys(re.findall(r'href="(https?://[^"]+)"', xml)))
    return {"text": txt, "links": links}


def pdf_cached(cache: dict, url: str) -> dict:
    """{'text', 'links'} of a PDF; re-extracted only when its bytes change."""
    data = fetch.get(url).content
    h = hashlib.sha256(data).hexdigest()
    store = cache.setdefault("pdf", {})
    hit = store.get(url)
    if hit and hit.get("sha") == h:
        return hit
    store[url] = {"sha": h, **_pdf_extract(data)}
    return store[url]


def openings(html: str) -> list[tuple[str, str]]:
    """(link text, flyer url) for each re-rental under 'Current openings:'."""
    s = parse.soup(html)
    start = s.find(string=re.compile(r"Current openings", re.I))
    if start is None:
        raise RuntimeError("St Nicks page has no 'Current openings' section")
    out = []
    for el in start.find_all_next(["a", "h2", "h3", "h4", "strong"]):
        txt = el.get_text(" ", strip=True)
        if re.match(r"Option\s*3", txt, re.I):
            break
        if el.name == "a" and el.get("href"):
            out.append((txt, urljoin(INDEX, el["href"])))
    return out


def mail_address(app_text: str) -> str | None:
    m = re.search(r"MAIL COMPLETE APPLICATION TO:\s*\n((?:[ \t]*\S.*\n){1,4})", app_text or "", re.I)
    if not m:
        return None
    return ", ".join(ln.strip() for ln in m.group(1).splitlines() if ln.strip())


def parse_flyer(label: str, url: str, flyer: str, apply_url: str | None, app_text: str = "") -> Listing:
    flat = re.sub(r"[ \t]+", " ", flyer)
    ami_pct = parse.ami(flat)
    rent = re.search(r"Monthly Rent:?\s*\$\s*([\d,]+(?:\.\d+)?)", flat, re.I)
    size = parse.size(label)
    if size == "unknown":
        sm = re.search(r"(STUDIO|\w+\s*-?\s*BEDROOM)\s+APARTMENT", flat, re.I)
        size = parse.size(sm.group(0)) if sm else "unknown"
    bands: list[Band] = []
    m = re.search(
        r"INCOME LIMITS?:?\s*\n?\s*(\d\s*(?:[-–—]|to)?\s*\d?\s*persons?|\d\s*people)\s*\n?\s*"
        r"(Minimum\s*\$[\d,]+.{0,6}?Maximum\s*\$[\d,]+|\$[\d,]+\s*[-–—]\s*\$[\d,]+)",
        flat,
        re.I | re.S,
    )
    if m:
        bands = parse.bands_from_row(m.group(1), m.group(2), ami_pct)
    unit = Unit(size=size, rent=parse.money(rent.group(1)) if rent else None, ami=ami_pct, bands=bands)
    if re.search(r"\b62\s*\+|\bsenior\b", flat, re.I):
        unit.set_aside = "senior"
    addr = label.split(":")[0].strip()
    mail = mail_address(app_text)
    phone = re.search(r"Phone:\s*([\d.\-() ]+(?:ext\.?\s*\d+)?)", flat, re.I)
    email = re.search(r"[\w.+-]+@[\w-]+\.\w+", flat)
    notes = []
    if apply_url:
        notes.append(f"application PDF {apply_url}")
    if mail:
        regular = "regular mail only" if re.search(r"REGULAR MAIL", app_text, re.I) else "mail"
        notes.append(f"{regular} to {mail}")
    if email or phone:
        notes.append("questions " + ", ".join(x.group(1 if x is phone else 0).strip() for x in (phone, email) if x))
    return Listing(
        source="st_nicks",
        ext_id=url.rstrip("/").split("/")[-1].removesuffix(".pdf"),
        title=label,
        url=url,
        address=addr,
        borough=parse.borough(addr, flat)
        or ("Bronx" if re.search(r"[-_ ]Bx[-_ .]", url) else None)
        or ("Brooklyn" if re.search(r"[-_ ]Bk[-_ .]", url) else None),
        status="open",  # the page itself is the signal: closed flyers are taken down
        units=[unit],
        senior_only=unit.set_aside == "senior",
        notes=("Apply: " + "; ".join(notes)) if notes else None,
    )


class StNicks(PageAdapter):
    name = "st_nicks"
    title = "St. Nicks Alliance"
    url = INDEX
    pages = [INDEX]
    min_listings = 1

    def fetch(self) -> Result:
        snaps = super().fetch().snapshots
        out, seen = [], set()
        for label, url in openings(fetch.html(INDEX)):
            if not url.lower().endswith(".pdf"):
                out.append(Listing(source=self.name, ext_id=url, title=label, url=url, borough=parse.borough(label)))
                continue
            flyer = pdf_cached(self.cache, url)
            seen.add(url)
            apply_url = next((u for u in flyer["links"] if u.lower().endswith(".pdf")), None)
            app_text = ""
            if apply_url:
                app_text = pdf_cached(self.cache, apply_url)["text"]
                seen.add(apply_url)
            out.append(parse_flyer(label, url, flyer["text"], apply_url, app_text))
        for u in [u for u in self.cache.get("pdf", {}) if u not in seen]:
            del self.cache["pdf"][u]
        return Result(listings=out, snapshots=snaps)
