"""K&G Upright: one GoDaddy page (/current-vacancies). Plain GET returns the full server-rendered markup.

Each building's rent/income table is a hand-written HTML widget inside an <iframe srcdoc>, invisible to
the page text, so the widgets are parsed (and folded into the snapshot) explicitly. Their layouts all
differ (rowspan tables, card grids, "Minimum Income Required" boxes), so they are read as one token
stream: size -> rent -> household -> income. The main building's application PDF names the email it
goes to; that PDF is read once per content hash. When a widget yields no units and an image appears in
its place, the image is OCR'd (cached by URL + sha256) and fed to the same token reader.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Band, Listing, Result, Snapshot, Unit
from ._page import COMMON_IGNORE, DOC_RE, PageAdapter

URL = "https://kgupright.com/current-vacancies"

_TOK = re.compile(
    r"(?P<ami>\d{2,3})\s*%\s*(?:area\s*median|ami)"
    r"|(?P<hh>\b\d\s*(?:-|–|to)?\s*\d?\s*(?:people|persons?)\b)"
    r"|(?P<range>\$\s?\d[\d,]*(?:\.\d\d)?\s*[-–—]\s*\$\s?\d[\d,]*(?:\.\d\d)?)"
    r"|(?P<money>\$\s?\d[\d,]*(?:\.\d\d)?)"
    r"|(?P<size>\bstudio\b|\b(?:\d|one|two|three)\s*-?\s*(?:bedroom|bdrm|br|bed)s?\b)"
    r"|(?P<minimum>minimum\s+income\s+required)",
    re.I,
)
_SET_ASIDE = [
    (r"visual|hearing", "hearing/visual"),
    (r"mobility|wheelchair", "mobility"),
    (r"senior|62\s*\+|62 and older", "senior"),
]


def _set_aside(line: str) -> str | None:
    hits = [name for pat, name in _SET_ASIDE if re.search(pat, line, re.I)]
    return "/".join(hits) or None


# HUD 100% AMI for NYC, households 1-4: FY2025 and parse's FY2026 (widgets mix both years).
_AMI100 = [{1: 113_400, 2: 129_600, 3: 145_800, 4: 162_000}, {h: parse.AMI100[h] for h in (1, 2, 3, 4)}]


def infer_ami(bands: list[Band]) -> int | None:
    """AMI % from a per-household cap when none is printed: only an exact HUD multiple of 5 counts."""
    for b in bands:
        for table in _AMI100:
            if b.income_max and b.household in table:
                pct = b.income_max / table[b.household] * 100
                if abs(pct - round(pct / 5) * 5) < 0.05:
                    return int(round(pct / 5) * 5)
    return None


def units_from_lines(lines: list[str]) -> list[Unit]:
    """Token-stream reader for one building's table text (HTML text lines or OCR lines)."""
    units: list[Unit] = []
    cur: Unit | None = None
    ami: int | None = None
    default_size, default_min = "unknown", None
    want_min = False
    hh: tuple[str, list[int]] | None = None  # household text, money values seen since

    def close_hh():
        nonlocal hh
        if hh and cur is not None and hh[1]:
            vals = hh[1]
            lo, hi = (vals[0], vals[1]) if len(vals) >= 2 else (default_min, vals[0])
            # a placeholder minimum keeps money_range from reading the max as the min; lo is restored after
            bands = [Band(b.household, lo, b.income_max) for b in parse.bands_from_row(hh[0], f"$1,000 - ${hi:,}", ami)]
            cur.bands += bands
        hh = None

    for i, line in enumerate(lines):
        toks = list(_TOK.finditer(line))
        heading = bool(re.search(r"\bavailable\b", line, re.I)) and not re.search(r"units available", line, re.I)
        if heading:
            sizes = {parse.size(m.group("size")) for m in toks if m.group("size")}
            if len(sizes) == 1:
                default_size = sizes.pop()
            continue
        prev = lines[i - 1] if i else ""
        for m in toks:
            kind = m.lastgroup
            val = m.group(kind)
            if kind == "ami":
                close_hh()
                ami = int(val)
                if cur is not None and cur.ami is None and not cur.bands:
                    cur.ami = ami
            elif kind == "size":
                close_hh()
                cur = Unit(size=parse.size(re.sub(r"bdrm", "bedroom", val, flags=re.I)), ami=ami)
                cur.set_aside = _set_aside(line)
                units.append(cur)
            elif kind == "minimum":
                want_min = True
            elif kind == "hh":
                close_hh()
                if cur is None and units == [] and default_size != "unknown":
                    cur = Unit(size=default_size, ami=ami)
                    units.append(cur)
                hh = (val, [])
            elif kind == "range":
                lo, hi = (parse.whole(x) for x in re.split(r"[-–—]", val))
                if hh is not None:
                    hh[1].extend([lo, hi])
                    close_hh()
            else:  # money
                v = parse.money(val)
                if want_min:
                    default_min, want_min = int(v), False
                elif hh is not None:
                    hh[1].append(int(v))
                    if len(hh[1]) == 2:
                        close_hh()
                elif v < 10_000 and (cur is None or (cur.rent is None and not cur.bands)):
                    if cur is None:
                        if not re.search(r"rent", line + " " + prev, re.I):
                            continue
                        cur = Unit(size=default_size, ami=ami)
                        units.append(cur)
                    cur.rent = v
    close_hh()
    for u in units:
        if u.ami is None:
            u.ami = infer_ami(u.bands)
    return units


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _address(lines: list[str]) -> str | None:
    for i, ln in enumerate(lines):
        if re.match(r"^\d[\d-]*\s+\S", ln) and re.search(r"\b(st|street|ave|avenue|rd|road|blvd|place|pl)\b", ln, re.I):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if re.search(r"\bNY\b|New York", ln):
                return ln
            return f"{ln}, {nxt}" if re.search(r"\bNY\s+\d{5}\b", nxt) else ln
    return None


def widgets(html: str) -> list[str]:
    """Visible text of each iframe srcdoc widget, in page order."""
    s = parse.soup(html)
    return [parse.text(f["srcdoc"]) for f in s.find_all("iframe") if f.get("srcdoc")]


def parse_page(html: str, ocr_for=None, app_email: str | None = None) -> list[Listing]:
    """ocr_for(image_url) -> OCR text, used only when a widget has no readable table."""
    s = parse.soup(html)
    main = _lines(parse.text(s))
    app = next(
        (urljoin(URL, a["href"]) for a in s.find_all("a", href=True) if "application" in a["href"].lower()), None
    )
    docs = sorted({urljoin(URL, a["href"]) for a in s.find_all("a", href=True) if ".pdf" in a["href"].lower()})
    out = []
    for w in widgets(html):
        lines = _lines(w)
        if not lines:
            continue
        hdr = next((ln for ln in lines if ln.lower().startswith("additional opportunities")), None)
        name = lines[lines.index(hdr) + 1] if hdr and lines.index(hdr) + 1 < len(lines) else lines[0]
        name = re.sub(r"\s+", " ", name).strip()
        primary = hdr is None  # the page's own building; its address and application sit in the main text
        addr = _address(lines)
        if primary and not addr:
            k = next((i for i, ln in enumerate(main) if ln.lower() == name.lower()), None)
            addr = _address(main[k + 1 : k + 3]) if k is not None else None
        units = units_from_lines(lines)
        if not units and ocr_for:
            for img in re.findall(r"https?://\S+\.(?:png|jpe?g)", w):
                units = units_from_lines(_lines(ocr_for(img)))
                if units:
                    break
        if primary:
            notes = f"Email the application to {app_email or 'the address printed on it'}"
            notes += f"; application: {app}" if app else ""
            notes += "; " + ", ".join(d for d in docs if d != app) if len(docs) > 1 else ""
        elif re.search(r"coming soon", w, re.I):
            notes = 'Application not published yet ("Application & Eligibility Documents Coming Soon"); watch ' + URL
        else:
            notes = f"No application posted; contact K&G Upright via https://kgupright.com/contact-us ({URL})"
        title = name.title() if name.isupper() else name
        out.append(
            Listing(
                source="kg_upright",
                ext_id=re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
                title=title,
                url=URL,
                address=addr,
                borough=parse.borough(addr, hdr, " ".join(lines[:6])),
                status="open",
                kind="re-rental",
                units=units,
                senior_only=bool(re.search(r"62 years|62\s*\+|senior", w, re.I)),
                notes=notes,
            )
        )
    return out


_EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"


def app_email(pdf_text: str) -> str | None:
    """'Please return this application to VerdantFG@kgupright.com' -> the address."""
    m = re.search(r"return this application to\s+(" + _EMAIL + ")", pdf_text, re.I) or re.search(_EMAIL, pdf_text)
    return (m.group(1) if m.groups() else m.group(0)).rstrip(".") if m else None


def snapshot(html: str, name: str) -> Snapshot:
    """Like PageAdapter's, but with the iframe widgets' text included (they are the actual listings)."""
    s = parse.soup(html)
    links = sorted(
        {
            urljoin(URL, a["href"].strip()).split("#")[0]
            for a in s.find_all("a", href=True)
            if DOC_RE.search(a["href"]) and "godaddy.com" not in a["href"]
        }
    )
    pats = [re.compile(p, re.I) for p in COMMON_IGNORE]
    txt = "\n".join([parse.text(s), *widgets(html)])
    txt = "\n".join(ln for ln in txt.splitlines() if not any(p.fullmatch(ln.strip()) for p in pats))
    if len(txt) < 80 or "Current Vacancies" not in txt:
        raise RuntimeError(f"{URL}: only {len(txt)} chars of text (blocked?)")
    return Snapshot(source=name, ext_id=URL, url=URL, text=txt, links=links)


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180).stdout


def _cached(cache: dict, kind: str, url: str, seen: set[str], convert) -> str:
    data = fetch.get(url).content
    h = hashlib.sha256(data).hexdigest()
    store = cache.setdefault(kind, {})
    seen.add(f"{kind}:{url}")
    hit = store.get(url)
    if hit and hit.get("sha") == h:
        return hit["text"]
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f" + (os.path.splitext(url.split("?")[0])[1] or ".bin"))
        with open(p, "wb") as f:
            f.write(data)
        text = convert(p)
    store[url] = {"sha": h, "text": text}
    return text


class KgUpright(PageAdapter):
    name = "kg_upright"
    title = "K&G Upright"
    url = URL
    pages = [URL]
    every_hours = 12
    min_listings = 1

    def fetch(self) -> Result:
        html = fetch.html(URL)
        snap = snapshot(html, self.name)
        seen: set[str] = set()
        app = next((u for u in snap.links if "application" in u.lower() and ".pdf" in u.lower()), None)
        email = None
        if app:
            txt = _cached(self.cache, "pdf", app, seen, lambda p: _run(["pdftotext", "-layout", "-l", "2", p, "-"]))
            email = app_email(txt)

        def ocr(u):
            return _cached(self.cache, "ocr", u, seen, lambda p: _run(["tesseract", p, "-", "--psm", "6"]))

        listings = parse_page(html, ocr, email)
        for kind in ("pdf", "ocr"):
            store = self.cache.setdefault(kind, {})
            for u in [u for u in store if f"{kind}:{u}" not in seen]:
                del store[u]
        return Result(listings=listings, snapshots=[snap])
