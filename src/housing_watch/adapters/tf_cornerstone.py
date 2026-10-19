"""TF Cornerstone re-rentals: one JS-rendered page, a card per building, a table per AMI band.

Each table row group is one tier (unit type + rent + set-aside) with a row per household size, so bands
are direct. The page prints no borough, so it comes from the street (all TFC buildings are in Manhattan,
LIC or Brooklyn). A known site typo repeats "1 Person" on every household row of a tier: a repeated size
is read as the next size. With no building cards, the section is kept as a Snapshot.
"""

from __future__ import annotations

import re

from .. import fetch, parse
from ..models import Band, Listing, Result, Snapshot, Unit
from . import Adapter

PAGE = "https://tfc.com/about/affordable-re-rentals"
APPLY = (
    "Apply: 'Request Application' button on the page (contact form; the prefilled message names 5241 Center "
    "Blvd, edit it), or email AffordableHousingAPPT@TFCornerstone.com for the HC2.0 application PDF and "
    "return it there stating the building and unit size. No broker or application fee."
)
STREETS = [  # TFC portfolio streets -> borough
    (r"center blvd|\b5[01]st ave|\b2nd st\b.*lic|jackson ave|long island city|\blic\b", "Queens"),
    (r"dean st|schermerhorn|flatbush|brooklyn", "Brooklyn"),
    (r"\b[ew]\.? \d+(st|nd|rd|th) st|avenue|\bave\b|hanover|broadway|manhattan", "Manhattan"),
]
SET_ASIDES = [
    ("mobility", r"mobility|\bmi\b"),
    ("hearing/visual", r"hearing|visual|\bh/?v\b"),
    ("senior", r"senior|62\+"),
]


def _borough(title: str, desc: str) -> str | None:
    t = title.lower()
    for pat, b in STREETS:
        if re.search(pat, t):
            return b
    # description: drop skyline/view mentions of other boroughs before guessing
    d = re.sub(r"(manhattan|brooklyn|queens)\s+(skyline|views?)|views? of [^.]*", "", desc, flags=re.I)
    return parse.borough(d)


def _set_aside(text: str) -> str | None:
    for name, pat in SET_ASIDES:
        if re.search(pat, text, re.I):
            return name
    return None


def _tiers(table, ami: int | None) -> list[Unit]:
    units: list[Unit] = []
    for tr in table.select("tbody tr"):
        tds = [re.sub(r"\s+", " ", td.get_text(" ")).strip() for td in tr.find_all("td")]
        if len(tds) >= 5:  # first row of a tier: type, rent, set-aside, household, income
            units.append(Unit(size=parse.size(tds[0]), rent=parse.money(tds[1]), ami=ami, set_aside=_set_aside(tds[2])))
            hh, inc = tds[3], tds[4]
        elif len(tds) == 2 and units:
            hh, inc = tds
        else:
            continue
        u = units[-1]
        sizes = parse.household_range(hh)
        if not sizes:
            continue
        h = sizes[0]
        if u.bands and h <= u.bands[-1].household:  # site typo: "1 Person" repeated down the tier
            h = u.bands[-1].household + 1
        lo, hi = parse.money_range(inc)
        u.bands.append(Band(h, lo, hi))
    return units


def parse_page(html: str, url: str = PAGE) -> Result:
    s = parse.soup(html)
    if "Available Re-Rentals" not in parse.text(s):
        raise RuntimeError(f"{url}: no 'Available Re-Rentals' section (render failed?)")
    out = []
    for card in s.select("div.building-card"):
        h = card.select_one("h3.heading-2") or card.find("h3")
        title = re.sub(r"\s+", " ", h.get_text(" ")).strip() if h else ""
        if not title:
            continue
        desc = parse.text(str(card.select_one("div.basic-text") or card))
        units: list[Unit] = []
        for block in card.select("div.income-requirements"):
            h4 = block.find("h4")
            a = parse.ami(h4.get_text() if h4 else "")
            for table in block.select("table.income-table"):
                units += _tiers(table, a)
        out.append(
            Listing(
                source="tf_cornerstone",
                ext_id=re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"),
                title=f"TF Cornerstone {title}",
                url=f"{url}#available-rerentals",
                address=title,
                borough=_borough(title, desc),
                status="open",
                kind="re-rental",
                units=units,
                notes=APPLY,
            )
        )
    if out:
        return Result(listings=out)
    i = (t := parse.text(s)).find("Available Re-Rentals")
    return Result(snapshots=[Snapshot(source="tf_cornerstone", ext_id=url, url=url, text=t[i : i + 4000])])


class TFCornerstone(Adapter):
    name = "tf_cornerstone"
    title = "TF Cornerstone re-rentals"
    url = f"{PAGE}#available-rerentals"
    every_hours = 4  # first come, first served
    min_listings = 1  # a building, or the snapshot when none are listed

    def fetch(self) -> Result:
        return parse_page(fetch.rendered(PAGE, wait_ms=6000))
