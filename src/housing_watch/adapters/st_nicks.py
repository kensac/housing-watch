"""St. Nicks Alliance: re-rentals are one-line links to flyer PDFs under "Option 2 ... Current openings:".

Each flyer (pdftotext -layout) gives size, AMI, rent and one income range for "1-2 persons". Its "CLICK HERE
TO APPLY" is a PDF link annotation (read with pdftohtml -xml) to the property's application PDF, whose
page 1 says where to mail it. PDF text is cached by URL + sha256 so unchanged flyers are not re-read.

Standing waitlists, one listing each with its paper application as the url: "Option 3" (the General Wait List
for 35+ multi-family properties, and 270 Pulaski Street's own list) and the HUD "Senior Housing" buildings.
The building's address is the one its application's header prints (or its mail-to block, when that names the
building). None of them publishes sizes, rents or income limits.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from . import by_content, prune
from ._page import PageAdapter

INDEX = "https://stnicksalliance.org/get-help-affordable-housing/"


def _pdf_extract(data: bytes) -> dict:
    links: list[str] = []
    if shutil.which("pdftohtml"):
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(data)
            f.flush()
            xml = subprocess.run(
                ["pdftohtml", "-xml", "-stdout", "-i", "-q", f.name], capture_output=True, text=True, timeout=120
            ).stdout
        links = list(dict.fromkeys(re.findall(r'href="(https?://[^"]+)"', xml)))
    return {"text": fetch.pdf_bytes_text(data), "links": links}


def pdf_with_links(cache: dict, url: str) -> dict:
    """{'text', 'links'} of a PDF, kept in cache["pdf"] by content hash."""
    return by_content(cache.setdefault("pdf", {}), url, _pdf_extract)


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


def senior_apps(html: str) -> list[tuple[str, str]]:
    """(building, application url) for each building under "Senior Housing" (HUD, for elderly or disabled
    residents), each with its own waitlist."""
    s = parse.soup(html)
    head = s.find(["h2", "h3"], string=re.compile(r"Senior Housing\s*$", re.I))
    out = []
    for el in head.find_all_next(["a", "h2", "h3"]) if head else []:
        if el.name != "a":
            if "senior" not in el.get_text(" ").lower():
                break
            continue
        if el.get("href", "").lower().endswith(".pdf") and (name := el.get_text(" ", strip=True)):
            out.append((name, urljoin(INDEX, el["href"])))
    return out


def senior_waitlists(html: str) -> list[str]:
    """The names of the "Senior Housing" buildings (SANEL's adapter marks its copies of them senior)."""
    return [name for name, _ in senior_apps(html)]


def multifamily_apps(html: str) -> list[tuple[str, str, str]]:
    """(waitlist, application url, the page's description of it) for each list under "Option 3": a "... Wait List"
    heading or paragraph names the list, and the first application PDF after it is its application."""
    s = parse.soup(html)
    start = s.find(string=re.compile(r"Option\s*3", re.I))
    if start is None:
        raise RuntimeError("St Nicks page has no 'Option 3' (multi-family waitlists) section")
    out: list[tuple[str, str, str]] = []
    name = blurb = None
    for el in start.find_all_next(["a", "p", "h2", "h3", "h4"]):
        txt = el.get_text(" ", strip=True)
        if el.name != "a" and re.search(r"Senior Housing\s*$", txt, re.I):
            break
        if el.name != "a" and (m := re.match(r"(.{0,60}?\bWait List)\b\s*[-–—:]?\s*(.*)", txt)):  # a name: capitals
            name, blurb = m.group(1).strip(), m.group(2).strip()
            continue
        href = urljoin(INDEX, el.get("href", "")) if el.name == "a" else ""
        if name and href.lower().endswith(".pdf"):
            out.append((name, href, blurb or ""))
            name = None
    if not out:
        raise RuntimeError("St Nicks 'Option 3' section names no waitlist with an application PDF")
    return out


_MAIL = re.compile(r"MAIL (?:COMPLETE APPLICATION )?TO:[ \t]*(.*)\n((?:[ \t]*\S.*\n){1,4})", re.I)
_STREET = re.compile(r"\b\d{1,5}\s+(?:[A-Za-z]+\s+){1,3}?(?:Street|Avenue|Place|Road|Boulevard)\b", re.I)
_CITY = re.compile(r"\b([A-Za-z]+),?\s+NY,?\s+(\d{5})\b")
_BROOKLYN_HOODS = re.compile(r"\b(Greenpoint|Williamsburg|Bed-?Stuy|Bushwick|Bedford-Stuyvesant)\b", re.I)


def _mail_lines(app_text: str) -> list[str]:
    m = _MAIL.search(app_text or "")
    if not m:
        return []
    return [ln.strip() for ln in [m.group(1), *m.group(2).splitlines()] if ln.strip()]


def mail_address(app_text: str) -> str | None:
    return ", ".join(lines) if (lines := _mail_lines(app_text)) else None


def _street_city(text: str) -> str | None:
    """'320 Devoe Street ... BROOKLYN, NY 11211' -> '320 Devoe Street, Brooklyn, NY 11211'."""
    street = _STREET.search(text)
    if not street:
        return None
    city = _CITY.search(text, street.end())
    s = street.group(0).title() if street.group(0).isupper() else street.group(0)  # "270 PULASKI STREET, LP"
    return s + (f", {city.group(1).title()}, NY {city.group(2)}" if city else "")


def building_address(name: str, app_text: str) -> str | None:
    """The address an application's header prints above its instructions, else its mail-to address when the
    recipient is the building itself ("C/O: Ellery Court"), not St. Nicks' office."""
    header = re.split(r"\bMAIL\b", app_text or "", maxsplit=1)[0]
    if addr := _street_city(header):
        return addr
    lines = _mail_lines(app_text)
    key = parse.name_key(name)
    recipient = " ".join(ln for ln in lines if not _STREET.search(ln) and not _CITY.search(ln)).lower()
    if key and key in recipient:
        return _street_city("\n".join(lines))
    return None


def parse_waitlist(name: str, url: str, app_text: str, senior: bool, blurb: str = "") -> Listing:
    """One standing waitlist: its application PDF is the posting, its header the building's address."""
    addr = building_address(name, app_text)
    if addr is None and not re.match(r"General\b", name, re.I):
        raise RuntimeError(f"St Nicks application {url} for {name!r} prints no building address")
    notes = [blurb.rstrip(".") + "."] if blurb else []
    program = re.search(r"HUD\s+Section\s+(8|202)|Section\s+(8)\s+Project", app_text, re.I)
    if program:
        notes.append(f"HUD Section {program.group(1) or program.group(2)} housing (per its application).")
    if senior:
        notes.append("St. Nicks lists it under its HUD senior housing (elderly or disabled residents).")
    apply = f"Apply: paper application {url}"
    if mail := mail_address(app_text):
        regular = "regular mail only" if re.search(r"REGULAR MAIL", app_text, re.I) else "mail"
        apply += f"; {regular} to {mail}"
    notes.append(apply + ".")
    sizes = re.search(r"BEDROOM SIZE REQUESTED:(.*)", app_text)
    if sizes:
        opts = re.sub(r"_+", " ", sizes.group(1)).split()
        notes.append(f"Its application offers sizes: {', '.join(opts)}.")
    notes.append("St. Nicks publishes no unit list, rents or income limits for it.")
    return Listing(
        source="st_nicks",
        ext_id=parse.slug(name.removesuffix(".")),
        title=f"St. Nicks Alliance {name}" if re.match(r"General\b", name, re.I) else name,
        url=url,
        address=addr,
        borough=parse.borough(addr) or ("Brooklyn" if _BROOKLYN_HOODS.search(blurb) else None),
        status="waitlist",  # the page offers an application for it: the list is taking names
        kind="waitlist",
        senior_only=senior,
        notes=" ".join(notes),
    )


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
    min_listings = 4  # the standing waitlists (2 multi-family, 5 senior) are always listed

    def fetch(self) -> Result:
        body = fetch.html(INDEX)
        snaps = [self.snapshot(INDEX, body)]
        out, seen = [], set()
        for label, url in openings(body):
            if not url.lower().endswith(".pdf"):
                out.append(Listing(source=self.name, ext_id=url, title=label, url=url, borough=parse.borough(label)))
                continue
            flyer = pdf_with_links(self.cache, url)
            seen.add(url)
            apply_url = next((u for u in flyer["links"] if u.lower().endswith(".pdf")), None)
            app_text = ""
            if apply_url:
                app_text = pdf_with_links(self.cache, apply_url)["text"]
                seen.add(apply_url)
            out.append(parse_flyer(label, url, flyer["text"], apply_url, app_text))
        seniors = senior_apps(body)
        if not seniors:
            raise RuntimeError("St Nicks page lists no Senior Housing applications")
        lists = [(n, u, b, False) for n, u, b in multifamily_apps(body)] + [(n, u, "", True) for n, u in seniors]
        for name, url, blurb, senior in lists:
            seen.add(url)
            out.append(parse_waitlist(name, url, pdf_with_links(self.cache, url)["text"], senior, blurb))
        prune(self.cache.get("pdf", {}), seen)
        return Result(listings=out, snapshots=snaps)
