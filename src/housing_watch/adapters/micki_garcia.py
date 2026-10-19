"""Micki Garcia Realty (Wix): a portfolio of HPD projects plus homeownership lotteries.

The portfolio grid has a "Show More" button, so its HTML only carries the first 12 projects. The full
list is the gallery in the page's own Wix JSON (static.wixstatic.com/sites/<pageJsonFileName>.json.z),
whose file name and every page's URL slug are in the site's page map embedded in any page. Slugs are
misleading ("copy-of-550-dekalb-ave" is 55 Carlton), so items are followed by page id, never by slug.
Each project page has a WHAT / WHERE / WHEN block: "Complete/Leased" = closed. Rental pages publish no
rent/AMI/income; the homeownership offerings have income-requirement pages with per-unit tables.
"""

from __future__ import annotations

import re

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from ._page import PageAdapter

BASE = "https://www.mickigarciarealty.com"
URL = f"{BASE}/portfolio"
SALES = f"{BASE}/nme3"
PAGE_JSON = "https://static.wixstatic.com/sites/{file}.json.z?v=3"
HOW_RENT = "Contact Us for an Application: 212-694-3893, micki@mickigarciarealty.com, 1355 Park Ave, New York NY 10029"
HOW_OWN = f"Homeownership (first-time buyers): interest form {BASE}/nmeapplication"
WHERE = {
    "harlem": "Manhattan",
    "east harlem": "Manhattan",
    "washington heights": "Manhattan",
    "inwood": "Manhattan",
    "williamsburg": "Brooklyn",
    "bushwick": "Brooklyn",
    "bed-stuy": "Brooklyn",
    "bedford-stuyvesant": "Brooklyn",
    "crown heights": "Brooklyn",
    "fort greene": "Brooklyn",
    "clinton hill": "Brooklyn",
    "south bronx": "Bronx",
    "forrest hills": "Queens",
    "forest hills": "Queens",
    "rego park": "Queens",
    "navy yard": "Brooklyn",
}


def page_map(html: str) -> dict[str, dict]:
    """pageId -> {title, slug, file} from the Wix page map embedded in every page."""
    out = {}
    for m in re.finditer(
        r'"([a-z0-9]{5})":\{"pageId":"\1","title":"([^"]*)","pageUriSEO":"([^"]*)","pageJsonFileName":"([^"]+)"', html
    ):
        out[m.group(1)] = {"title": m.group(2).strip(), "slug": m.group(3), "file": m.group(4)}
    return out


def gallery(page_json: dict, pages: dict[str, dict]) -> list[tuple[str, str]]:
    """Portfolio page JSON -> [(project title, page url)] in grid order (all items, past "Show More")."""
    data = page_json["data"]["document_data"]
    lists = [v for v in data.values() if v.get("type") == "ImageList" and len(v.get("items", [])) > 1]
    if not lists:
        raise RuntimeError("micki_garcia: portfolio gallery not found in page JSON")
    out = []
    for ref in max(lists, key=lambda v: len(v["items"]))["items"]:
        item = data.get(ref.lstrip("#"), {})
        link = data.get(str(item.get("link") or "").lstrip("#"), {})
        pid = str(link.get("pageId") or "").lstrip("#")
        if not item.get("title") or pid not in pages:
            continue
        out.append((item["title"].strip(), f"{BASE}/{pages[pid]['slug']}"))
    return out


def _field(lines: list[str], name: str) -> str | None:
    i = next((k for k, ln in enumerate(lines) if ln.strip().lower() == name), None)
    return lines[i + 1].strip() if i is not None and i + 1 < len(lines) else None


def parse_project(title: str, url: str, html: str) -> Listing:
    lines = parse.text(html).splitlines()
    what, where, when = (_field(lines, k) for k in ("what", "where", "when"))
    if when is None:
        raise RuntimeError(f"micki_garcia: no WHEN on {url}")
    closed = bool(re.search(r"complete|leased|sold|closed", when, re.I))
    own = bool(what and re.search(r"homeownership|ownership|sale", what, re.I))
    boro = parse.borough(where) or WHERE.get((where or "").lower()) or parse.borough(title)
    return Listing(
        source="micki_garcia",
        ext_id=url.rsplit("/", 1)[-1],
        title=title,
        url=url,
        address=title if re.match(r"\d", title) else None,
        borough=boro,
        status="closed" if closed else "open",
        kind="homeownership" if own else "re-rental",
        notes=f"{what or '?'}; WHEN: {when}. " + (HOW_OWN if own else HOW_RENT),
    )


_PEOPLE = re.compile(r"(\d+)\s*(?:person|people)\s*:\s*\$?([\d,]+)\s*[-–]\s*\$?([\d,]+)", re.I)


def _hood_borough(addr: str, context: str) -> str | None:
    """Borough from the neighborhood printed next to the address on the sales page ("... Harlem, NY")."""
    street = re.sub(r"^\S+\s+", "", addr).strip()
    m = re.search(re.escape(street) + r"(.{0,60})", context, re.I | re.S)
    near = (m.group(1) if m else "").lower()
    return parse.borough(near) or next((b for k, b in WHERE.items() if k in near), None)


def parse_income_page(url: str, html: str, context: str = "") -> Listing | None:
    """A homeownership "INCOME REQUIREMENTS" page -> one Listing, a Unit per apartment (no rent: a sale).

    `context` is the sales page text, which names each building's neighborhood."""
    lines = parse.text(html).splitlines()
    i = next((k for k, ln in enumerate(lines) if ln.strip().upper() == "INCOME REQUIREMENTS"), None)
    if i is None:
        return None
    addr = lines[i + 1].strip()
    units, prices = [], []
    for cells in parse.table_rows(html):
        m = re.match(r"\s*([0-9]+[A-Z]?)\s*\(([^)]*)\)", cells[0])
        if not m:
            continue
        bands = [Band(int(h), parse.whole(lo), parse.whole(hi)) for h, lo, hi in _PEOPLE.findall(" ".join(cells))]
        units.append(Unit(size=parse.size(m.group(2)), unit_no=m.group(1), bands=bands))
        if len(cells) > 1 and parse.money(cells[1]):
            prices.append(int(parse.money(cells[1])))  # type: ignore[arg-type]
    price = f" Prices ${min(prices):,}-${max(prices):,}." if prices else ""
    return Listing(
        source="micki_garcia",
        ext_id=url.rsplit("/", 1)[-1],
        title=f"{addr} (homeownership)",
        url=url,
        address=addr,
        borough=parse.borough(addr) or _hood_borough(addr, context),
        status="open",
        kind="homeownership",
        units=units,
        notes=f"{HOW_OWN}.{price}",
    )


class MickiGarcia(PageAdapter):
    name = "micki_garcia"
    title = "Micki Garcia Realty"
    url = URL
    pages = [URL, SALES]
    every_hours = 24
    min_listings = 1
    ignore = [r"press to zoom", r"\d+/\d+"]

    def fetch(self) -> Result:
        snaps = super().fetch().snapshots
        home = fetch.html(URL)
        pages = page_map(home)
        portfolio = next((p for p in pages.values() if p["slug"] == "portfolio"), None)
        if portfolio is None:
            raise RuntimeError("micki_garcia: page map has no portfolio page")
        items = gallery(fetch.json(PAGE_JSON.format(file=portfolio["file"])), pages)
        if len(items) < 12:
            raise RuntimeError(f"micki_garcia: only {len(items)} portfolio items")
        listings = [parse_project(t, u, fetch.html(u)) for t, u in items]
        sales = parse.text(fetch.html(SALES))
        for p in pages.values():
            if re.search(r"income requirements", p["title"], re.I):
                u = f"{BASE}/{p['slug']}"
                lst = parse_income_page(u, fetch.html(u), sales)
                if lst:
                    listings.append(lst)
        return Result(listings=listings, snapshots=snaps)
