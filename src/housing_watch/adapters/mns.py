"""MNS Real Estate affordable units: one static table of live units, plus a detail page per unit.

Only `tr.afordable_listing` rows are live (the building filter list above the table names buildings,
not vacancies). The detail page's "description" states MINIMUM INCOME and MAXIMUM INCOME, often only
for one household size ("2 person: $77,760"). When no 1-person max is printed, the AMI % is inferred
from the printed cap and the 1-person cap derived from it (parse.ami_max), as bands_from_row does.
The borough is the last segment of the detail URL (/details/<id>/rental/<area>/<borough>). The page is
often empty of units, so it is also returned as a Snapshot; that keeps a zero-vacancy run valid.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from urllib.parse import unquote_plus, urljoin

from .. import fetch, parse
from ..models import Band, Listing, Result, Snapshot, Unit
from . import Adapter

URL = "https://www.mns.com/affordable_units"
OFFICE = "718-222-0211"
PHONE_RE = re.compile(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}")


def parse_index(html: str) -> list[dict]:
    """Live table rows: [{url, address, unit, beds, price}]."""
    s = parse.soup(html)
    if not s.find(string=re.compile(r"^\s*Availability\s*$")):
        raise RuntimeError("MNS affordable page has no Availability table (layout changed?)")
    rows = []
    for tr in s.select("tr.afordable_listing"):
        a = tr.find("a", href=True)
        cells = {
            (x.get("aria-label") or "").lower(): re.sub(r"\s+", " ", x.get_text(" ")).strip() for x in tr.find_all("a")
        }
        rows.append(
            {
                "url": urljoin(URL, a["href"]) if a else URL,
                "address": cells.get("address") or re.sub(r"\s+", " ", tr.get("laddress", "")).strip(),
                "unit": cells.get("unit") or None,
                "beds": cells.get("bed") or tr.get("lbedrooms", ""),
                "price": parse.money(cells.get("price")),
            }
        )
    return rows


def parse_income(desc: str) -> tuple[list[Band], int | None]:
    """'MINIMUM INCOME: $35,000 / MAXIMUM INCOME: 2 person: $77,760' -> bands (+1-person derived), ami%."""
    m = re.search(r"MINIMUM\s+INCOME\s*:?\s*\$?([\d,]{4,})", desc, re.I)
    lo = parse.whole(m.group(1)) if m else None
    mx = re.search(r"MAXIMUM\s+INCOME\s*:?(.{0,300})", desc, re.I | re.S)
    caps: dict[int, int] = {}
    if mx:
        seg = mx.group(1)
        for hh, val in re.findall(r"(\d)\s*(?:person|people|ppl)s?\s*:?\s*\$([\d,]{4,})", seg, re.I):
            caps[int(hh)] = parse.whole(val)
        if not caps:
            v = re.search(r"\$([\d,]{4,})", seg)
            if v:  # a bare figure with no household size: assume it applies to a single person
                caps[1] = parse.whole(v.group(1))
    ami = parse.ami(desc)
    if caps and 1 not in caps:
        h = min(caps)
        pct = ami or (round(caps[h] / parse.AMI100[h] * 100, 1) if h in parse.AMI100 else None)
        if pct:
            caps[1] = parse.ami_max(pct, 1)
    if not caps and lo is not None:
        return [Band(1, lo, None)], ami
    return [Band(h, lo, caps[h]) for h in sorted(caps)], ami


def parse_detail(url: str, html: str, row: dict | None = None) -> Listing:
    row = row or {}
    s = parse.soup(html)
    flat = parse.text(s)
    i = flat.find("\ndescription\n")
    j = flat.find("\nsimilar listings", i)
    desc = flat[i + 13 : j if j > i else i + 3000] if i >= 0 else ""
    bands, ami = parse_income(desc)
    lines = flat.splitlines()
    address = row.get("address") or (lines[lines.index("Contact") + 1] if "Contact" in lines else None)
    unit_no = row.get("unit")
    bed_line = next((ln for ln in lines if re.search(r"bedroom|studio", ln, re.I)), "")
    size = parse.size(row["beds"]) if row.get("beds") else parse.size(bed_line)
    rent = row.get("price")
    if rent is None:
        m = re.search(r"\n\$([\d,]+)\n", flat)
        rent = parse.money(m.group(1)) if m else None
    seg = [p for p in url.rstrip("/").split("/") if p]
    boro_seg = unquote_plus(seg[-1]) if len(seg) >= 2 and "details" in seg else ""
    borough = parse.borough(boro_seg) or parse.borough(address)
    # agent: the "Licensed ..." line is preceded by the name and followed by a phone
    agent = ""
    for k, ln in enumerate(lines):
        if ln.lower().startswith("licensed") and k > 0:
            phone = next((x for x in lines[k + 1 : k + 3] if PHONE_RE.search(x)), "")
            agent = f"{lines[k - 1].title()} {phone}".strip()
            break
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", desc)))
    contact = ", ".join(x for x in [agent, *emails] if x)
    return Listing(
        source="mns",
        ext_id=seg[seg.index("details") + 1] if "details" in seg else url,
        title=f"{address} #{unit_no}" if unit_no else (address or url),
        url=url,
        address=address,
        borough=borough,
        units=[Unit(size=size, rent=rent, ami=ami, unit_no=unit_no, bands=bands)],
        senior_only=bool(re.search(r"\bseniors?\b|62\+|62 (years|and older)", desc, re.I)),
        notes=f"No online application: contact the listing agent {contact or '(see page)'} or MNS {OFFICE}",
    )


def _from_dict(d: dict) -> Listing:
    units = [Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]}) for u in d["units"]]
    return Listing(**{**d, "units": units})


class MNS(Adapter):
    name = "mns"
    title = "MNS Real Estate (affordable units)"
    url = URL
    every_hours = 12
    min_listings = 1  # the page Snapshot is always returned; a failed fetch or layout change raises

    def fetch(self) -> Result:
        body = fetch.html(URL)
        rows = parse_index(body)
        cache: dict = self.cache.setdefault("details", {})
        out = []
        for r in rows:
            fp = f"{r['unit']}|{r['price']}|{r['beds']}"
            hit = cache.get(r["url"])
            if hit and hit["fp"] == fp:
                out.append(_from_dict(hit["listing"]))
                continue
            lst = parse_detail(r["url"], fetch.html(r["url"]), r)
            cache[r["url"]] = {"fp": fp, "listing": asdict(lst)}
            out.append(lst)
        live = {r["url"] for r in rows}
        for u in [u for u in cache if u not in live]:
            del cache[u]
        snap = Snapshot(source=self.name, ext_id=URL, url=URL, text=table_text(body), links=sorted(live))
        return Result(listings=out, snapshots=[snap])


def table_text(html: str) -> str:
    """The availability table and building filter only (the rest is menus and forms)."""
    t = parse.text(html)
    a, b = t.find("Neighborhood"), t.find("I am a Renter")
    return t[a:b] if a >= 0 and b > a else t
