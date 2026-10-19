"""TF Cornerstone re-rentals, from the JSON API behind tfc.com's re-rentals page.

https://tfc.io/api/affordable-rerentals/ lists every unit (type, rent, AMI %, set-aside) with its income
range per household size, so no rendering is needed (the page itself is a 5 KB shell, and byparr often
snapshots it half-loaded). One Listing per building; ext_ids are the address slugs the page-based
adapter used. The API has no borough, so it comes from the street (all TFC buildings are in Manhattan,
LIC or Brooklyn). An empty API keeps the page as a Snapshot.
"""

from __future__ import annotations

import re

from .. import ami, fetch, parse
from ..models import Band, Listing, Result, Snapshot, Unit
from . import Adapter

PAGE = "https://tfc.com/about/affordable-re-rentals"
API = "https://tfc.io/api/affordable-rerentals/"
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


def _dollars(x) -> int | None:
    return int(x) if x else None  # whole dollars, cents dropped ($85,927.55 -> 85,927)


def parse_api(data: dict) -> Result:
    by_addr: dict[str, list[Unit]] = {}
    for r in data["Data"]:
        u = Unit(
            size=parse.size(r["UnitType"]),
            rent=r.get("MonthlyRent"),
            ami=r.get("AmiPercent"),
            set_aside=_set_aside(r.get("SetAside") or ""),
        )
        for z in sorted(r.get("Sizes") or [], key=lambda z: z["AffordableReRentalSizeId"]):
            h = z["HouseholdSize"]
            if u.bands and h <= u.bands[-1].household:  # site typo: "1 Person" repeated down a tier
                h = u.bands[-1].household + 1
            u.bands.append(Band(h, _dollars(z.get("MinIncome")), _dollars(z.get("MaxIncome"))))
        by_addr.setdefault(r["Property"]["Address"].strip(), []).append(u)
    out = []
    order = {"studio": 0, "1br": 1, "2br": 2, "3br": 3}
    for addr, units in by_addr.items():
        units.sort(key=lambda u: (u.ami or 0, order.get(u.size, 9), u.rent or 0))  # the API order is arbitrary
        ami.stamp(units)
        out.append(
            Listing(
                source="tf_cornerstone",
                ext_id=re.sub(r"[^a-z0-9]+", "-", addr.lower()).strip("-"),
                title=f"TF Cornerstone {addr}",
                url=f"{PAGE}#available-rerentals",
                address=addr,
                borough=_borough(addr, ""),
                status="open",
                kind="re-rental",
                units=units,
                notes=APPLY,
            )
        )
    if out:
        return Result(listings=out)
    return Result(snapshots=[Snapshot(source="tf_cornerstone", ext_id=PAGE, url=PAGE, text="no re-rentals listed")])


class TFCornerstone(Adapter):
    name = "tf_cornerstone"
    title = "TF Cornerstone re-rentals"
    url = f"{PAGE}#available-rerentals"
    every_hours = 4  # first come, first served
    min_listings = 1  # a building, or the snapshot when none are listed

    def fetch(self) -> Result:
        return parse_api(fetch.json(API))
