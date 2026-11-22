"""Bronx Pro Group: one Squarespace page (/apartments) for its own Bronx buildings, all rent-stabilized and affordable.

The page has two parts, each one Listing:
- "General Housing": the waitlist for the portfolio ("Studio to 3-bedroom units", "AMI 50-120%"), no address.
  Each size is one Unit at the top of the AMI range, the most generous tier, so an income above it rules a unit out.
- "Senior Housing": a list block, one card per building ("1080 Washington Ave", "Crotona Park East – 1017 Home
  Street"), with lines such as "62 Yrs+", "1–2-person household", "Studio and 1 bedroom Available",
  "Min Income: $0", "HPD PBV S8 included", "Rent shares are 30% of household income". No rent and no maximum
  income is published (project-based Section 8 sets them), so each band has only the $0 minimum.
There are no market-rate apartments on the page. Applying is one PDF application for everything ("Download
Application"), sent to the leasing office by email, mail, fax or in person. The building pages under /completed/
describe the projects, not availability, so every listing's url is /apartments; address is the street address.
?format=json and /api/ are disallowed by robots.txt, so the HTML is parsed.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from ._page import PageAdapter
from ._pagekit import snapshot

URL = "https://bronxprogroup.com/apartments"
SIZES = ["studio", "1br", "2br", "3br", "4br", "5br", "6br"]
_SIZE_TOKEN = re.compile(r"studio|\b[0-6]\s*-?\s*(?:br|bd|bed(?:room)?s?)\b", re.I)
_ADDR = re.compile(r"\b\d{1,5}\s+[A-Za-z .]+?\b(?:Street|St|Avenue|Ave|Road|Rd|Place|Pl|Boulevard|Blvd)\b\.?", re.I)
_WAITLIST = re.compile(r"interested in joining our waitlist", re.I)


def _sizes(t: str) -> list[str]:
    """'Studio and 1 bedroom' -> [studio, 1br]; 'Studio to 3-bedroom' -> studio..3br."""
    found = [parse.size(m.group(0)) for m in _SIZE_TOKEN.finditer(t)]
    if len(found) == 2 and re.search(r"studio\s+to\s+\d|\d\D{0,12}\bto\s+\d", t, re.I):
        return SIZES[SIZES.index(found[0]) : SIZES.index(found[1]) + 1]
    return list(dict.fromkeys(found))


def _contact(main) -> str:
    """How to apply: the application PDF and the office's contact lines, as printed."""
    flat = parse.text(str(main))
    app = next(
        (a["href"] for a in main.find_all("a", href=True) if re.search(r"application", a.get_text(" "), re.I)), None
    )
    email = next((a["href"][7:].split("?")[0] for a in main.select("a[href^=mailto]")), None)
    office = re.search(r"\d+ Dr\.? Martin Luther King Jr\.? Blvd\.?,? Bronx, NY \d{5}", flat)
    phone = re.search(r"Phone:\s*(\(\d{3}\)\s*\d{3}-\d{4})", flat)
    fax = re.search(r"Fax:\s*(\(\d{3}\)\s*\d{3}-\d{4})", flat)
    hours = re.search(r"Hours:\s*([^\n]+)", flat)
    parts = [f"Apply: download the application ({urljoin(URL, app) if app else URL})"]
    parts.append("and email, mail, fax or drop it off at the leasing office")
    if office:
        parts.append(f"({office.group(0)})")
    extra = [x for x in (email, phone and f"phone {phone.group(1)}", fax and f"fax {fax.group(1)}") if x]
    note = " ".join(parts) + (": " + ", ".join(extra) if extra else "")
    return note + (f"; hours {hours.group(1).strip()}" if hours else "")


def parse_general(s, how: str) -> Listing:
    """The 'General Housing' block: the portfolio waitlist."""
    head = s.find(lambda t: t.name in ("h1", "h2", "h3", "h4") and t.get_text(" ", strip=True) == "General Housing")
    if head is None:
        raise RuntimeError(f"{URL}: 'General Housing' heading not found (layout changed?)")
    block = head.find_next("div", class_="sqs-html-content")
    txt = parse.text(str(block)) if block else ""
    sizes = _sizes(txt)
    m = re.search(r"AMI\s*(\d{2,3})\s*%?\s*[-–to]+\s*(\d{2,3})\s*%", txt, re.I)
    if not sizes or not m:
        raise RuntimeError(f"{URL}: General Housing block has no sizes or AMI range: {txt[:200]!r}")
    lo, hi = int(m.group(1)), int(m.group(2))
    return Listing(
        source="bronx_pro",
        ext_id="general-housing",
        title="Bronx Pro Group: general housing waitlist",
        url=URL,
        borough="Bronx",
        status="waitlist",
        kind="waitlist",
        units=[Unit(size=z, ami=hi) for z in sizes],
        notes=f"{' / '.join(txt.splitlines())}. Units are AMI {lo}-{hi}% (shown at {hi}%, the top of the range). {how}",
    )


def parse_card(li, how: str) -> Listing:
    """One 'Senior Housing' card -> Listing for that building."""
    h = li.find(class_="list-item-content__title")
    desc = li.find(class_="list-item-content__description")
    if h is None or desc is None:
        raise RuntimeError(f"{URL}: a list card has no title or description (layout changed?)")
    title = h.get_text(" ", strip=True)
    lines = [ln.replace("\xa0", " ").strip() for ln in parse.text(str(desc)).splitlines()]
    flat = " / ".join(ln for ln in lines if ln)
    street = _ADDR.search(title)
    if street is None:
        raise RuntimeError(f"{URL}: no street address in card title {title!r}")
    sizes = _sizes(flat)
    hh_m = re.search(r"(\d\s*[-–—]\s*\d|\d)\s*-?\s*person", flat)
    households = parse.household_range(hh_m.group(1)) if hh_m else []
    lo_m = re.search(r"min\.?\s*income:?\s*\$\s*([\d,]+)", flat, re.I)
    lo = parse.whole(lo_m.group(1)) if lo_m else None
    if not sizes:
        raise RuntimeError(f"{URL}: card {title!r} names no unit size: {flat!r}")
    bands = [Band(n, lo, None) for n in households]
    senior = bool(re.search(r"\b62\s*(yrs|years)?\s*\+|\bsenior", flat, re.I))
    available = bool(re.search(r"\bavailable\b", flat, re.I))
    return Listing(
        source="bronx_pro",
        ext_id=parse.slug(street.group(0).rstrip(".")),
        title=title,
        url=URL,
        address=f"{street.group(0).rstrip('.')}, Bronx, NY",
        borough="Bronx",
        status="open" if available else "waitlist",
        kind="waitlist",
        units=[Unit(size=z, bands=list(bands)) for z in sizes],
        senior_only=senior,
        notes=f"{flat}. {how}",
    )


def parse_page(html: str) -> list[Listing]:
    s = parse.soup(html)
    main = s.find("main")
    if main is None or not main.find(string=_WAITLIST):
        raise RuntimeError(f"{URL}: 'Interested in joining our waitlist?' not found (layout changed?)")
    how = _contact(main)
    out = [parse_general(main, how)]
    out += [parse_card(li, how) for li in main.select("li.list-item")]
    return out


class BronxPro(PageAdapter):
    name = "bronx_pro"
    title = "Bronx Pro Group"
    url = URL
    pages = [URL]
    min_listings = 1  # the general waitlist is always on the page; the senior cards may legitimately go

    def fetch(self) -> Result:
        body = fetch.html(URL)
        return Result(listings=parse_page(body), snapshots=[snapshot(self, URL, body, root="main")])
