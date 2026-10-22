"""Taxace NY: a Wix page of per-unit cards, first come first served, each with its own ClickUp apply form.

The card's "Min Income: $min - $max" is NOT per household size: $max is the cap of the largest household
the unit allows. It is matched against HUD's 2026 and 2025 NYC AMI charts to find that household size and
AMI %, then each smaller household gets its own cap (2025-chart ads cap 1 person at $147,420, below the
2026 $154,440). A card can stay up after its form is deleted, so each form is checked through ClickUp's
public form API (403 "Form is not active" = closed), cached for a few hours per form.
Borough labels are sloppy (Bronx streets labelled Brooklyn), so known Bronx streets override them.
"""

from __future__ import annotations

import re
import time

from .. import ami, fetch, parse
from ..models import Band, Listing, Result, Snapshot, Unit
from . import Adapter

URL = "https://www.taxaceny.com/projects-8"
CONTACT = "Info@taxaceny.com, 718-908-7003"
FORM_API = "https://frontdoor-prod-us-east-2-1.clickup.com/ui/v3/workspaces/{team}/forms/{form}?token={token}"
FORM_TTL = 6 * 3600

# smallest / largest household HPD lets into each size (studio 69 Amboy 4B prints a 3-person cap)
HH = {"studio": (1, 3), "1br": (1, 3), "2br": (2, 5), "3br": (3, 7), "unknown": (1, 8)}
# streets that exist only in the Bronx but appear labelled "Brooklyn" (2775 Reservoir Ave, 2710 Decatur Ave)
BRONX_STREETS = re.compile(r"\b(reservoir|decatur)\s+ave", re.I)
_BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li"}
_TITLE = re.compile(r"^(.+?)\.?\s+Unit\s+([\w-]+)\.?$", re.I)


def bands_for(size: str, income: str) -> tuple[list[Band], int | None, int | None]:
    """(bands, ami %, chart year) for one card's 'Min Income' text."""
    lo, hi = parse.money_range(income)
    lo_h, top_h = HH.get(size, HH["unknown"])
    m = ami.match(hi, sizes=range(top_h, lo_h - 1, -1)) if hi else None
    if m is None:  # unknown chart: scale from the largest household HPD allows
        return ami.bands(range(lo_h, top_h + 1), lo, hi)[0], None, None
    year, h, pct = m
    return ami.bands(range(lo_h, h + 1), lo, hi, pct)[0], pct, year


def _stream(html: str) -> list[tuple[str, str]]:
    s = parse.soup(html)
    for t in s(["script", "style", "noscript", "template"]):
        t.decompose()
    out: list[tuple[str, str]] = []
    for el in s.find_all(True):
        if el.name == "a" and "forms.clickup.com" in el.get("href", ""):
            out.append(("a", el["href"]))
        elif el.name in _BLOCKS and not el.find(list(_BLOCKS)):
            t = re.sub(r"\s+", " ", el.get_text("")).replace("​", "").strip()
            if t:
                out.append(("t", t))
    return out


def _field(body: list[str], key: str) -> str:
    return next((b.split(":", 1)[1].strip() for b in body if b.lower().startswith(key.lower() + ":")), "")


def parse_page(html: str, forms: dict[str, bool] | None = None) -> tuple[list[Listing], str]:
    """`forms`: form url -> active?; a url missing from it is treated as active (not checked)."""
    forms = forms or {}
    items = _stream(html)
    flat = "\n".join(v for k, v in items if k == "t")
    starts = [i for i, (k, v) in enumerate(items) if k == "t" and _TITLE.match(v)]
    first = starts[0] if starts else 0
    stop = next((i for i, (k, v) in enumerate(items) if i > first and k == "t" and v == "CONTACT US"), len(items))
    out: list[Listing] = []
    for n, i in enumerate(starts):
        region = items[i : starts[n + 1] if n + 1 < len(starts) else stop]
        texts = [v for k, v in region if k == "t"]
        form = next((v for k, v in region if k == "a"), None)
        m = _TITLE.match(texts[0])
        street, unit_no = m.group(1).strip(), m.group(2).upper()
        city = re.sub(r"\s+,", ",", texts[1]) if len(texts) > 1 else ""
        body = texts[2:]
        size = parse.size(_field(body, "Unit Size"))
        bands, pct, year = bands_for(size, _field(body, "Min Income"))
        active = forms.get(form, True) if form else True
        unit = Unit(
            size=size,
            rent=parse.money(_field(body, "Rent")),
            ami=pct,
            unit_no=unit_no,
            bands=bands,
            available=None if active else False,
            chart_year=year,
        )
        notes = [f"Apply: ClickUp form {form} (ID upload for all adults required)" if form else "Apply: no form link"]
        if not active:
            notes.append("Form is unavailable (deleted) although the card is still up.")
        if year and year < ami.CURRENT_YEAR:
            notes.append(f"Income caps follow the {year} HUD chart (1 person ${bands[0].income_max:,}); ask {CONTACT}.")
        utilities = _field(body, "Utilities")
        if utilities:
            notes.append(f"Utilities: {utilities}.")
        notes.append(f"Contact: {CONTACT}.")
        borough = "Bronx" if BRONX_STREETS.search(street) else parse.borough(city)
        out.append(
            Listing(
                source="taxace",
                ext_id=re.sub(r"[^a-z0-9]+", "-", f"{street} {unit_no}".lower()).strip("-"),
                title=f"{street}, Unit {unit_no}",
                url=URL,
                address=f"{street}, {city}",
                borough=borough,
                status="open" if active else "closed",
                units=[unit],
                notes=" ".join(notes),
            )
        )
    return out, flat


def form_active(url: str) -> bool | None:
    """True/False from ClickUp's public form API; None when the answer is not a clear yes/no."""
    m = re.search(r"forms\.clickup\.com/(\d+)/f/([\w-]+)/(\w+)", url)
    if not m:
        return None
    api = FORM_API.format(team=m.group(1), form=m.group(2), token=m.group(3))
    try:
        r = fetch._client.get(api, headers={"Origin": "https://forms.clickup.com"})
    except Exception:  # noqa: BLE001 - an unanswered check leaves the unit open
        return None
    if r.status_code == 200 and '"form"' in r.text:
        return True
    if r.status_code in (403, 404) and re.search(r"not active|not found|deleted", r.text, re.I):
        return False
    return None


class Taxace(Adapter):
    name = "taxace"
    title = "Taxace NY"
    url = URL
    min_listings = 1

    def fetch(self) -> Result:
        html = fetch.html(URL)
        cache: dict = self.cache.setdefault("forms", {})
        now = time.time()
        forms: dict[str, bool] = {}
        unanswered = 0
        links = sorted(set(re.findall(r'href="(https://forms\.clickup\.com/[^"]+)"', html)))
        for u in links:
            hit = cache.get(u)
            if hit and now - hit["checked"] < FORM_TTL:
                forms[u] = hit["active"]
                continue
            fetch._polite(u)
            a = form_active(u)
            if a is None:
                unanswered += 1
                continue
            forms[u] = a
            cache[u] = {"active": a, "checked": now}
        if links and unanswered == len(links):
            raise RuntimeError("ClickUp form API answered no form check (API changed?)")
        for u in [u for u in cache if u not in links]:
            del cache[u]
        listings, flat = parse_page(html, forms)
        if listings:
            return Result(listings=listings)
        if "Vacant Available Units" not in flat:
            raise RuntimeError(f"{URL}: page did not load as expected ({len(flat)} chars)")
        return Result(snapshots=[Snapshot(source=self.name, ext_id=URL, url=URL, text=flat)])
