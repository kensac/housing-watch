"""Tax Solute (Affordable Living NYC): one Google Sites page of listing text blocks, first come first served.

Google Sites splits text into many spans, so the page is read as a stream of leaf blocks (each block's
spans joined with no separator) plus Drive links, in document order. A card starts at a "<City>, NY"
line (the address is the block before it); an optional label just above ("Unit on Hold", "Initial
Lease-Up", "Available!") sets the status. Income rows are printed per household size
("1 PERSON $min -$max"). Applying is by standard mail only, so `notes` carries the mail-to address.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

from .. import fetch, parse
from ..models import Listing, Result, Snapshot, Unit
from . import Adapter

URL = "https://sites.google.com/affordablelivingnyc.com/hpd/home"
MAIL_TO = "C/O Tax Solute Consulting, 670 Flushing Ave #316, Brooklyn NY 11206"  # fallback
_BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li"}
_CITY = re.compile(r"^([A-Za-z .]+?)\s*,\s*NY\.?$")
_LABEL = re.compile(r"on hold|lease-?up|available|rented|off the market|closed|leased", re.I)
_ROW = re.compile(r"^(\d+(?:\s*-\s*\d+)?)\s*(?:PERSON|PEOPLE|PERSONS)\b\s*(.*)$", re.I)
_TIER = re.compile(r"^INCOME GUIDELINES\s*-\s*([^,]+?)(?:,\s*\$\s*([\d,.]+))?\s*$", re.I)


def _stream(html: str) -> list[tuple[str, str]]:
    """('t', text) per leaf text block and ('a', href) per Drive application link, in document order."""
    s = parse.soup(html)
    for t in s(["script", "style", "noscript", "template"]):
        t.decompose()
    out: list[tuple[str, str]] = []
    for el in s.find_all(True):
        if el.name == "a" and el.get("href"):
            href = el["href"]
            if "google.com/url" in href:
                href = unquote(parse_qs(urlparse(href).query).get("q", [href])[0])
            drive = "drive.google.com" in href or "docs.google.com" in href
            if drive and re.search(r"applic", el.get_text(" "), re.I):  # not photos / floor plans / video
                out.append(("a", href))
        elif el.name in _BLOCKS and not el.find(list(_BLOCKS)):
            t = re.sub(r"\s+", " ", el.get_text("")).strip()
            if t:
                out.append(("t", t))
    return out


def _download(href: str) -> str:
    m = re.search(r"/d/([\w-]+)", href) or re.search(r"[?&]id=([\w-]+)", href)
    return f"https://drive.google.com/uc?export=download&id={m.group(1)}" if m else href


def _slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def _infer_ami(bands) -> int | None:
    """These ads print no AMI %; the cap of the first household row gives it (154,440 for 1 = 130%)."""
    for b in bands:
        if b.income_max and b.household in parse.AMI100:
            pct = b.income_max / parse.AMI100[b.household] * 100
            if abs(pct - round(pct / 5) * 5) < 0.3:
                return int(round(pct / 5) * 5)
            return None
    return None


def _fix_money(t: str) -> str:
    return re.sub(r"\b(\d{3})\.(\d{3})\.(\d{2})\b", r"\1,\2.\3", t)  # "$154.440.00" typo


def _units(body: list[str]) -> list[Unit]:
    """'INCOME GUIDELINES - 1 BED, $3,595' starts a tier; else one unit from 'Unit Size:' + 'Rent:'."""
    units: list[Unit] = []
    flat_size = next((b.split(":", 1)[1].strip() for b in body if b.lower().startswith("unit size")), "")
    flat_rent = next((b.split(":", 1)[1] for b in body if re.match(r"rent\s*:", b, re.I)), None)
    for b in body:
        m = _TIER.match(b)
        if m and m.group(2):
            units.append(Unit(size=parse.size(m.group(1)), rent=parse.money(m.group(2))))
        elif b.upper().startswith("INCOME GUIDELINES") and not units:
            hdr = parse.size(m.group(1)) if m else "unknown"
            size = hdr if hdr != "unknown" else parse.size(flat_size)
            units.append(Unit(size=size, rent=parse.money(flat_rent.replace("$", "")) if flat_rent else None))
        elif (r := _ROW.match(b)) and units:
            inc = re.split(r"(?<=\d)(?=[A-Za-z])", _fix_money(r.group(2)))[0]  # "$198,510.00Note: ..."
            units[-1].bands += parse.bands_from_row(r.group(1), inc, None)
    for u in units:
        u.ami = _infer_ami(u.bands)
    return units


def parse_page(html: str, url: str = URL) -> tuple[list[Listing], str]:
    items = _stream(html)
    texts = [(i, v) for i, (k, v) in enumerate(items) if k == "t"]
    flat = "\n".join(v for _, v in texts)
    mail = MAIL_TO
    m = re.search(r"Mail the Completed Application To:\s*(.+?)\s+(\d+ [^\n]+?\b\d{5})", flat)
    if m:
        mail = f"C/O {m.group(1).strip()}, {m.group(2).strip()}"
    # card starts: index (into items) of the address block, which sits right before "<City>, NY"
    starts = []
    for n, (_i, v) in enumerate(texts):
        if n and _CITY.match(v) and not texts[n - 1][1].lower().startswith(("call", "mail")):
            starts.append((n - 1, n))
    stop = next((i for i, v in texts if v.startswith("Submit Your Application")), len(items))
    listings: list[Listing] = []
    seen: dict[str, int] = {}
    for k, (an, cn) in enumerate(starts):
        a_idx = texts[an][0]
        end = texts[starts[k + 1][0]][0] if k + 1 < len(starts) else stop
        # labels sit just above the address
        labels = []
        j = an - 1
        while j >= 0 and len(texts[j][1]) < 40 and _LABEL.search(texts[j][1]) and not _ROW.match(texts[j][1]):
            labels.insert(0, texts[j][1])
            j -= 1
        if k + 1 < len(starts):  # the next card's labels are not part of this card's body
            nxt = starts[k + 1][0] - 1
            while nxt > cn and len(texts[nxt][1]) < 40 and _LABEL.search(texts[nxt][1]):
                end = min(end, texts[nxt][0])
                nxt -= 1
        region = items[a_idx:end]
        body = [v for kk, v in region if kk == "t"][2:]
        links = sorted({_download(v) for kk, v in region if kk == "a"})
        address = texts[an][1].rstrip(" .")
        city = re.sub(r"\s+,", ",", texts[cn][1])
        lab = " ".join(labels).lower()
        if re.search(r"rented|off the market|closed|leased", lab):
            status = "closed"
        elif "on hold" in lab:
            status = "waitlist"
        else:
            status = "open"
        units = _units(body)
        if status == "closed":
            for u in units:
                u.available = False
        size_txt = next((b.split(":", 1)[1].strip() for b in body if b.lower().startswith("unit size")), "")
        ext = _slug(f"{address} {size_txt}")
        seen[ext] = seen.get(ext, 0) + 1
        if seen[ext] > 1:
            ext = f"{ext}-{seen[ext]}"
        note = [f"Apply by standard mail only: '<Building> Apartments, {mail}' (building name is on page 1"]
        note[0] += " of the application PDF)."
        if "lease-up" in lab:
            note.append("Initial lease-up.")
        if status == "waitlist":
            note.append("Unit on hold: applications received and under review.")
        if links:
            note.append("Application: " + " ".join(links))
        listings.append(
            Listing(
                source="tax_solute",
                ext_id=ext,
                title=address,
                url=url,
                address=f"{address}, {city}",
                borough=parse.borough(city) or parse.borough(address),
                status=status,
                units=units,
                senior_only=bool(re.search(r"\bsenior\b|62\+", " ".join(body), re.I)),
                notes=" ".join(note),
            )
        )
    return listings, flat


class TaxSolute(Adapter):
    name = "tax_solute"
    title = "Tax Solute (Affordable Living NYC)"
    url = URL
    every_hours = 4  # first come, first served
    min_listings = 1

    def fetch(self) -> Result:
        html = fetch.html(URL)
        listings, flat = parse_page(html)
        if listings:
            return Result(listings=listings)
        if "Available Affordable Housing Units" not in flat or len(flat) < 300:
            raise RuntimeError(f"{URL}: page did not load as expected ({len(flat)} chars)")
        # a real page with no cards right now: watch the text so new postings still alert
        return Result(snapshots=[Snapshot(source=self.name, ext_id=URL, url=URL, text=flat)])
