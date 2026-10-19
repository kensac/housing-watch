"""Sterling23 (Astoria brokerage): 421-a / 485-x rent-stabilized re-rentals, first come first served.

The page sits behind a Cloudflare JS challenge, so it is rendered once through byparr; the same HTML
feeds both the structured parse and the index Snapshot. Each listing is a card: a status badge
("Available"), an <h2> title, an address line, then per AMI tier an <h4> "130% Area Median Income" and a
table whose unit-type/rent cells span the household-size rows. The StreetEasy button points at an
unrelated Astoria unit and is ignored.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Listing, Result, Snapshot, Unit
from . import _page
from ._page import PageAdapter

URL = "https://www.sterling23.com/?page=affordable"
HOW = (
    "Apply: HC2.0 PDF application ({pdf}); email info@sterling23.com, fax (718) 204-7460, or mail/drop-off "
    "Sterling23, 28-15 23rd Ave, Astoria NY 11105. One application per household. Questions 718-626-3400."
)
_BADGES = {"available", "rented", "leased", "unavailable", "closed", "pending", "waitlist", "coming soon"}


def _badge(el) -> str | None:
    t = el.get_text(" ", strip=True).lower()
    return t if t in _BADGES else None


def parse_page(html: str) -> list[Listing]:
    s = parse.soup(html)
    out: list[Listing] = []
    cur: dict | None = None
    badge: str | None = None
    for el in s.find_all(["div", "span", "h2", "h3", "h4", "p", "table", "a"]):
        if el.name in ("div", "span") and not el.find(["div", "h2", "table"]):
            b = _badge(el)
            if b:
                badge = b
            continue
        if el.name == "h2":
            title = el.get_text(" ", strip=True)
            if re.search(r"re-?rental|affordable housing", title, re.I) or not re.search(r"\d", title):
                continue
            nxt = el.find_next_sibling("p")
            cur = {"title": title, "addr": nxt.get_text(" ", strip=True) if nxt else title, "badge": badge}
            cur.update(units=[], ami=None, unit_no=None, apply=None)
            out.append(cur)  # type: ignore[arg-type]
            badge = None
            continue
        if cur is None:
            continue
        txt = el.get_text(" ", strip=True)
        if el.name == "h3":
            m = re.search(r"\b(?:apt|unit)\.?\s*#?\s*([0-9]+[a-z]?|[a-z][0-9]+)\b", txt, re.I)
            if m:
                cur["unit_no"] = m.group(1).upper()
        elif el.name == "h4" and parse.ami(txt):
            cur["ami"] = parse.ami(txt)
        elif el.name == "table" and re.search(r"household", txt, re.I):
            size_txt = rent = None
            unit: Unit | None = None
            for tr in el.find_all("tr"):
                cells = [re.sub(r"\s+", " ", c.get_text(" ")).strip() for c in tr.find_all("td")]
                if not cells:
                    continue
                if len(cells) >= 4:
                    size_txt, rent = cells[0], parse.money(cells[1])
                    unit = Unit(size=parse.size(size_txt), rent=rent, ami=cur["ami"], unit_no=cur["unit_no"])
                    unit.set_aside = _set_aside(" ".join(cells))
                    cur["units"].append(unit)
                if unit is None or len(cells) < 2:
                    continue
                unit.bands += parse.bands_from_row(cells[-2], cells[-1], cur["ami"])
        elif el.name == "a" and txt.lower() == "apply" and el.get("href", "#") != "#":
            cur["apply"] = urljoin(URL, el["href"])
    listings = []
    for c in out:
        closed = c["badge"] in {"rented", "leased", "unavailable", "closed"}
        if closed:
            for u in c["units"]:
                u.available = False
        addr = c["addr"]
        listings.append(
            Listing(
                source="sterling23",
                ext_id=re.sub(r"[^a-z0-9]+", "-", (addr + " " + (c["unit_no"] or "")).lower()).strip("-"),
                title=c["title"] + (f" Apt {c['unit_no']}" if c["unit_no"] else ""),
                url=URL,
                address=addr,
                borough=parse.borough(addr, c["title"]),
                status="closed" if closed else ("waitlist" if c["badge"] == "waitlist" else "open"),
                units=c["units"],
                notes=HOW.format(pdf=c["apply"] or "Apply button"),
            )
        )
    return listings


def _set_aside(text: str) -> str | None:
    m = re.search(r"\b(mobility|hearing|visual|senior|62\+|m/?i)\b", text, re.I)
    if not m:
        return None
    w = m.group(1).lower()
    return {"62+": "senior", "mi": "mobility", "m/i": "mobility"}.get(w, w)


def snapshot(adapter: PageAdapter, url: str, body: str) -> Snapshot:
    """PageAdapter's snapshot logic for HTML already in hand (avoids a second Cloudflare render)."""
    pats = [re.compile(p, re.I) for p in _page.COMMON_IGNORE + adapter.ignore]
    s = parse.soup(body)
    links = set()
    for a in s.find_all("a", href=True):
        href = urljoin(url, a["href"].strip())
        if _page.DOC_RE.search(href) or (adapter.link_re and adapter.link_re.search(href)):
            links.add(href.split("#")[0])
    txt = "\n".join(ln for ln in parse.text(s).splitlines() if not any(p.fullmatch(ln.strip()) for p in pats))
    if len(txt) < adapter.min_text:
        raise RuntimeError(f"{url}: only {len(txt)} chars of text (blocked or JS not rendered?)")
    return Snapshot(source=adapter.name, ext_id=url, url=url, text=txt, links=sorted(links))


class Sterling23(PageAdapter):
    name = "sterling23"
    title = "Sterling23"
    url = URL
    pages = [URL]
    render = True
    every_hours = 4
    min_listings = 1
    ignore = [r"please check the captcha\.?"]

    def fetch(self) -> Result:
        body = fetch.rendered(URL, wait_ms=6000)
        snap = snapshot(self, URL, body)
        if "Affordable Housing Re-Rental" not in snap.text:
            raise RuntimeError("sterling23: affordable page not rendered (Cloudflare challenge?)")
        return Result(listings=parse_page(body), snapshots=[snap])
