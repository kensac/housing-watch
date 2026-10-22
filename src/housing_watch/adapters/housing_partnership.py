"""NYC Housing Partnership: server-rendered index of properties + one detail page per property.

The index splits "Active Listings" from "Properties With No Current Vacancies"; a detail page of the
latter carries a "No vacancies at the moment" banner. Each detail page has one table per AMI band
(the % is in the table caption): a unit row (size, an "Available" tag, rent, total units) followed by
one row per household size. A lone income figure is the maximum (the minimum is then unpublished, and
said so in notes). Within an active property only rows
tagged "Available" with a nonzero count are open. No-vacancy rentals are kept as waitlists (HP logs
applications in order received), so their 130% tiers still show up. The footer holds HP's own office
address (Manhattan 10001): the borough comes from the "Property Overview" block instead.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

INDEX = "https://www.housingpartnership.com/services/current-vacancies"
EMAIL = "marketing@housingpartnership.com"
PHONE = "(646) 217-3370"
REFRESH_INACTIVE = 7 * 86400  # no-vacancy pages rarely change; re-read weekly or when their card changes


def parse_index(html: str) -> list[dict]:
    """[{url, name, address, active}] in page order."""
    s = parse.soup(html)
    out, seen = [], set()
    for sec in s.find_all("section"):
        h2 = sec.find("h2")
        if not h2:
            continue
        head = h2.get_text(" ", strip=True).lower()
        if "active listings" in head:
            active = True
        elif "no current vacancies" in head:
            active = False
        else:
            continue
        for a in sec.find_all("a", href=re.compile(r"/current-vacancies/[^/]+$")):
            url = urljoin(INDEX, a["href"])
            if url in seen:
                continue
            seen.add(url)
            h3, addr = a.find("h3"), a.find("span", class_="text-sm")
            out.append(
                {
                    "url": url,
                    "name": h3.get_text(" ", strip=True) if h3 else url.rsplit("/", 1)[-1],
                    "address": addr.get_text(" ", strip=True) if addr else None,
                    "active": active,
                }
            )
    return out


def _bands(person_rows: list[tuple[str, str]]) -> list[Band]:
    bands = []
    for hh, inc in person_rows:
        sizes = parse.household_range(hh)
        lo, hi = parse.money_range(inc)
        if lo is not None and hi is None:  # a lone figure is the maximum
            lo, hi = None, lo
        bands += [Band(h, lo, hi) for h in sizes]
    return bands


def parse_tables(s, active: bool) -> list[Unit]:
    units: list[Unit] = []
    for table in s.find_all("table"):
        cap = table.find("caption")
        ami = parse.ami(cap.get_text(" ", strip=True) if cap else "")
        heads = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        rent_i = next((i for i, h in enumerate(heads) if "rent" in h), None)
        count_i = next((i for i, h in enumerate(heads) if "total units" in h), None)
        groups: list[dict] = []
        for tr in table.find("tbody").find_all("tr") if table.find("tbody") else []:
            tds = tr.find_all("td")
            cells = [re.sub(r"\s+", " ", td.get_text(" ")).strip() for td in tds]
            p = next(
                (i for i, c in enumerate(cells) if re.fullmatch(r"\d+(\s*-\s*\d+)?\s*(person|people)s?", c, re.I)), None
            )
            if p is None:
                continue
            if p > 0:  # a new unit row: size [Available], rent, total units, household, income
                first = tds[0]
                tagged = bool(first.find(string=re.compile(r"^\s*Available\s*$")))
                size_txt = re.sub(r"\bAvailable\b", "", cells[0]).strip()
                rent_txt = cells[rent_i] if rent_i is not None and rent_i < p else ""
                count = parse.whole(cells[count_i]) if count_i is not None and count_i < p else None
                if active:
                    available = tagged and (count is None or count > 0)
                else:
                    available = None  # building fully occupied: no current unit, but the tier still exists
                groups.append(
                    {
                        "size": size_txt,
                        "rent": parse.money(rent_txt) if "$" in rent_txt else None,
                        "available": available,
                        "rows": [],
                    }
                )
            if groups:
                inc = cells[p + 1] if p + 1 < len(cells) else ""
                groups[-1]["rows"].append((cells[p], inc))
        units += [
            Unit(
                size=parse.size(g["size"]),
                rent=g["rent"],
                ami=ami,
                bands=_bands(g["rows"]),
                available=g["available"],
            )
            for g in groups
        ]
    return units


def parse_detail(url: str, html: str, active: bool = True, name: str | None = None) -> Listing:
    s = parse.soup(html)
    flat = parse.text(s)
    lines = flat.splitlines()
    novac = "No vacancies at the moment" in flat[:3000]
    active = active and not novac
    h1 = s.find("h1")
    title = (h1.get_text(" ", strip=True) if h1 else None) or name or url.rsplit("/", 1)[-1]
    homeowner = "homeownership" in title.lower() or "price limits" in flat.lower()
    # Property Overview: "Borough\nmanhattan ... Address\n<addr>"
    ov = flat[flat.rfind("Property Overview") :] if "Property Overview" in flat else ""
    m_b = re.search(r"\nBorough\n([^\n]+)", ov)
    m_a = re.search(r"\nAddress\n([^\n]+)", ov)
    address = m_a.group(1).strip() if m_a else None
    if not address and h1:
        nxt = h1.find_next(string=True)
        address = nxt.strip() if nxt else None
    borough = parse.borough(m_b.group(1)) if m_b else None
    borough = borough or parse.borough(address)
    intro = "\n".join(lines[: next((i for i, ln in enumerate(lines) if ln == "Services"), 40)])
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", intro)) - {EMAIL})
    units = parse_tables(s, active)
    if homeowner:
        status = "open" if active else "closed"
    else:
        status = "open" if active else "waitlist"
    how = f"Request an application via the contact form on {url} or email {EMAIL} ({PHONE}); no fee"
    if emails:
        how = f"Contact {', '.join(emails)}; or " + how[0].lower() + how[1:]
    if not active:
        how = "No vacancies at the moment (applications logged in order received). " + how
    if any(b.income_min is None for u in units for b in u.bands):
        how += ". Some tiers print only a maximum income: HP publishes no minimum for them"
    return Listing(
        source="housing_partnership",
        ext_id=url.rstrip("/").rsplit("/", 1)[-1],
        title=title,
        url=url,
        address=address,
        borough=borough,
        status=status,
        kind="homeownership" if homeowner else ("re-rental" if active else "waitlist"),
        units=units,
        senior_only=bool(re.search(r"\bseniors? (only|62)|62 (years|and older)", flat, re.I)),
        notes=how,
    )


def _from_dict(d: dict) -> Listing:
    units = [Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]}) for u in d["units"]]
    return Listing(**{**d, "units": units})


class HousingPartnership(Adapter):
    name = "housing_partnership"
    title = "NYC Housing Partnership"
    url = INDEX
    min_listings = 3  # ~12 properties are always listed (active or not); fewer = page changed/blocked

    def fetch(self) -> Result:
        cards = parse_index(fetch.html(INDEX))
        if len(cards) < self.min_listings:
            raise RuntimeError(f"index lists only {len(cards)} properties")
        cache: dict = self.cache.setdefault("pages", {})
        now = time.time()
        out = []
        for c in cards:
            fp = hashlib.sha1(repr(sorted(c.items())).encode()).hexdigest()
            hit = cache.get(c["url"])
            if hit and hit["fp"] == fp and not c["active"] and now - hit["at"] < REFRESH_INACTIVE:
                out.append(_from_dict(hit["listing"]))
                continue
            lst = parse_detail(c["url"], fetch.html(c["url"]), c["active"], c["name"])
            cache[c["url"]] = {"fp": fp, "at": now, "listing": asdict(lst)}
            out.append(lst)
        live = {c["url"] for c in cards}
        for u in [u for u in cache if u not in live]:
            del cache[u]
        return Result(listings=out)
