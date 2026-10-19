"""SANEL (sanel.nyc): an aggregator of ~27 NYC sources, used here as the safety net.

Every listing page is in the sitemap as /affordable-housing/<borough>/<slug>. The sitemap has no lastmod, so
pages are cached by url and re-read on a rolling schedule: new urls at once, then the stalest pages, so each
page is refreshed at least every MAX_AGE_H. The pages due are spread over chunks on different workers; every
page and document is its own top-level cache key, so chunks' deltas never overlap. A page carries JSON-LD
RealEstateListing (name, official source in `isBasedOn`, address), a facts block (Rent, Bedrooms, AMI,
Household, Income, Deadline), links to the official page or flyer and, when the source publishes one, a tier
table with one row per household size. Without a tier table the facts block only summarises every tier, so the
official data is read where it is public (Housing Connect's API, HDC's flyer PDF, Pronto's API, HPD's
Mitchell-Lama list, Reside's page, HCR Housing Search), cached per document for DOC_TTL_H; otherwise `notes`
says what the source does not publish. Applying is never through SANEL.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict
from datetime import datetime

from .. import ami, fetch, parse
from ..fetch import FetchError
from ..models import Band, Listing, Result, Unit
from . import PLAN, Adapter, housing_connect, mitchell_lama, nychdc, nys_housing_search, pronto, reside, spread

log = logging.getLogger("housing_watch")
SITEMAP = "https://sanel.nyc/sitemap.xml"
PAGE_RE = re.compile(r"^https://sanel\.nyc/affordable-housing/([a-z-]+)/([^/]+)$")
URL_BOROUGH = {
    "manhattan": "Manhattan",
    "brooklyn": "Brooklyn",
    "queens": "Queens",
    "bronx": "Bronx",
    "staten-island": "Staten Island",
}
KINDS = {
    "lottery": "lottery",
    "re-rental": "re-rental",
    "rerental": "re-rental",
    "waitlist": "waitlist",
    "mitchell-lama": "waitlist",
    "homeownership": "homeownership",
    "sale": "homeownership",
}
GAP_S = 1.0  # SANEL asks nothing, but it is a small site: 1 request per second per worker
MAX_AGE_H = 48  # every cached page is re-read at least this often
REFRESH_SHARE = 8  # each run re-reads the stalest 1/8 of known pages (every_hours=6 -> all within 48h)
MAX_FAILED = 20  # more fetch failures than this in one run = something is wrong with the site
DOC_TTL_H = 24  # an official document (API answer, flyer, list) is re-read at most this often
DOC_DROP_H = 7 * 24  # a document no listing has needed for this long leaves the cache
FACT_LABELS = ("Rent", "Bedrooms", "AMI", "Household", "Income", "Available units", "Deadline", "Source")
SENIOR_RE = re.compile(r"\bseniors?\b|62\s*(\+|years|and older)|aged? 62|elderly", re.I)
SENIOR_PREF_RE = re.compile(r"senior\s+prefere?n?ce?\s*\d*\+?", re.I)  # a preference, not a restriction
SET_ASIDE_RE = re.compile(r"\b(mobility|hearing|visual|h/v|senior|62\+|mi)\b", re.I)
OLD_CHART = " Income limits are printed on HUD's {} chart ({} caps are higher; confirm which applies)."
# sources with nothing public beyond SANEL's summary
NOT_PUBLISHED = {
    "St. Nicks Alliance": "St. Nicks' waitlist application lists no rents or income limits (HUD-assisted).",
    "Settlement Housing Fund": "SHF publishes this unit's income table only as an image in its form.",
    "MHANY Management": "MHANY's page lists no unit sizes or income limits for this building.",
}


def _ld(s) -> dict:
    for tag in s.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(tag.string or "")
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "RealEstateListing":
            return d
    return {}


def _facts(lines: list[str]) -> dict[str, str]:
    """Label/value pairs in the header block (everything before 'About this opportunity')."""
    out: dict[str, str] = {}
    for i, ln in enumerate(lines[:-1]):
        if ln.startswith("About this opportunity"):
            break
        if ln in FACT_LABELS and ln not in out:
            out[ln] = lines[i + 1]
    return out


def _date(text: str | None) -> str | None:
    """'Nov 10, 2026' / 'November 10, 2026' -> '2026-11-10'; 'Open until filled' -> None."""
    m = re.search(r"\b([A-Z][a-z]{2})[a-z]*\.? (\d{1,2}), (\d{4})", text or "")
    if not m:
        return None
    try:
        return datetime.strptime(" ".join(m.groups()), "%b %d %Y").date().isoformat()
    except ValueError:
        return None


def _old_chart(units: list[Unit]) -> str:
    old = sorted({u.chart_year for u in units if u.chart_year and u.chart_year < ami.CURRENT_YEAR})
    return OLD_CHART.format("/".join(map(str, old)), ami.CURRENT_YEAR) if old else ""


def _tiers(s) -> list[Unit]:
    """Tier table rows (size, rent, AMI, household, income [, notes]) grouped into one Unit per tier."""
    units: dict[tuple, Unit] = {}
    for cells in parse.table_rows(s):
        if len(cells) < 5 or "/mo" not in cells[1] and not re.search(r"\$|not published|—|-", cells[1]):
            continue
        if cells[0].lower() == "unit":
            continue
        size = parse.size(cells[0])
        rent = parse.money(cells[1]) if "$" in cells[1] else None
        a = parse.ami(cells[2])
        extra = " ".join([cells[0], *cells[5:]])
        set_aside = None
        if m := SET_ASIDE_RE.search(extra):
            set_aside = "mobility" if m.group(1).lower() == "mi" else m.group(1).lower()
        k = (size, rent, a, set_aside)
        u = units.get(k) or units.setdefault(k, Unit(size=size, rent=rent, ami=a, set_aside=set_aside))
        hh, (lo, hi) = parse.household_range(cells[3]), parse.money_range(cells[4])
        bands, year = ami.bands(hh, lo, hi, a) if hh else ([], None)
        u.chart_year = u.chart_year or year
        for b in bands:
            if all(x.household != b.household for x in u.bands):
                u.bands.append(b)
    for u in units.values():
        u.bands.sort(key=lambda b: b.household)
    return list(units.values())


def _from_facts(f: dict[str, str]) -> list[Unit]:
    """No tier table: one Unit per published size, with what the facts block allows.

    Income there runs from the lowest minimum to the highest cap over every tier. One size, rent and AMI
    make it a single tier, read exactly; otherwise the caps still bound every tier from above (each
    household's cap at the top AMI) but the minimum may be a cheaper tier's, so it is dropped."""
    sizes = [parse.size(x) for x in f.get("Bedrooms", "").split("·")]
    sizes = [x for x in dict.fromkeys(sizes) if x != "unknown"] or ["unknown"]
    amis = [int(x) for x in re.findall(r"(\d{2,3})\s*%", f.get("AMI", ""))]
    rents = [parse.money(x) for x in re.findall(r"\$[\d,]+(?:\.\d+)?", f.get("Rent", ""))]
    hh = parse.household_range(f.get("Household", ""))
    lo, hi = parse.money_range(f.get("Income", ""))
    single = len(sizes) == 1 and len(rents) <= 1 and len(amis) <= 1
    year = None
    if hi and hi > (lo or 0):
        bands, year = ami.bands(hh, lo if single else None, hi, max(amis) if amis else None)
    else:  # no income: the household range can only prove a minimum, never a 1-person cap
        bands = [Band(h, None, None) for h in hh] if hh and min(hh) > 1 else []
    units = [
        Unit(
            size=z,
            rent=rents[0] if len(rents) == 1 and len(sizes) == 1 else None,
            ami=max(amis) if amis else None,
            bands=list(bands),
            chart_year=year,
        )
        for z in sizes
    ]
    return units


def doc_links(html: str) -> list[str]:
    """External links in the listing body (official page, flyer PDF), before the related listings."""
    art = parse.soup(html).find("article")
    out = []
    for a in art.find_all("a", href=True) if art else []:
        h2 = (a.find_parent("section") or a).find("h2")
        if h2 and "More nearby" in h2.get_text():
            break
        if a["href"].startswith("http") and "nyc.gov/site/buildings" not in a["href"]:
            out.append(a["href"])
    return list(dict.fromkeys(out))


def parse_page(url: str, html: str) -> Listing:
    s = parse.soup(html)
    ld = _ld(s)
    art = s.find("article")
    if art is None or not ld:
        raise ValueError(f"{url}: no listing article/JSON-LD (page layout changed?)")
    lines = parse.text(str(art)).splitlines()
    head = lines[:3]
    f = _facts(lines)
    desc = ld.get("description", "")
    kind = KINDS.get((head[0] if head else "").lower().replace(" ", "-"), "re-rental")  # the type badge
    badge = " ".join(head[:2]).lower()
    if "closed" in badge or "filled" in badge or "expired" in badge:
        status = "closed"
    elif kind == "waitlist":
        status = "waitlist"
    else:
        status = "open"
    pm = PAGE_RE.match(url.rstrip("/"))
    addr = ((ld.get("about") or {}).get("address") or {}).get("streetAddress")
    borough = URL_BOROUGH.get(pm.group(1)) if pm else None
    borough = borough or parse.borough(addr)
    flat = " ".join(
        lines[: lines.index("About this opportunity") + 3] if "About this opportunity" in lines else lines[:60]
    )
    units = _tiers(s) or _from_facts(f)
    src = f.get("Source")
    official = ld.get("isBasedOn")
    notes = f"Apply via the official source ({src or 'unknown'}): {official or url}." + _old_chart(units)
    if "upcoming" in badge:
        notes = "Upcoming. " + notes
    return Listing(
        source="sanel",
        ext_id=(pm.group(2) if pm else url.rstrip("/").split("/")[-1]),
        title=ld.get("name") or (lines[2] if len(lines) > 2 else url),
        url=url,
        address=addr,
        borough=borough,
        status=status,
        kind=kind,
        units=units,
        deadline=_date(f.get("Deadline")),
        senior_only=bool(SENIOR_RE.search(SENIOR_PREF_RE.sub(" ", desc + " " + flat))),
        notes=notes,
    )


def _official(lst: Listing) -> tuple[str, str]:
    """(source name, official url) back out of the notes parse_page wrote."""
    m = re.search(r"official source \((.*?)\): (\S+)", lst.notes or "")
    return (m.group(1), m.group(2).rstrip(".")) if m else ("unknown", "")


def _tokens(s: str) -> list[str]:
    return [re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", t) for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def reside_match(title: str, urls: list[str]) -> str | None:
    """'346 Kent Ave #1001' -> the Reside page naming unit 1001 that shares the most (2+) address words."""
    m = re.match(r"(.*?)\s*#\s*(\w+)\s*$", title)
    if not m:
        return None
    want, unit = set(_tokens(m.group(1))), m.group(2).lower()
    best: tuple[int, str] | None = None
    for u in urls:
        toks = _tokens(u.rstrip("/").rsplit("/", 1)[-1])
        shared = len(want & set(toks))
        if shared >= 2 and "unit" in toks and unit in toks[toks.index("unit") + 1 :] and (not best or shared > best[0]):
            best = (shared, u)
    return best[1] if best else None


def _unit(u: dict) -> Unit:
    return Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]})


def _from_dict(d: dict) -> Listing:
    return Listing(**{**d, "units": [_unit(u) for u in d["units"]]})


class Sanel(Adapter):
    name = "sanel"
    title = "SANEL (aggregator)"
    url = "https://sanel.nyc/listings"
    every_hours = 6
    min_listings = 150
    parallel = 4
    _aura = None

    def _doc(self, key: str, load):
        """An official document's parsed content (json-serializable), cached for DOC_TTL_H."""
        k = f"doc:{key}"  # a top-level key per document, so concurrent chunks' caches merge without loss
        hit = self.cache.get(k)
        if hit and time.time() - hit["fetched"] < DOC_TTL_H * 3600:
            return hit["data"]
        self.cache[k] = {"fetched": time.time(), "data": load()}
        return self.cache[k]["data"]

    def _official_units(self, lst: Listing, links: list[str]) -> tuple[list[Unit] | None, str]:
        """(units from the official source or None, a note saying where they came from or why there are none)."""
        src, official = _official(lst)
        if m := re.search(r"housingconnect\.nyc\.gov/PublicWeb/details/(\d+)", official):
            lid = m.group(1)
            ad = self._doc(
                f"hc:{lid}", lambda: fetch.json(f"{housing_connect.API}/GetLotteryAdvertisement?lotteryId={lid}")
            )
            units = housing_connect.parse_ad({"lotteryId": lid}, ad).units
            return (
                units or None,
                "Tiers from Housing Connect's ad." if units else "Housing Connect's ad lists no units.",
            )
        if flyer := next((u for u in links if re.search(r"nychdc\.com/.+\.pdf$", u, re.I)), None):
            units = nychdc.parse_flyer(self._doc(f"hdc:{flyer}", lambda: fetch.pdf_text(flyer)))
            return units or None, f"Tiers from HDC's flyer {flyer}." if units else f"HDC's flyer {flyer} has no tiers."
        if src == "Pronto":
            return self._pronto(lst, [official, *links])
        if src == "NYC Mitchell-Lama":
            dev = self._doc("ml", self._ml_list).get(mitchell_lama.slug(lst.title))
            if not dev:
                return None, "Not on HPD's open Mitchell-Lama waitlists PDF, so its income-limit category is unknown."
            lst.senior_only = lst.senior_only or dev["senior_only"]
            return [_unit(u) for u in dev["units"]], "Sizes and income caps from HPD's Mitchell-Lama list and limits."
        if src == "Reside":
            page = (
                official
                if "/property/" in official
                else reside_match(lst.title, self._doc("reside", self._reside_urls))
            )
            units = reside.parse_page(page, self._doc(f"page:{page}", lambda: fetch.html(page))).units if page else []
            rents = {round(u.rent) for u in lst.units if u.rent}
            if page and page != official and rents and not rents & {round(u.rent) for u in units if u.rent}:
                page = None  # a same-named unit from an older listing (the rent differs)
            if page is None:
                return None, "Reside has no public page for this unit, only its Airtable form."
            if not any(b.income_max for u in units for b in u.bands) and all(u.size == "unknown" for u in units):
                return None, f"Reside's page {page} lists no sizes or income limits."
            return units, f"Unit details from Reside's page {page}."
        if m := re.search(r"nyshcr\.my\.site\.com/housing/s/waitlist/(\w+)", official):
            units = nys_housing_search.rule_units(self._doc(f"nys:{m.group(1)}", lambda: self._hcr_rules(m.group(1))))
            return (
                units or None,
                "Tiers from HCR Housing Search." if units else "HCR Housing Search shows no units table.",
            )
        return None, NOT_PUBLISHED.get(src, f"{src} publishes no income table for this listing.")

    def _hcr_rules(self, wid: str) -> list[dict]:
        self._aura = self._aura or nys_housing_search.Aura()  # one page load per run
        return self._aura.call([("ListingRuleController", "getWaitlistRules", {"waitlistId": wid})])[0] or []

    def _pronto(self, lst: Listing, urls: list[str]) -> tuple[list[Unit] | None, str]:
        found = (re.search(r"prontohousingrentals\.com/listings/property/(\d+)", u) for u in urls)
        pid = next((m.group(1) for m in found if m), None)
        if pid is None:
            reg = next((m.group(1) for u in urls if (m := re.search(r"registration/([0-9a-f-]{36})", u))), "")
            pid = self._doc("pronto", self._pronto_reg).get(reg)
            if pid is None:
                return None, "Pronto has only an account sign-up for this waitlist: no sizes or income limits public."
        lp = pronto.parse_property(self._doc(f"pronto:{pid}", lambda: fetch.json(pronto.DETAIL.format(pid))))
        units = lp.units if lp else []
        if lst.kind == "waitlist":  # named units are SANEL's separate re-rental listings
            units = [u for u in units if not u.unit_no] or units
        if not units:
            return None, "Pronto publishes no unit sizes or income bands for this waitlist."
        sizeless = all(u.size == "unknown" for u in units)
        return units, "Income bands from Pronto's API" + (", which has no unit sizes for it." if sizeless else ".")

    def _pronto_reg(self) -> dict[str, int]:
        """Pronto registration uuid -> property id, from its search API (SANEL links some waitlists by uuid)."""
        out: dict[str, int] = {}
        for r in pronto.ny_records(fetch.json(pronto.SEARCH)):
            for k in ("applicationLink", "waitlistLink"):
                if m := re.search(r"registration/([0-9a-f-]{36})", r.get(k) or ""):
                    out.setdefault(m.group(1), r["id"])
        return out

    def _ml_list(self) -> dict[str, dict]:
        limits = mitchell_lama.income_limits(fetch.html(mitchell_lama.PAGE))
        devs = mitchell_lama.parse_open_lists(fetch.pdf_text(mitchell_lama.OPEN_LISTS), limits)
        return {d.ext_id: {"units": [asdict(u) for u in d.units], "senior_only": d.senior_only} for d in devs}

    def _reside_urls(self) -> list[str]:
        return [u for sm in reside.SITEMAPS for u in re.findall(r"<loc>([^<]+)</loc>", fetch.html(sm))]

    def enrich(self, lst: Listing, html: str) -> Listing:
        """A listing SANEL only summarises (no tier table) -> the official source's units, or a note why not."""
        if lst.status == "closed" or lst.kind == "homeownership" or _tiers(parse.soup(html)):
            return lst
        try:
            units, note = self._official_units(lst, doc_links(html))
        except (FetchError, RuntimeError, ValueError, KeyError) as e:
            log.warning("sanel: official source for %s: %s", lst.url, e)
            units, note = None, "Official source unreadable this run; units are SANEL's summary."
        if units:
            old = _old_chart(lst.units)
            for u in units:
                bands, year = ami.rows(u.bands, u.ami)  # Pronto stamps a unit's top cap on every household
                u.bands, u.chart_year = bands, u.chart_year or year
                if (
                    u.bands
                    and min(b.household for b in u.bands) == 1
                    and not any(b.income_min or b.income_max for b in u.bands)
                ):
                    u.bands = []  # rows without any income say nothing the printed AMI does not
            lst.units = units
            lst.notes = (lst.notes or "").replace(old, "") if old else lst.notes
            note += _old_chart(units)
        elif all(u.size != "unknown" and any(b.income_max for b in u.bands) for u in lst.units):
            return lst  # the facts block already had every size and cap
        lst.notes = f"{lst.notes} {note}"
        return lst

    def chunks(self) -> list[str]:
        """Read the sitemap and plan: new pages, then anything older than MAX_AGE_H, then the stalest share."""
        xml = fetch.html(SITEMAP)
        urls = [u for u in dict.fromkeys(re.findall(r"<loc>([^<]+)</loc>", xml)) if PAGE_RE.match(u)]
        if len(urls) < self.min_listings:
            raise RuntimeError(f"sitemap lists only {len(urls)} listing pages")
        c, now = self.cache, time.time()
        for u, e in c.pop("pages", {}).items():  # older layout: pages and docs each under one key
            c.setdefault(u, e)
        for k, e in c.pop("docs", {}).items():
            c.setdefault(f"doc:{k}", e)
        live = set(urls)
        for k in list(c):
            if (
                k.startswith("http")
                and k not in live
                or k.startswith("doc:")
                and now - c[k]["fetched"] > DOC_DROP_H * 3600
            ):
                del c[k]  # pages gone from the sitemap, documents no listing has needed for a week
        missing = [u for u in urls if u not in c]
        cached = sorted((u for u in urls if u in c), key=lambda u: c[u]["fetched"])
        stale = [u for u in cached if now - c[u]["fetched"] > MAX_AGE_H * 3600]
        todo = set(missing) | set(cached[: max(len(urls) // REFRESH_SHARE, len(stale))])
        c[PLAN] = spread([u for u in urls if u in todo], [u for u in urls if u not in todo], self.parallel)
        return list(c[PLAN])

    def fetch_chunk(self, key: str) -> Result:
        """Cached listings for the chunk's other pages; its planned pages fetched, enriched and cached by url."""
        c, part, now = self.cache, self.cache[PLAN][key], time.time()
        out = [_from_dict(c[u]["listing"]) for u in part["keep"] if c[u].get("listing")]
        last = 0.0
        failed = []
        for url in part["fetch"]:
            hit = c.get(url)
            time.sleep(max(0.0, last + GAP_S - time.monotonic()))
            last = time.monotonic()
            try:
                body = _get(url)
                lst = parse_page(url, body) if body is not None else None
            except (FetchError, ValueError) as e:
                log.warning("sanel: %s", e)
                failed.append(url)
                if hit and hit.get("listing"):  # keep the last good copy; a new page waits for the next run
                    out.append(_from_dict(hit["listing"]))
                continue
            if lst is None:  # the sitemap lists some pages that 404
                c[url] = {"fetched": now, "listing": None}
                continue
            lst = self.enrich(lst, body)
            c[url] = {"fetched": now, "listing": asdict(lst)}
            out.append(lst)
        if len(failed) > MAX_FAILED // int(key.split("/")[1]):  # the run's allowance, shared by its chunks
            raise RuntimeError(f"{len(failed)} SANEL pages failed to fetch, e.g. {failed[:3]}")
        return Result(listings=out)

    def fetch(self) -> Result:
        return self.fetch_serial()


def _get(url: str) -> str | None:
    """Page html, or None when the page is a 404 (no retries for those: they are permanent)."""
    try:
        return fetch.get(url, retries=0).text
    except FetchError as e:
        if "404 Not Found" in str(e):
            return None
    return fetch.get(url).text
