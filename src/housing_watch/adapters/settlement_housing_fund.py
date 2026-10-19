"""Settlement Housing Fund: its own re-rental list (a Microsoft Form) plus Pronto-hosted buildings.

1. "Re-Rental Vacancies (2026)" is a Microsoft Form. Its public runtime JSON (no auth) lists each unit as
   a checkbox choice ("515 W 36th Street Unit 11J (130% AMI Studio)") under one question per AMI group.
   Each question also carries a PNG of the unit table (rent, household sizes, income limits). The PNG is
   read by finding the table's ruled grid in the pixels and OCR'ing every cell on its own (tesseract
   alone mis-reads whole rows of this table); results are cached by image URL + sha256.
2. "New Lotteries and Re-rentals" are bit.ly links to Pronto. Pronto's property JSON gives every unit,
   rent, AMI and per-household income range. A link that lands on a Pronto registration page (3333
   Broadway) has no public listing: the registration form's own config gives the application window
   and intro text, but Pronto publishes no sizes, rents or income limits for it, so it has no units.
"""

from __future__ import annotations

import base64
import hashlib
import json as _json
import os
import re
import struct
import subprocess
import tempfile
import uuid
import zlib
from collections import Counter

from .. import ami, fetch, parse
from ..models import Band, Listing, Result, Unit
from ._page import PageAdapter

URL = "https://www.settlementhousingfund.org/find-housing/"
SOURCE = "settlement_housing_fund"
FORM_API = (
    "https://forms.office.com/formapi/api/{tenant}/users/{user}/light/runtimeForms('{id}')"
    "?$expand=questions($expand=choices)"
)
PRONTO_API = "https://prontohousing.io/api/listings/properties/{id}/"
PRONTO_REG = "https://prontohousing.io/api/v2/self-register/{uuid}/config/"  # the registration page's own call
# bit.ly slugs whose redirect chain cannot be followed by a plain client (Waterside goes through Mimecast)
KNOWN_PRONTO = {"WatersidePlazaListings": 7409}
# Buildings SHF markets, for addresses printed without a borough (several street names exist in 2+ boroughs)
KNOWN_BOROUGH = {
    "509 third": "Manhattan",
    "515 36": "Manhattan",
    "572 eleventh": "Manhattan",
    "280 24": "Manhattan",
    "7 dey": "Manhattan",
    "30 kent": "Brooklyn",
    "146 pierrepont": "Brooklyn",
    "3333 broadway": "Manhattan",
}
_DIRS = {"w", "west", "e", "east", "n", "north", "s", "south"}


# ---------------------------------------------------------------- addresses


def addr_key(addr: str) -> str:
    """'515 W 36th Street' / '515 West 36" Street' -> '515 36'; '509 Third Avenue' -> '509 third'."""
    toks = re.findall(r"[a-z0-9]+", addr.lower())
    if not toks or not toks[0].isdigit():
        return " ".join(toks[:2])
    rest = [t for t in toks[1:] if t not in _DIRS]
    street = re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", rest[0]) if rest else ""
    return f"{toks[0]} {street}"


def borough_of(addr: str) -> str | None:
    return parse.borough(addr) or KNOWN_BOROUGH.get(addr_key(addr))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ---------------------------------------------------------------- PNG table OCR (no image libraries)


def png_gray(data: bytes) -> tuple[int, int, list[bytes]]:
    """Decode an 8-bit non-interlaced PNG to grayscale rows."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, pal = 8, b"", b""
    w = h = depth = ctype = inter = 0
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", body)
        elif typ == b"IDAT":
            idat += body
        elif typ == b"PLTE":
            pal = body
    if depth != 8 or inter:
        raise ValueError(f"unsupported PNG (depth {depth}, interlace {inter})")
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * ch
    prev = bytearray(stride)
    rows, i = [], 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1 : i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - ch] if x >= ch else 0
            b = prev[x]
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif f == 4:
                c = prev[x - ch] if x >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        prev = line
        if ctype == 3:
            rows.append(bytes((pal[3 * v] * 299 + pal[3 * v + 1] * 587 + pal[3 * v + 2] * 114) // 1000 for v in line))
        elif ch >= 3:
            rows.append(
                bytes((line[k] * 299 + line[k + 1] * 587 + line[k + 2] * 114) // 1000 for k in range(0, stride, ch))
            )
        else:
            rows.append(bytes(line[0::ch]))
    return w, h, rows


def _runs(flags: list[bool], off: int = 0) -> list[tuple[int, int]]:
    out, s = [], None
    for i, f in enumerate([*flags, False]):
        if f and s is None:
            s = i
        elif not f and s is not None:
            out.append((s + off, i + off))
            s = None
    return out


def _gaps(lines: list[tuple[int, int]], lo: int, hi: int, minlen: int = 8) -> list[tuple[int, int]]:
    edges = [lo] + [x for r in lines for x in r] + [hi]
    return [(edges[i], edges[i + 1]) for i in range(0, len(edges), 2) if edges[i + 1] - edges[i] >= minlen]


def _ocr_cell(rows: list[bytes], x0: int, x1: int, y0: int, y1: int, d: str, scale: int = 3) -> str:
    pad = 6
    w = x1 - x0 + 2 * pad
    blank = bytes([255] * w)
    seg = [blank] * pad + [bytes([255] * pad) + r[x0:x1] + bytes([255] * pad) for r in rows[y0:y1]] + [blank] * pad
    buf = bytearray()
    for r in seg:
        buf += bytes(v for v in r for _ in range(scale)) * scale
    p = os.path.join(d, "c.pgm")
    with open(p, "wb") as f:
        f.write(b"P5\n%d %d\n255\n" % (w * scale, len(seg) * scale) + bytes(buf))
    out = subprocess.run(["tesseract", p, "-", "--psm", "6"], capture_output=True, text=True, timeout=60).stdout
    return re.sub(r"[ \t]+", " ", out.replace("|", " ")).strip()


def ocr_table(png: bytes) -> list[list[list[str]]]:
    """A ruled table image -> blocks (one per bordered row group) -> columns -> sub-rows of cell text."""
    w, h, rows = png_gray(png)

    def dark(v: int) -> bool:
        return v < 160

    wide = _runs([sum(map(dark, rows[y])) / w > 0.5 for y in range(h)])
    if len(wide) < 2:
        return []
    top, bottom = wide[0][1], wide[-1][0]
    vl = _runs([sum(dark(rows[y][x]) for y in range(top, bottom)) / max(1, bottom - top) > 0.9 for x in range(w)])
    if len(vl) < 3:
        return []
    left, right = vl[0][0], vl[-1][1]
    cols = _gaps(vl, left, right)
    # a block border runs across the whole table; household sub-row rules only cross the right columns
    borders = [
        r for r in _runs([sum(map(dark, rows[y][left:right])) / (right - left) > 0.9 for y in range(h)]) if r[0] >= top
    ]
    blocks = _gaps(borders, top, bottom, 12)
    out = []
    with tempfile.TemporaryDirectory() as d:
        for a, b in blocks:
            cells = []
            for x0, x1 in cols:
                sub = _runs([sum(map(dark, rows[y][x0:x1])) / (x1 - x0) > 0.8 for y in range(a, b)], a)
                parts = _gaps(sub, a, b) or [(a, b)]
                cells.append([_ocr_cell(rows, x0, x1, p, q, d) for p, q in parts])
            out.append(cells)
    return out


# ---------------------------------------------------------------- reading OCR'd blocks


def _income(cell: str) -> tuple[int | None, int | None]:
    vals = [int(n.replace(",", "")) for n in re.findall(r"\d{1,3},\d{3}", cell)]
    if len(vals) >= 2 and vals[0] > vals[1] and vals[0] >= 100_000:
        vals[0] = int(str(vals[0])[1:])  # a "$" read as a digit: "580,229 - $176,410"
    return (vals[0] if vals else None), (vals[1] if len(vals) > 1 else None)


def _cols(b: list[list[str]]) -> dict[str, int | None] | None:
    """Find the columns of one block by content (the header is white-on-navy and not OCR'd)."""

    def first(pred, after: int = -1) -> int | None:
        return next((i for i, c in enumerate(b) if i > after and pred(" ".join(c))), None)

    hh = first(lambda t: re.search(r"person|people", t, re.I))
    inc = first(lambda t: len(re.findall(r"\d{1,3},\d{3}", t)) >= 2)
    proj = first(lambda t: re.match(r"\d+\s+[A-Za-z]", t) and not re.search(r"%|\bunit\b", t, re.I))
    if hh is None or inc is None or proj is None:
        return None
    rent = next(
        (i for i in range(hh - 1, proj, -1) if re.search(r"\d", " ".join(b[i])) and "%" not in " ".join(b[i])), None
    )
    return {"project": proj, "units": proj + 1, "rent": rent, "household": hh, "income": inc}


def read_blocks(blocks: list[list[list[str]]], ami_pct: int | None) -> list[dict]:
    """OCR'd blocks -> [{key, rent, bands, units}]."""
    out = []
    for b in blocks:
        idx = _cols(b)
        if idx is None:
            continue
        proj = " ".join(b[idx["project"]])
        rent_txt = " ".join(b[idx["rent"]]) if idx["rent"] is not None else ""
        rent = parse.money(re.sub(r"^[^\d]*", "", rent_txt) or None)
        hhs, incs = b[idx["household"]], b[idx["income"]]
        if len(hhs) != len(incs):
            continue
        sizes: list[int | None] = []
        for c in hhs:
            m = re.search(r"([1-9])\s*\??\s*(person|people)", c, re.I)
            sizes.append(int(m.group(1)) if m else None)
        for i, s in enumerate(sizes):  # rows are consecutive household sizes; fill an unread digit
            if s is None:
                if i and sizes[i - 1]:
                    sizes[i] = sizes[i - 1] + 1  # type: ignore[operator]
                elif i + 1 < len(sizes) and sizes[i + 1]:
                    sizes[i] = sizes[i + 1] - 1  # type: ignore[operator]
        ranges = [_income(c) for c in incs]
        mins = Counter(lo for lo, _ in ranges if lo)
        common = mins.most_common(1)[0][0] if mins else None
        bands = []
        for s, (lo, hi) in zip(sizes, ranges, strict=True):
            if not s:
                continue
            cap = ami.cap(ami_pct, s)
            if cap and hi not in {ami.cap(ami_pct, s, y) for y in ami.CHARTS}:
                hi = cap  # these tables print HUD's exact AMI caps (some year's); a mismatch is an OCR misread
            if mins and mins[common] >= 2 and lo != common:
                lo = common  # every row of a block shares one minimum
            bands.append(Band(s, lo, hi))
        out.append({"key": addr_key(proj), "project": proj, "rent": rent, "bands": bands, "units": b[idx["units"]]})
    return out


# ---------------------------------------------------------------- the form


def form_api_url(form_url: str) -> str:
    fid = re.search(r"[?&]id=([^&#]+)", form_url).group(1)  # type: ignore[union-attr]
    raw = base64.urlsafe_b64decode(fid + "=" * (-len(fid) % 4))
    tenant, user = uuid.UUID(bytes_le=raw[:16]), uuid.UUID(bytes_le=raw[16:32])
    return FORM_API.format(tenant=tenant, user=user, id=fid)


_CHOICE = re.compile(
    r"^(?P<addr>.+?)\s+Units?\s+(?P<units>[0-9A-Z][0-9A-Z\s&,/-]*?)\s*\((?P<inner>[^)]*)\)\s*(?:-\s*(?P<extra>.*))?$",
    re.I,
)


def _set_aside(text: str) -> str | None:
    t = text.lower()
    if re.search(r"mobility", t):
        return "mobility"
    if re.search(r"hearing|visual|h/v", t):
        return "hearing/visual"
    if re.search(r"\bage\b|62 or over|senior|62\+", t):
        return "senior"
    if re.search(r"\bmi\b|mental", t):
        return "MI"
    return None


def parse_form(form: dict, form_url: str, tables: dict[str, list[list[list[str]]]]) -> list[Listing]:
    """Form JSON (+ OCR'd tables keyed by image URL) -> one Listing per building."""
    active = form.get("status") == "Active"
    buildings: dict[str, Listing] = {}
    for q in sorted(form.get("questions", []), key=lambda q: q.get("order", 0)):
        if q.get("type") != "Question.Choice":
            continue
        choices = [c.get("Description", "") for c in _json.loads(q.get("questionInfo") or "{}").get("Choices", [])]
        img = ((q.get("image") or {}).get("resourceUrl")) or ""
        q_ami = parse.ami(q.get("title"))
        rows = read_blocks(tables.get(img, []), q_ami)
        for ch in choices:
            m = _CHOICE.match(re.sub(r"\s+", " ", ch.replace("\xa0", " ")).strip())
            if not m:
                continue
            addr, inner, extra = m.group("addr").strip(), m.group("inner"), m.group("extra") or ""
            a = parse.ami(inner) or q_ami
            size = parse.size(inner)
            nos = [u.strip().upper() for u in re.split(r"\s*(?:&|,|/|\band\b)\s*", m.group("units")) if u.strip()]
            key = addr_key(addr)
            row = next(
                (r for r in rows if r["key"] == key and any(n in " ".join(r["units"]).upper() for n in nos)), None
            )
            row = row or next((r for r in rows if r["key"] == key), None)
            lst = buildings.get(key)
            if lst is None:
                lst = buildings[key] = Listing(
                    source=SOURCE,
                    ext_id=f"rerental-{_slug(key)}",
                    title=addr,
                    url=form_url,
                    address=addr,
                    borough=borough_of(addr),
                    status="open" if active else "closed",
                    notes=(
                        f"Apply: tick the unit(s) on SHF's re-rental Microsoft Form {form_url} "
                        "(name, phone, email). Questions: marketinginfo@shfinc.org"
                    ),
                )
            for no in nos:
                lst.units.append(
                    Unit(
                        size=size,
                        rent=row["rent"] if row else None,
                        ami=a,
                        unit_no=no,
                        bands=list(row["bands"]) if row else [],
                        set_aside=_set_aside(extra),
                        available=None if active else False,
                    )
                )
    for lst in buildings.values():
        lst.senior_only = bool(lst.units) and all(u.set_aside == "senior" for u in lst.units)
    return list(buildings.values())


# ---------------------------------------------------------------- Pronto

_PRONTO_SIZE = {0: "studio", 1: "1br", 2: "2br", 3: "3br", 4: "4br"}


def parse_pronto(d: dict, page_url: str) -> Listing:
    p = d["property"]
    offers = p.get("available_offerings") or []
    units = []
    for u in d.get("units", []):
        a = u.get("ami_percentage")
        off = next(
            (
                o
                for o in offers
                if o.get("bedrooms") == u.get("bedrooms")
                and round((o.get("ami_pct") or 0) * 100) == a
                and (o.get("rent_min") or 0) <= (u.get("rent") or 0) <= (o.get("rent_max") or 1e9)
            ),
            None,
        )
        bands = [
            Band(r["household_size"], parse.whole(r.get("min_income")), parse.whole(r.get("max_income")))
            for r in (off or {}).get("income_ranges", [])
        ]
        feats = " ".join(map(str, u.get("accessibility_features") or []))
        units.append(
            Unit(
                size=_PRONTO_SIZE.get(u.get("bedrooms"), "unknown"),
                rent=u.get("rent"),
                ami=a,
                unit_no=u.get("name"),
                bands=bands,
                set_aside=_set_aside(feats),
                available=False if u.get("is_available") is False else None,
            )
        )
    waitlist = p.get("is_waitlist") or p.get("waitlist_only")
    how = p.get("application_link") or page_url
    contact = p.get("contact_email") or "marketinginfo@shfinc.org"
    outside = "" if parse.borough(p.get("address")) else " Outside NYC."
    return Listing(
        source=SOURCE,
        ext_id=f"pronto-{p['id']}",
        title=p.get("name") or p.get("address"),
        url=page_url,
        address=p.get("address"),
        borough=parse.borough(p.get("address")),
        status="waitlist" if waitlist else "open",
        kind="waitlist" if waitlist else "re-rental",
        units=units,
        deadline=p.get("application_deadline"),
        notes=f"Apply online via Pronto (account required): {how}. Contact {contact}.{outside}",
    )


# ---------------------------------------------------------------- index page


def index_links(html: str) -> tuple[str | None, list[tuple[str, str, str]]]:
    """-> (re-rental form url, [(name, address, bit.ly url)]) from the Find Housing page."""
    s = parse.soup(html)
    form = next(
        (a["href"] for a in s.find_all("a", href=True) if re.search(r"forms\.(office|cloud\.microsoft)", a["href"])),
        None,
    )
    out = []
    for a in s.find_all("a", href=re.compile(r"bit\.ly/")):
        par = a.find_parent(["p", "li", "div"])
        line = re.sub(r"\s+", " ", par.get_text(" ") if par else "")
        line = line[: line.find(a["href"])] if a["href"] in line else line
        name, _, addr = line.partition(":")
        addr = re.sub(r"^\s*[^:]*:\s*", "", addr) if addr.count(":") else addr
        out.append((name.strip(), addr.strip(" |"), a["href"]))
    seen, uniq = set(), []
    for t in out:
        if t[2] not in seen:
            seen.add(t[2])
            uniq.append(t)
    return form, uniq


def registration_listing(name: str, addr: str, link: str, target: str, cfg: dict | None = None) -> Listing:
    """A bit.ly link to a Pronto registration form; `cfg` is the form's config JSON when it loaded."""
    cfg = cfg or {}
    intro = re.sub(r"\s+", " ", cfg.get("form_text") or "").strip()
    window = ""
    if cfg.get("link_closes_date"):
        window = f" Applications {cfg.get('link_opens_date') or '?'} to {cfg['link_closes_date']}."
    return Listing(
        source=SOURCE,
        ext_id=_slug(name or addr),
        title=name or addr,
        url=link,
        address=addr or None,
        borough=borough_of(addr or name),
        status="open",
        kind="waitlist",
        deadline=cfg.get("link_closes_date") or None,
        notes=f"No public unit list: neither SHF nor Pronto publishes sizes, rents or income limits. Pronto "
        f"registration only (account required): {target}.{window}"
        + (f" Form: {intro}" if intro else "")
        + f" Questions: {cfg.get('property_email') or 'marketinginfo@shfinc.org'}",
    )


class SettlementHousingFund(PageAdapter):
    name = SOURCE
    title = "Settlement Housing Fund"
    url = URL
    pages = [URL]
    every_hours = 4
    min_listings = 1

    def _table(self, img: str) -> list[list[list[str]]]:
        data = fetch.get(img).content
        h = hashlib.sha256(data).hexdigest()
        hit = self.cache.setdefault("ocr", {}).get(img)
        if hit and hit.get("sha") == h:
            return hit["blocks"]
        blocks = ocr_table(data)
        self.cache["ocr"][img] = {"sha": h, "blocks": blocks}
        return blocks

    def fetch(self) -> Result:
        snaps = super().fetch().snapshots
        form_url, links = index_links(fetch.html(URL))
        listings: list[Listing] = []
        imgs: set[str] = set()
        if form_url:
            form = fetch.json(form_api_url(form_url))
            tables = {}
            for q in form.get("questions", []):
                img = (q.get("image") or {}).get("resourceUrl")
                if q.get("type") == "Question.Choice" and img:
                    tables[img] = self._table(img)
                    imgs.add(img)
            listings += parse_form(form, form_url, tables)
        for img in [k for k in self.cache.get("ocr", {}) if k not in imgs]:
            del self.cache["ocr"][img]
        for name, addr, link in links:
            pid = KNOWN_PRONTO.get(link.rstrip("/").split("/")[-1])
            target = link
            if pid is None:
                r = fetch.get(link)
                chain = [str(x.url) for x in r.history] + [str(r.url)]
                m = next((re.search(r"/listings/property/(\d+)", u) for u in chain if "/listings/property/" in u), None)
                pid = int(m.group(1)) if m else None
                target = next((u for u in chain if "registration" in u), chain[-1])
            if pid:
                listings.append(parse_pronto(fetch.json(PRONTO_API.format(id=pid)), link))
            else:
                m = re.search(r"/registration/([0-9a-f-]{36})", target)
                try:
                    cfg = fetch.json(PRONTO_REG.format(uuid=m.group(1))) if m else None
                except fetch.FetchError:
                    cfg = None  # the listing still stands on the link alone
                listings.append(registration_listing(name, addr, link, target, cfg))
        return Result(listings=listings, snapshots=snaps)
