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
import json as _json
import re
import uuid
from collections import Counter

from ... import ami, fetch, parse
from ...models import Band, Listing, Result, Unit
from .. import by_content, prune
from .._page import PageAdapter
from .table_image import ocr_table

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
    units = first(lambda t: re.match(r"\s*unit\b", t, re.I))  # the project is the column before ("The Biltmore")
    if hh is None or inc is None or not units:
        return None
    rent = next(
        (i for i in range(hh - 1, units, -1) if re.search(r"\d", " ".join(b[i])) and "%" not in " ".join(b[i])), None
    )
    return {"project": units - 1, "units": units, "rent": rent, "household": hh, "income": inc}


def read_blocks(blocks: list[list[list[str]]], ami_pct: int | None) -> list[dict]:
    """OCR'd blocks -> [{key, rent, bands, units}]."""
    out = []
    for b in blocks:
        idx = _cols(b)
        if idx is None:
            continue
        proj = " ".join(b[idx["project"]])
        rent_txt = " ".join(b[idx["rent"]]) if idx["rent"] is not None else ""
        rent_txt = re.sub(r"^[^\d]*", "", rent_txt)
        rent = parse.money(rent_txt or None)
        if rent and rent >= 10_000:
            rent = parse.money(rent_txt[1:])  # a "$" read as a digit: "53,857"
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
                    ext_id=f"rerental-{parse.slug(key)}",
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
    out, seen = [], set()
    for a in s.find_all("a", href=re.compile(r"bit\.ly/")):
        if a["href"] in seen:
            continue
        seen.add(a["href"])
        par = a.find_parent(["p", "li", "div"])
        line = re.sub(r"\s+", " ", par.get_text(" ") if par else "")
        line = line[: line.find(a["href"])] if a["href"] in line else line
        name, _, addr = line.partition(":")
        addr = re.sub(r"^\s*[^:]*:\s*", "", addr) if addr.count(":") else addr
        out.append((name.strip(), addr.strip(" |"), a["href"]))
    return form, out


def registration_listing(name: str, addr: str, link: str, target: str, cfg: dict | None = None) -> Listing:
    """A bit.ly link to a Pronto registration form; `cfg` is the form's config JSON when it loaded."""
    cfg = cfg or {}
    intro = re.sub(r"\s+", " ", cfg.get("form_text") or "").strip()
    window = ""
    if cfg.get("link_closes_date"):
        window = f" Applications {cfg.get('link_opens_date') or '?'} to {cfg['link_closes_date']}."
    return Listing(
        source=SOURCE,
        ext_id=parse.slug(name or addr),
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


def follow(link: str) -> tuple[int | None, str]:
    """A bit.ly link -> (the Pronto property id it lands on or None, the registration page or final url)."""
    if (pid := KNOWN_PRONTO.get(link.rstrip("/").split("/")[-1])) is not None:
        return pid, link
    r = fetch.get(link)
    chain = [str(x.url) for x in r.history] + [str(r.url)]
    m = next((re.search(r"/listings/property/(\d+)", u) for u in chain if "/listings/property/" in u), None)
    return (int(m.group(1)) if m else None), next((u for u in chain if "registration" in u), chain[-1])


def registration_config(target: str) -> dict | None:
    """The Pronto registration form's config JSON, or None when it does not load."""
    m = re.search(r"/registration/([0-9a-f-]{36})", target)
    try:
        return fetch.json(PRONTO_REG.format(uuid=m.group(1))) if m else None
    except fetch.FetchError:
        return None  # the listing still stands on the link alone


def read_form(form_url: str, table) -> tuple[list[Listing], set[str]]:
    """The re-rental form -> (its listings, the table image urls); `table(url)` OCRs one image."""
    form = fetch.json(form_api_url(form_url))
    tables = {}
    for q in form.get("questions", []):
        img = (q.get("image") or {}).get("resourceUrl")
        if q.get("type") == "Question.Choice" and img:
            tables[img] = table(img)
    return parse_form(form, form_url, tables), set(tables)


class SettlementHousingFund(PageAdapter):
    name = SOURCE
    title = "Settlement Housing Fund"
    url = URL
    pages = [URL]
    min_listings = 1

    def _table(self, img: str) -> list[list[list[str]]]:
        return by_content(self.cache.setdefault("ocr", {}), img, lambda data: {"blocks": ocr_table(data)})["blocks"]

    def fetch(self) -> Result:
        body = fetch.html(URL)
        snaps = [self.snapshot(URL, body)]
        form_url, links = index_links(body)
        listings: list[Listing] = []
        imgs: set[str] = set()
        if form_url:
            found, imgs = read_form(form_url, self._table)
            listings += found
        prune(self.cache.get("ocr", {}), imgs)
        for name, addr, link in links:
            pid, target = follow(link)
            if pid:
                listings.append(parse_pronto(fetch.json(PRONTO_API.format(id=pid)), link))
            else:
                listings.append(registration_listing(name, addr, link, target, registration_config(target)))
        return Result(listings=listings, snapshots=snaps)
