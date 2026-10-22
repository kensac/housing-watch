"""AffordableHousing.com: the "Income Restricted" filter for the five NYC counties.

The site's search pages are server-rendered ASP.NET (42 cards a page, `/page-N/`). The UI's filters go
through `/v4/AjaxHandler*`, which robots.txt disallows, but `/<county>/income-restricted/` renders the
same IncomeRestricted filter server-side and is allowed. Everything else on the site (~3,000 NYC
listings) is market-rate or voucher-oriented and is deliberately not collected.

Cards give rent, beds, availability and address; the detail page adds the free-text description (where
owners type AMI % and income limits), the Age Restrictions flag and per-model rows for buildings. A
detail page is re-read only when its card changes or the cached copy is a week old.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from .. import ami, fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

BASE = "https://www.affordablehousing.com"
COUNTIES = ["new-york-county-ny", "kings-county-ny", "queens-county-ny", "bronx-county-ny", "richmond-county-ny"]
PER_PAGE = 42
MAX_AGE = timedelta(days=7)

_AMI = re.compile(
    r"(\d{2,3})\s*%\s*(?:of\s+)?(?:the\s+)?(?:AMI|area median)|\bAMI\s*(?:of\s*|at\s*|:\s*)?(\d{2,3})\s*%", re.I
)
_PER_HH = re.compile(
    r"\$\s*(\d[\d,]{3,}(?:\.\d+)?)\s*(?:for\s+(?:a\s+)?)?(\d{1,2})\s*-?\s*(?:ppl|people|persons?|pp)\b", re.I
)
_PER_HH_REV = re.compile(r"\b(\d{1,2})\s*-?\s*(?:ppl|people|persons?)\s*[:=–-]\s*\$\s*(\d[\d,]{3,}(?:\.\d+)?)", re.I)
_RANGE = re.compile(r"min\.?\s*[-–/]?\s*max\.?[^$\d]{0,20}(\$?\s*\d[\d,.]*\s*[-–]\s*\$?\s*\d[\d,.]*)", re.I)
_SET_ASIDES = [
    (r"\bmobility\b|hearing[- ]impair|visual(ly)?[- ]impair|vision[- ]impair|\bdisabilit(y|ies)\b", "disability"),
    (
        r"(section[ -]?8|vouchers?|city ?fheps|hasa|nycha)\s*(holders?\s*)?only\b|"
        r"\bonly\s*(accept(s|ing)?\s*)?(section[ -]?8|vouchers?|city ?fheps|hasa)|"
        r"must have (a )?(section[ -]?8|voucher)",
        "voucher only",
    ),
    (r"\bshelter\b|master lease", "shelter / master lease"),
]
_SENIOR = re.compile(r"\bseniors?\b|\b62\s*(\+|years|and older|or older)|\belderly\b", re.I)


def _tx(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ")).strip() if el is not None else ""


def parse_search(html: str) -> tuple[int, list[dict]]:
    """A county results page -> (total the site reports, cards on this page)."""
    s = parse.soup(html)
    head = _tx(s.find(id="MainContentPlaceHolder_lblHeading"))
    if "income restricted" not in head.lower():
        raise RuntimeError(f"income-restricted filter not applied (heading {head!r})")
    total = parse.whole(_tx(s.find(id="MainContentPlaceHolder_RentalsCount"))) or 0
    cards = []
    # cards hold unbalanced markup, so lxml nests every later card inside the first: parse each alone
    for chunk in re.findall(r"<article\b.*?</article>", html, re.S):
        a = parse.soup(chunk).find("article")
        link = a.select_one("a.listing-card--stretched-link[href]") if a is not None else None
        if link is None or not a.get("data-communityid"):
            continue

        def g(sel, a=a):
            return _tx(a.select_one(sel))

        cards.append(
            {
                "id": a["data-communityid"],
                "url": link["href"].split("?")[0],
                "price": g("[class$='card--price']"),
                "avail": g(".available--txt"),
                "details": g("[class$='card--details']"),
                "address": g(".tnresult--propertyaddress"),
                "comment": g(".property--comment"),
            }
        )
    return total, cards


def _stamp(card: dict) -> str:
    raw = "|".join(card[k] for k in ("url", "price", "avail", "details", "address", "comment"))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _ami(text: str) -> int | None:
    m = _AMI.search(text or "")
    return int(m.group(1) or m.group(2)) if m else None


def _bands(text: str, pct: int | None) -> list[Band]:
    """Owner free text -> Bands. Per-size caps when listed; a lone min-max range is read as 1 person's."""
    caps: dict[int, int] = {}
    for amt, hh in _PER_HH.findall(text):
        caps.setdefault(int(hh), parse.whole(amt))
    for hh, amt in _PER_HH_REV.findall(text):
        caps.setdefault(int(hh), parse.whole(amt))
    lo = hi = None
    m = _RANGE.search(text)
    if m:
        lo, hi = parse.money_range(m.group(1))
    if caps:
        return [Band(h, lo, caps[h]) for h in sorted(caps)]
    if lo is None and hi is None:
        return []
    # The range's top is the cap for the largest household it covers, so it bounds 1 person's cap from
    # above (never wrongly excludes); with an AMI % the 1-person cap can be computed exactly.
    cap1 = ami.cap(pct, 1)
    if hi is not None and cap1 is not None:
        hi = min(hi, cap1)
    return [Band(1, lo, hi)]


def _set_aside(text: str) -> str | None:
    for pat, name in _SET_ASIDES:
        if re.search(pat, text or "", re.I):
            return name
    return None


def _available(avail: str) -> bool | None:
    return False if re.search(r"not available|unavailable|rented|leased", avail or "", re.I) else None


def parse_detail(card: dict, html: str) -> Listing | None:
    """Card + detail page -> Listing; None when the page no longer says it is income restricted."""
    s = parse.soup(html)
    flags = dict(re.findall(r"serverSideVariables\.(\w+)\(\"([^\"]*)\"\)", html))
    m = re.search(r"isIncomeRestricted\((true|false)\)", html)
    if m and m.group(1) == "false":
        return None
    opts = {_tx(li.find("small")).lower(): _tx(li.find("span")) for li in s.select("ul.prop--dtls--opt li")}
    age_restricted = opts.get("age restrictions", "").lower() == "yes"
    ctitle = _tx(s.find(id="communityTitle"))
    desc = _tx(s.find(id="communityDescription"))
    building_text = f"{card['comment']} {ctitle} {desc}"
    building_ami = _ami(building_text)

    units: list[Unit] = []
    for row in s.select("div.unit--row.CommunityModelRow"):
        cols = row.find_all("div", class_="unit--col", recursive=False)
        model = re.sub(r"^Model:\s*", "", _tx(row.select_one(".model-text")) or "")
        mid = re.search(r"ShowViewModel\((\d+)\)", str(row))
        modal = s.select_one(f"div.unitmodel_{mid.group(1)}") if mid else None
        mtext = _tx(modal.select_one(".unit--info--desc--text")) if modal else ""
        rent_col = next((c for c in cols if "rent--range--col" in c.get("class", [])), None)
        avail_col = next((c for c in cols if "avl--col" in c.get("class", [])), None)
        size = parse.size(model)
        if size == "unknown":
            size = parse.size(row.get("data-bedroomcount"))
        a = _ami(mtext) or building_ami
        units.append(
            Unit(
                size=size,
                rent=parse.money(_tx(rent_col)) if rent_col is not None and "$" in _tx(rent_col) else None,
                ami=a,
                bands=_bands(mtext, a),
                set_aside=_set_aside(f"{model} {mtext} {building_text}"),
                available=_available(_tx(avail_col)),
            )
        )
    avails = [card["avail"]]
    if not units:
        beds = card["details"].split("|")[0] or _tx(s.find(id="spanBedCount"))
        rent_txt = _tx(s.find(id="spanRentRange")) or card["price"]
        parts = [p.strip() for p in card["address"].split(",")]
        units.append(
            Unit(
                size=parse.size(beds),
                rent=parse.money(rent_txt) if "$" in rent_txt and "-" not in rent_txt else None,
                ami=building_ami,
                unit_no=parts[1] if len(parts) >= 4 else None,
                bands=_bands(building_text, building_ami),
                set_aside=_set_aside(building_text),
                available=_available(_tx(s.find(id="spanAvailable")) or card["avail"]),
            )
        )
    else:
        avails = [_tx(c) for c in s.select("div.unit--row.CommunityModelRow div.avl--col")] or avails

    if flags.get("listingStatusCode", "active") != "active":
        status = "closed"
    elif all(u.available is False for u in units):
        status = "closed"
    elif any("waiting list" in a.lower() for a in avails) and not any("available" in a.lower() for a in avails):
        status = "waitlist"
    else:
        status = "open"
    name = (flags.get("communityName") or "").strip()
    title = f"{name} ({card['address']})" if name else card["address"]
    county = flags.get("proposedCounty", "")
    borough = parse.borough(card["address"]) or {
        "Kings": "Brooklyn",
        "Richmond": "Staten Island",
        "New York": "Manhattan",
    }.get(county, county or None)
    note = " ".join(x for x in (ctitle, desc) if x)
    note = (note[:400] + ("…" if len(note) > 400 else "")) if note else ""
    if not any(u.bands or u.ami for u in units):
        # the page's "Income restrictions" block is a hidden placeholder ($500 - $1000 for every listing)
        note = (note + " " if note else "") + "The listing publishes no income limits or AMI."
    return Listing(
        source="affordablehousing_com",
        ext_id=card["id"],
        title=title,
        url=card["url"],
        address=card["address"],
        borough=borough,
        status=status,
        kind="waitlist" if status == "waitlist" else "re-rental",
        units=units,
        senior_only=age_restricted or bool(_SENIOR.search(building_text)),
        notes=note or None,
    )


def _from_dict(d: dict) -> Listing:
    units = [Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]}) for u in d["units"]]
    return Listing(**{**d, "units": units})


class AffordableHousingCom(Adapter):
    name = "affordablehousing_com"
    title = "AffordableHousing.com (income restricted, NYC)"
    url = f"{BASE}/kings-county-ny/income-restricted/"
    min_listings = 10  # 34 across the five counties in Oct 2026

    def fetch(self) -> Result:
        cards: dict[str, dict] = {}
        for county in COUNTIES:
            first = f"{BASE}/{county}/income-restricted/"
            total, page_cards = parse_search(fetch.html(first))
            got = {c["id"]: c for c in page_cards}
            for n in range(2, math.ceil(total / PER_PAGE) + 1):
                _, more = parse_search(fetch.html(f"{first}page-{n}/"))
                if not more:
                    break
                got.update({c["id"]: c for c in more})
            if total and not got:
                raise RuntimeError(f"{county}: site reports {total} rentals but no cards parsed")
            cards.update(got)

        cache: dict = self.cache.setdefault("detail", {})
        now = datetime.now(UTC)
        out = []
        for cid, card in cards.items():
            stamp = _stamp(card)
            hit = cache.get(cid)
            if hit and hit["stamp"] == stamp and now - datetime.fromisoformat(hit["fetched"]) < MAX_AGE:
                lst = _from_dict(hit["listing"]) if hit["listing"] else None
            else:
                lst = parse_detail(card, fetch.html(card["url"]))
                cache[cid] = {"stamp": stamp, "fetched": now.isoformat(), "listing": asdict(lst) if lst else None}
            if lst:
                out.append(lst)
        for cid in [c for c in cache if c not in cards]:
            del cache[cid]
        return Result(listings=out)
