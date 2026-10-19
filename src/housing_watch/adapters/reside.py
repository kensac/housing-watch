"""Reside New York: ~2,100 WordPress property pages, listed with lastmod in two sitemaps.

Only pages whose lastmod moved (or parsed by an older PARSER) are re-fetched (cache: url -> {lastmod, v,
listing}). Status and city come from the WordPress terms on the page's <article>, else the label right before
the title. Units come from the page's table; without one, from the header (household range, required income,
rent) every page prints, with sizes taken from the copy and floor-plan images. Applying is one shared Airtable form.
"""

from __future__ import annotations

import re
from dataclasses import asdict

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

SITEMAPS = [f"https://residenewyork.com/wp-sitemap-posts-property-{i}.xml" for i in (1, 2)]
APPLY = "https://airtable.com/appsseXTOVx59HC0W/pagcVengefPFQvMZC/form"
PARSER = 2  # bump when parse_page changes, so cached listings are re-parsed rather than kept until lastmod moves


_W = "one two three four five six seven eight".split()
_HHW = r"\b([1-8]|" + "|".join(_W) + r")\b[\s-]*(?:persons?|people)"
_NUM = r"\$?\s*(\d[\d,]*(?:\.\d+)?)"
_RANGE = re.compile(_NUM + r"\s*\.?\s*[-–—]+\s*" + _NUM)
# HUD 2025 NYC 100% AMI; older ads print these (130% of 1 person = $147,420), newer ones parse.AMI100's
_AMI100_2025 = {1: 113_400, 2: 129_600, 3: 145_800, 4: 162_000, 5: 175_000, 6: 188_000, 7: 201_000, 8: 213_900}
_SIZES = re.compile(
    r"\b((?:studio|one|two|three|four|[0-4])(?:[\s-]*(?:,|&|and|or|/)[\s-]*(?:studio|one|two|three|four|[0-4]))*)"
    r"[\s-]*(?:bed(?:room)?s?|br|bd)\b|(?<!yoga )(?<!fitness )(?<!dance )(?<!art )\bstudios?\b",
    re.I,
)
_ASIDE = re.compile(r"\b(mi|mobility|hearing|visual|h/v|senior|62\+)\b", re.I)
_STATUS = {
    "open-market": "open",
    "open-to-applications": "open",
    "upcoming-project": "upcoming",
    "closed-project": "closed",
}


def _n(x: str) -> int:
    return int(float(x.replace(",", "")))


def _hh(text: str | None) -> list[int]:
    """'1 person' / '1-3' / '1-Persons 2-Persons' / 'Two People' -> household sizes."""
    t = text or ""
    if re.search(r"\d\s*[-–—]\s*\d", t):
        return parse.household_range(t)
    found = re.findall(_HHW, t, re.I)
    return [int(w) if w.isdigit() else _W.index(w.lower()) + 1 for w in found] or parse.household_range(t)


def _hh_for(cap: int, ami: int) -> int | None:
    """The household size whose HUD cap at this AMI % is `cap` (unlabeled 'Maximum Income' lists)."""
    for t in (parse.AMI100, _AMI100_2025):
        h = next((h for h, v in t.items() if abs(v * ami / 100 - cap) <= cap * 0.005), None)
        if h:
            return h
    return None


def _spread(hhs: list[int], lo: int, hi: int) -> list[Band]:
    """One range over several household sizes -> per-size caps, from the HUD table and whole AMI % its top cap is."""
    for h in hhs[::-1]:  # the stated top first; some headers print a smaller household's cap as the top
        for t in (parse.AMI100, _AMI100_2025):
            pct = round(hi / t.get(h, 10**9) * 20) * 5
            if pct >= 30 and abs(t[h] * pct / 100 - hi) <= 60:  # HUD rounds caps to $10-50
                return [Band(k, lo, hi if k >= h or k not in t else round(t[k] * pct / 1000) * 10) for k in hhs]
    top = hhs[-1]  # no whole % fits: scale by the 2026 table
    return [
        Band(k, lo, hi if k == top else round(hi * parse.AMI100.get(k, 0) / parse.AMI100.get(top, hi) / 10) * 10)
        for k in hhs
    ]


def _bands(hh_text: str | None, inc: str, ami: int | None, lo_col: str | None = None) -> list[Band]:
    """One table row (or the page header) -> Bands. lo_col is a separate 'Minimum Income' column."""
    lab = re.findall(_HHW + r"\W*?(\d[\d,]{3,})", inc, re.I)  # 'One Person $147,420 Two Person ...'
    if lo_col is not None:
        lo = parse.whole(lo_col)
        caps = [(lo, _n(c)) for c in ([c for _, c in lab] or re.findall(r"\d[\d,]{3,}(?:\.\d+)?", inc)) if _n(c) != lo]
    else:
        caps = [(_n(a), _n(b)) for a, b in _RANGE.findall(inc)]
        if len(caps) == 1 and len(_hh(hh_text)) > 1:  # one range over several household sizes
            return _spread(_hh(hh_text), *caps[0])
    hhs = [int(w) if w.isdigit() else _W.index(w.lower()) + 1 for w, _ in lab] or _hh(hh_text)
    if len(hhs) != len(caps):
        start = (ami and caps and _hh_for(caps[0][1], ami)) or 1
        hhs = list(range(start, start + len(caps)))
    return [Band(h, lo, hi) for h, (lo, hi) in zip(hhs, caps, strict=True)]


def _table_units(s) -> list[Unit]:
    """'Unit Size | Rent | AMI | Household Size | Income' tables; rows under a unit carry its other household sizes."""
    units: list[Unit] = []
    for t in s.find_all("table"):
        rows = [
            [re.sub(r"\s+", " ", c.get_text(" ")).strip() for c in tr.find_all(["td", "th"])] for tr in t.find_all("tr")
        ]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        head = [h.lower() for h in rows[0]]
        col = lambda pat, hs=head: next((i for i, h in enumerate(hs) if re.search(pat, h)), None)  # noqa: E731
        c_hh, c_lo = col(r"household|hh|composition"), col(r"^min(imum)?( income)?$")
        c_inc = next((i for i in range(len(head) - 1, -1, -1) if re.search(r"income|max", head[i])), len(head) - 1)
        mine: list[Unit] = []
        for cells in rows[1:]:
            if len(cells) == len(head) and re.search(r"studio|bed|br\b|sro", cells[0], re.I):
                a = next((parse.ami(c) for c in cells if parse.ami(c)), None)
                rent = next((parse.money(c) for c in cells[1:] if "$" in c), None)
                hh = (
                    cells[c_hh]
                    if c_hh is not None
                    else next((c for c in cells if re.search(r"person|people", c, re.I)), None)
                )
                lo = cells[c_lo] if c_lo is not None else None
                aside = "disability/senior" if _ASIDE.search(" ".join(cells)) else None
                bands = _bands(hh, cells[c_inc], a, lo)
                mine.append(Unit(size=parse.size(cells[0]), rent=rent, ami=a, bands=bands, set_aside=aside))
            elif mine and 2 <= len(cells) < len(head) and re.search(r"person|people|\d", cells[0], re.I):
                # rowspan continuation: the trailing columns (household size, income) for the unit above
                cols = head[len(head) - len(cells) :]
                hh = next(
                    (c for c, h in zip(cells, cols, strict=True) if re.search(r"household|hh|composition", h)), cells[0]
                )
                mine[-1].bands += _bands(hh, cells[-1], mine[-1].ami)
        units += mine
    return units


def _sizes(s, about: str, title: str) -> list[str]:
    """Unit sizes the page mentions: title, 'About this home' copy, floor-plan image names/alt text."""
    imgs = " ".join(
        f"{re.sub(r'[-_]+', ' ', (i.get('data-src') or i.get('src') or '').rsplit('/', 1)[-1])} {i.get('alt') or ''}"
        for i in s.select(
            ".reside-gallery img, .elementor-widget-theme-post-content img, .elementor-widget-text-editor img"
        )
    )
    # rendering file names credit an architect ('...-StudiosSC.jpg'): 'studio' counts only on floor plans
    imgs = (
        " ".join(w for w in imgs.split() if not re.fullmatch(r"studios?\w*", w, re.I))
        if not re.search(r"plan|layout|unit", imgs, re.I)
        else imgs
    )
    out: list[str] = []
    for m in _SIZES.finditer(f"{title} . {about} . {imgs}"):
        words = re.findall(r"studio|one|two|three|four|[0-4]", m.group(1), re.I) if m.group(1) else ["studio"]
        for w in words:
            sz = "studio" if w.lower() == "studio" else parse.size(f"{w} bedroom")
            if sz != "unknown" and sz not in out:
                out.append(sz)
    return out


def _grab(pat: str, text: str) -> str:
    m = re.search(pat, text)
    return m.group(1) if m else ""


def _unmangle(html: str) -> str:
    """Word-pasted tables arrive with curly quotes closing their attributes (wptexturize), which swallows every
    cell into an attribute value; straighten those quotes so the cells parse again."""
    if not re.search(r"&#82(?:20|21|43);>", html):
        return html
    html = re.sub(r"=&#82(?:20|21|43);", '="', html)
    return re.sub(r"&#82(?:20|21|43);(?=[\s>])", '"', html)


def parse_page(url: str, html: str) -> Listing:
    s = parse.soup(_unmangle(html))
    title = (s.find("meta", property="og:title") or {}).get("content") or (s.title.string if s.title else url)
    title = re.sub(r"\s*[-|–]\s*Reside New York.*$", "", title or "").strip()
    flat = re.sub(r"\s+", " ", parse.text(s))
    # WordPress terms on the property's <article>: property-status-*, property-city-* (borough or neighborhood)
    art = s.find(class_=re.compile(r"^property-(status|city)-"))
    cls = art.get("class", []) if art else []
    status = next((_STATUS[c[16:]] for c in cls if c[16:] in _STATUS and c.startswith("property-status-")), None)
    if status is None:
        # the page's own label sits right before its title; the site menu also lists two of the labels
        lab = re.search(r"(Open Market|Upcoming Project|Closed Project)\s+" + re.escape(title[:25]), flat)
        label = lab.group(1) if lab else ""
        status = "upcoming" if "Upcoming" in label else "closed" if "Closed" in label else "open"
    # header facts every page prints: Rent/ Month, Available Units, Household Size, Required Income
    box = {}
    for b in s.select(".elementor-icon-box-content"):
        k, v = b.select_one(".elementor-icon-box-title"), b.select_one(".elementor-icon-box-description")
        if k and v:
            box[re.sub(r"\s+", " ", k.get_text()).strip()] = re.sub(r"\s+", " ", v.get_text(" ")).strip()
    about = _grab(r"About this home(.*?)(?:Features & Amenities|Would you like to find out more)", flat)
    sizes = _sizes(s, about, title)
    units = _table_units(s)
    m = re.search(r"unit[-\s]*(?:apt[-\s]*)?([0-9]+[a-z]?|[a-z][0-9]+)\b", url.rstrip("/").split("/")[-1], re.I)
    if m and len(units) == 1:
        units[0].unit_no = m.group(1).upper()
    rents = [_n(x) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", box.get("Rent/ Month", "")) if _n(x) >= 100]
    hh, inc, avail = box.get("Household Size"), box.get("Required Income", ""), box.get("Available Units")
    notes = []
    if not units:
        # no table: the header's household/income range is the page's whole eligibility ad (its own checker uses it)
        bands = _bands(hh, inc, None) if hh else []
        if bands or rents:
            units = [Unit(size=sz, rent=rents[0] if rents else None, bands=list(bands)) for sz in sizes or ["unknown"]]
            notes.append("no unit table: header range for the whole building" if bands else "page publishes only rent")
        else:
            notes.append("page publishes no unit sizes, rent or income ('Contact us for price')")
        if inc and not bands:
            notes.append(f"required income {inc}")
    if len(rents) > 1:
        notes.append(f"rent ${min(rents):,}-${max(rents):,}")
    if avail:
        notes.append(avail.lower() if "unit" in avail.lower() else f"{avail} units available")
    notes.append(f"Apply: Airtable form {APPLY} (one unit per submission)")
    # borough: the address's zip, then the title's, then the city terms (only if they name one borough), then names
    addr = _grab(r"Property Address (.*?)(?:\[reside_auto|Three Simple Steps)", flat)
    zips = lambda t: " ".join(re.findall(r"\b1[01]\d{3}\b", t))  # noqa: E731
    terms = {parse.borough(c[14:].replace("-", " ")) for c in cls if c.startswith("property-city-")} - {None}
    borough = (
        parse.borough(zips(addr))
        or parse.borough(zips(title))
        or (terms.pop() if len(terms) == 1 else None)
        or parse.borough(addr)
        or parse.borough(title)
    )
    return Listing(
        source="reside",
        ext_id=url.rstrip("/").split("/")[-1],
        title=title,
        url=url,
        address=re.sub(r",?\s*(Apartment|Apt\.?|Unit)\b.*$", "", title, flags=re.I),
        borough=borough,
        status=status,
        senior_only=bool(re.search(r"\bsenior\b|62 (years|and older)", flat[:4000], re.I)),
        units=units,
        notes="; ".join(notes),
    )


def _from_dict(d: dict) -> Listing:
    units = [Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]}) for u in d["units"]]
    return Listing(**{**d, "units": units})


class Reside(Adapter):
    name = "reside"
    title = "Reside New York"
    url = "https://residenewyork.com/property-status/open-market/"
    every_hours = 3
    min_listings = 1000

    def fetch(self) -> Result:
        entries = []
        for sm in SITEMAPS:
            xml = fetch.html(sm)
            entries += re.findall(r"<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?", xml)
        if len(entries) < self.min_listings:
            raise RuntimeError(f"sitemaps list only {len(entries)} properties")
        cache: dict = self.cache.setdefault("pages", {})
        out = []
        for url, lastmod in entries:
            hit = cache.get(url)
            if hit and hit.get("lastmod") == lastmod and lastmod and hit.get("v") == PARSER:
                out.append(_from_dict(hit["listing"]))
                continue
            lst = parse_page(url, fetch.html(url))
            cache[url] = {"lastmod": lastmod, "v": PARSER, "listing": asdict(lst)}
            out.append(lst)
        live = {u for u, _ in entries}
        for u in [u for u in cache if u not in live]:
            del cache[u]
        return Result(listings=out)
