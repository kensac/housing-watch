"""MHANY Management: one Duda sub-page per re-rental building, each with HTML income tables.

The re-rental page's menu lists the building pages. Closed buildings stay linked; their status is the
"Recently Leased Properties" section of /avaliable-properties. Tables have one row per household size
(continuation rows leave the size/AMI/rent cells empty). A "62+" heading above a table = senior units.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from ._page import PageAdapter

BASE = "https://www.mutualhousingny.org"
RERENTALS = f"{BASE}/affordable-re-rental-units"
AVAILABLE = f"{BASE}/avaliable-properties"
OFFICE = re.compile(r"vanderbilt|11238|mhany management inc|lottery dept", re.I)


def building_urls(html: str) -> list[str]:
    """Pages under the menu's "Affordable Re-Rental Units" entry, plus the body's "View" links."""
    s = parse.soup(html)
    hrefs = [a["href"].strip() for a in s.find_all("a", href=True)]
    out: list[str] = []
    if "/affordable-re-rental-units" in hrefs:
        for h in hrefs[hrefs.index("/affordable-re-rental-units") + 1 :]:
            if h in ("#", "/home-ownership") or not h.startswith("/"):
                break
            out.append(h)
    for a in s.find_all("a", href=True):
        if a.get_text(strip=True).lower() == "view" and a["href"].startswith("/"):
            out.append(a["href"].strip())
    return [urljoin(BASE, h) for h in dict.fromkeys(out)]


def closed_section(html: str) -> str:
    t = parse.text(html)
    i = t.find("Recently Leased")
    if i < 0:
        return ""
    j = t.find("How to Apply", i)
    return t[i : j if j > 0 else i + 3000]


def _income(txt: str) -> tuple[int | None, int | None]:
    nums = [int(float(x.replace(",", ""))) for x in re.findall(r"\$\s*(\d[\d,]*(?:\.\d+)?)", txt or "")]
    return (nums[0], nums[1]) if len(nums) >= 2 else (None, nums[0] if nums else None)


def _col(header: list[str], pat: str) -> int | None:
    return next((i for i, h in enumerate(header) if re.search(pat, h, re.I)), None)


def _units(s) -> list[Unit]:
    units: list[Unit] = []
    for table in s.find_all("table"):
        heading = table.find_previous(string=re.compile(r"62\s*\+|general population|senior", re.I)) or ""
        senior = bool(re.search(r"62\s*\+|senior", heading, re.I))
        rows = parse.table_rows(parse.soup(str(table)))
        if not rows:
            continue
        header, body = rows[0], rows[1:]
        c_size = _col(header, r"^(unit )?size$")
        c_rent = _col(header, r"rent")
        c_hh = _col(header, r"household size|# of people|^people")
        c_inc = next((i for i in range(len(header) - 1, -1, -1) if re.search(r"income", header[i], re.I)), None)
        c_ami = _col(header, r"ami|median")
        if c_size is None or c_inc is None:
            continue
        cur: Unit | None = None
        for cells in body:
            cells = cells + [""] * (len(header) - len(cells))
            if cells[c_size].strip():
                ami = parse.ami(cells[c_ami]) if c_ami is not None else None
                rent = parse.money(cells[c_rent]) if c_rent is not None and "$" in cells[c_rent] else None
                cur = Unit(size=parse.size(cells[c_size]), rent=rent, ami=ami, set_aside="senior" if senior else None)
                units.append(cur)
            if cur is None:
                continue
            lo, hi = _income(cells[c_inc])
            hh = parse.household_range(cells[c_hh]) if c_hh is not None else []
            if len(hh) == 1:
                cur.bands.append(Band(hh[0], lo, hi))
            elif hh:
                cur.bands += parse.bands_from_row(cells[c_hh], f"${lo:,} - ${hi:,}" if lo and hi else "", cur.ami)
    return units


def _borough(body: str) -> str | None:
    counts: dict[str, list[int]] = {}
    for m in re.finditer(r"\b(Bronx|Brooklyn|Manhattan|Queens|Staten Island)\b", body):
        counts.setdefault(m.group(1), []).append(m.start())
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (len(kv[1]), -kv[1][0]))[0]


def parse_building(url: str, html: str, closed: str) -> Listing:
    s = parse.soup(html)
    mails = [
        a["href"][7:].split("?")[0]
        for a in s.find_all("a", href=True)
        if a["href"].lower().startswith("mailto:") and not a["href"].lower().startswith("mailto:info@")
    ]
    apps = [
        urljoin(url, a["href"])
        for a in s.find_all("a", href=True)
        if a.get_text(strip=True).upper().startswith("DOWNLOAD APPLICATION")
    ]
    t = parse.text(s)
    i = t.find("DONATE")
    j = t.find("Take the Next Step")
    body = t[i if i >= 0 else 0 : j if j > 0 else len(t)]
    title = (s.title.string or "").split("|")[0].strip() if s.title else url
    lines = body.splitlines()
    title = lines[2] if len(lines) > 2 and lines[1] == "Affordable Re-Rental Units" else title
    mail_to = ""
    m = re.search(
        r"(?:mailed to|drop off[^:\n]*to):\s*\n(.+?)\n(?:DOWNLOAD|Applications emailed|REQUIRED|You also)", body, re.S
    )
    if m:
        mail_to = re.sub(r"\s*\n\s*", " ", m.group(1)).replace(" th FL", "th FL").strip()
    addrs = re.findall(
        r"^\d{2,5}\s+(?:West|East|W\.?|E\.?)?\s*[\w ]+?(?:Street|St|Avenue|Ave|Road|Rd|Place|Pl)\b", body, re.M
    )
    addrs = [a for a in dict.fromkeys(addrs) if not OFFICE.search(a)]
    nums = re.findall(r"\b\d{3,5}\b", title)
    name = re.sub(r"\bHDFC\b", "", title, flags=re.I).strip().upper()
    is_closed = bool(closed) and (
        any(re.search(rf"\b{n}\b", closed) for n in nums) or (not nums and name and name in closed.upper())
    )
    units = _units(s)
    notes = []
    if mails:
        notes.append("email " + ", ".join(dict.fromkeys(mails)))
    if mail_to:
        notes.append(f"or mail to {mail_to}")
    if apps:
        notes.append(f"application PDF {apps[0]}")
    one = re.search(r"\*\*Note (1 application per person)\*\*", body)
    if one:
        notes.append(one.group(1))
    clean = "\n".join(ln for ln in body.splitlines() if not OFFICE.search(ln))
    return Listing(
        source="mhany",
        ext_id=url.rstrip("/").split("/")[-1],
        title=title,
        url=url,
        address=" / ".join(addrs) or None,
        borough=_borough(clean),
        status="closed" if is_closed else "open",
        units=units,
        senior_only=bool(units) and all(u.set_aside == "senior" for u in units),
        notes=("Apply: " + "; ".join(notes)) if notes else None,
    )


class Mhany(PageAdapter):
    name = "mhany"
    title = "MHANY Management"
    url = RERENTALS
    pages = [RERENTALS, AVAILABLE]
    min_listings = 1

    def fetch(self) -> Result:
        snaps = super().fetch().snapshots
        urls = building_urls(fetch.html(RERENTALS))
        if not urls:
            raise RuntimeError("no building pages linked from the re-rental page")
        closed = closed_section(fetch.html(AVAILABLE))
        return Result(listings=[parse_building(u, fetch.html(u), closed) for u in urls], snapshots=snaps)
