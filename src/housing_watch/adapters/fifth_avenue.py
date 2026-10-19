"""Fifth Avenue Committee re-rentals: one WordPress page of unit cards, grouped here into one listing per building.

Each card (div.grid_section-col) is an <h3> building, an <h4> unit and <li> "Label: value" rows (size,
household size, AMI, rent, min/max income), plus "**" notes (Section 8 / 62+). The household range
("1-2 people") carries one max income, the top size's cap, so per-size caps come from bands_from_row.
Cards under "Upcoming Units" are not marketed yet. Applying is one general Salesforce form, not per unit.
"""

from __future__ import annotations

import re

from .. import ami, fetch, parse
from ..models import Band, Listing, Result, Snapshot, Unit
from . import Adapter

URL = "https://fifthave.org/re-rental-availabilities/"
APPLY = "https://fifthave.my.site.com/s/flow/public?var_flow=lottery"
# FAC buildings named without an address on the page; all in Brooklyn (Sunset Park).
KNOWN_BOROUGH = {"sunset ridge": "Brooklyn", "sunset park": "Brooklyn"}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _card(col) -> dict | None:
    h3, h4 = col.find("h3"), col.find("h4")
    if not h3:
        return None
    fields = {}
    for li in col.find_all("li"):
        t = re.sub(r"\s+", " ", li.get_text("")).strip()  # "" joins "13" + "0%" split across spans
        if ":" in t:
            k, v = t.split(":", 1)
            fields[k.strip().lower()] = v.strip()
    notes = " ".join(re.sub(r"\s+", " ", d.get_text(" ")).strip() for d in col.find_all("div") if "**" in d.get_text())
    return {
        "building": re.sub(r"\s+", " ", h3.get_text(" ")).strip(),
        "unit": re.sub(r"(?i)^unit\s*", "", h4.get_text(" ").strip()) if h4 else None,
        "fields": fields,
        "notes": notes,
    }


def _bands(hh: str, lo: int | None, hi: int | None, a: int | None) -> list[Band]:
    """Per-size bands. The printed max is the top size's cap (ami.bands); min may be absent."""
    sizes = parse.household_range(hh)
    return ami.bands(sizes, lo, hi, a)[0] if sizes else []


def parse_page(html: str) -> list[Listing]:
    s = parse.soup(html)
    groups: dict[tuple[str, bool], list[dict]] = {}
    for col in s.select("div.grid_section-col"):
        c = _card(col)
        if not c or not c["fields"]:
            continue
        head = col.find_previous(["h1", "h2"])
        upcoming = bool(head and re.search(r"upcoming", head.get_text(), re.I))
        groups.setdefault((c["building"], upcoming), []).append(c)
    out = []
    for (building, upcoming), cards in groups.items():
        units = []
        seniors = []
        for c in cards:
            f = c["fields"]
            a = parse.ami(f.get("ami"))
            lo = parse.whole(f.get("min income"))
            hi = parse.whole(f.get("max income"))
            bands = _bands(f.get("household size", ""), lo, hi, a)
            senior = bool(re.search(r"62 years|senior", c["notes"], re.I))
            seniors.append(senior)
            sec8 = bool(re.search(r"section 8|qualify for", c["notes"], re.I))
            units.append(
                Unit(
                    size=parse.size(f.get("unit size")),
                    rent=parse.money(f.get("rent")),
                    ami=a,
                    unit_no=c["unit"],
                    bands=bands,
                    set_aside="senior" if senior else ("section 8" if sec8 else None),
                )
            )
        name, _, addr = building.partition(" -")
        addr = addr.strip() or (building if re.match(r"\d", building) else None)
        borough = parse.borough(building) or next((v for k, v in KNOWN_BOROUGH.items() if k in building.lower()), None)
        out.append(
            Listing(
                source="fifth_avenue",
                ext_id=_slug(name) + ("-upcoming" if upcoming else ""),
                title=name.strip() if addr else building,
                url=URL,
                address=addr,
                borough=borough,
                status="open",
                units=units,
                senior_only=all(seniors),
                notes=("Upcoming: not yet marketed. " if upcoming else "")
                + f"Apply: one general FAC Housing Re-Rental Form {APPLY} (FAC matches you to every vacancy; "
                "resubmit only if household/income changes)",
            )
        )
    return out


class FifthAvenue(Adapter):
    name = "fifth_avenue"
    title = "Fifth Avenue Committee"
    url = URL
    every_hours = 6  # first come, first served re-rentals
    min_listings = 1  # buildings, or a snapshot of the page when it lists none

    def fetch(self) -> Result:
        html = fetch.html(URL)
        if "Re-Rental" not in html:
            raise RuntimeError(f"{URL}: page does not look like FAC re-rentals")
        listings = parse_page(html)
        if listings:
            return Result(listings=listings)
        txt = parse.text(html)
        txt = "\n".join(ln for ln in txt.splitlines() if not re.match(r"©", ln))
        return Result(snapshots=[Snapshot(source=self.name, ext_id=URL, url=URL, text=txt)])
