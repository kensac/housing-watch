"""Hudson Companies: the "Affordable/Middle Income Rental Apartments" section of /availability.

The section is a run of <p><a href=building site>Name</a></p> each followed by <p class="grey-6b"> with
the program's AMI range and status ("Affordability 100-125% AMI. Waiting List Open", "... Application
Period Open Now!"). Each becomes a Listing with one tier Unit (size unknown, ami = top of the range, so a
range topping out at 125% or less is excluded for a $150k single). Unit sizes, rents and income tables
live on each building's own leasing site, linked in notes. The borough comes from the market-rate list on
the same page (BROOKLYN / MANHATTAN headings) or the name/link. The whole page is also snapshotted.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Listing, Result, Unit
from ._page import PageAdapter
from ._pagekit import snapshot

URL = "https://www.hudsoninc.com/availability/"
_AMI = re.compile(r"(\d{2,3})\s*(?:%\s*)?(?:-|–|to)\s*(\d{2,3})\s*%|(\d{2,3})\s*%")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _status(text: str) -> tuple[str, str]:
    t = text.lower()
    if re.search(r"closed|no longer|filled", t):
        return "closed", "waitlist" if "wait" in t else "lottery"
    if re.search(r"wait(ing)?\s*-?\s*list", t):
        return "waitlist", "waitlist"
    return "open", "lottery"  # "Application Period Open Now!"


def _section(s, heading: str):
    """Elements after the h2 whose text starts with `heading`, up to the next h2."""
    h2 = next((h for h in s.find_all("h2") if h.get_text(" ", strip=True).lower().startswith(heading)), None)
    if h2 is None:
        return None, []
    out = []
    for el in h2.find_next_siblings():
        if el.name == "h2":
            break
        out.append(el)
    return h2, out


def _market_boroughs(s) -> dict[str, str]:
    """Building name -> borough from the market-rate list's BROOKLYN / MANHATTAN subheadings."""
    out: dict[str, str] = {}
    _, els = _section(s, "market-rate")
    cur = None
    for el in els:
        txt = el.get_text(" ", strip=True)
        if not el.find("a") and txt.isupper():
            cur = parse.borough(txt.split("/")[0])
        elif cur:
            for a in el.find_all("a"):  # several buildings share one <p>, split by <br>
                out[slug(a.get_text(" ", strip=True))] = cur
    return out


def parse_programs(html: str, url: str = URL) -> list[Listing]:
    s = parse.soup(html)
    h2, els = _section(s, "affordable")
    if h2 is None:
        raise RuntimeError(f"{url}: no 'Affordable/Middle Income' heading (layout changed?)")
    boroughs = _market_boroughs(s)
    out: list[Listing] = []
    for i, el in enumerate(els):
        a = el.find("a", href=True)
        if a is None or "grey-6b" in (el.get("class") or []):
            continue
        name = a.get_text(" ", strip=True)
        nxt = els[i + 1] if i + 1 < len(els) else None
        desc = nxt.get_text(" ", strip=True) if nxt is not None and "grey-6b" in (nxt.get("class") or []) else ""
        status, kind = _status(desc)
        m = _AMI.search(desc)
        top = int(m.group(2) or m.group(3)) if m else None
        link = urljoin(url, a["href"])
        out.append(
            Listing(
                source="hudson",
                ext_id=slug(name),
                title=name,
                url=link,
                borough=boroughs.get(slug(name)) or parse.borough(name, link),
                status=status,
                kind=kind,
                units=[Unit(size="unknown", ami=top)],
                notes=f"{desc or 'no status text'}. Details and applications: {link}",
            )
        )
    return out


class Hudson(PageAdapter):
    name = "hudson"
    title = "Hudson Companies"
    url = URL
    pages = [URL]
    every_hours = 24
    min_text = 400
    min_listings = 1  # 3 programs on 2026-10-19 (Bergen Gardens, The Clark, La Central)

    def fetch(self) -> Result:
        body = fetch.html(URL)
        listings = parse_programs(body)
        if not listings:
            raise RuntimeError(f"{URL}: no affordable programs listed (layout changed?)")
        return Result(listings=listings, snapshots=[snapshot(self, URL, body, root="main")])
