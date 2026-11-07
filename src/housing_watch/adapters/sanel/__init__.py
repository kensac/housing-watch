"""SANEL (sanel.nyc): an aggregator of ~27 NYC sources, used here as the safety net.

Every listing page is in the sitemap as /affordable-housing/<borough>/<slug>. The sitemap has no lastmod, so pages
are cached by url and re-read on a rolling schedule: new urls at once, then the stalest pages, so each page is
refreshed at least every MAX_AGE_H. A page missing from the sitemap keeps its last copy until it has been missing
for MAX_AGE_H too, since the sitemap drops and re-adds pages between reads. The pages due are spread over chunks on
different workers; every page and document is its own top-level cache key, so chunks' deltas never overlap. A page
carries JSON-LD RealEstateListing (name, official source in `isBasedOn`, address), a facts block (Rent, Bedrooms,
AMI, Household, Income, Deadline), links to the official page or flyer and, sometimes, a tier table with one row per
household size. Both round rents and drop unit numbers, and a tier table can skip household sizes, so the
official data is read where it is public (Housing Connect's API, HDC's flyer PDF, Pronto's API, HPD's Mitchell-Lama
list, Reside's unit page or its building table's row at the unit's rent, SHF's re-rental form, Tax Solute's page,
St. Nicks' senior-housing list, HCR Housing Search), cached per document for DOC_TTL_H; otherwise `notes` says what
the source does not publish. Applying is never through SANEL.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict

from ... import ami, fetch, parse
from ...fetch import FetchError
from ...models import Band, Listing, Result, Unit
from .. import (
    PLAN,
    Adapter,
    cached_listing,
    housing_connect,
    mitchell_lama,
    nychdc,
    nys_housing_search,
    pronto,
    reside,
    spread,
    st_nicks,
    tax_solute,
)
from .. import settlement_housing_fund as shf
from .page import PAGE_RE, doc_links, old_chart_note, parse_page, tier_units

log = logging.getLogger("housing_watch")
SITEMAP = "https://sanel.nyc/sitemap.xml"
GAP_S = 1.0  # SANEL asks nothing, but it is a small site: 1 request per second per worker
MAX_AGE_H = 48  # every cached page is re-read at least this often
REFRESH_SHARE = 8  # each run re-reads the stalest 1/8 of known pages (every 4h -> all within 32h)
MAX_FAILED = 20  # more fetch failures than this in one run = something is wrong with the site
DOC_TTL_H = 24  # an official document (API answer, flyer, list) is re-read at most this often
DOC_DROP_H = 7 * 24  # a document no listing has needed for this long leaves the cache
UNIT_TITLE = re.compile(r"(.*?)\s*#\s*(\w+)\s*$")  # SANEL titles a single unit "<address> #<unit>"
# sources with nothing public beyond SANEL's summary
NOT_PUBLISHED = {
    "St. Nicks Alliance": "St. Nicks' waitlist application lists no rents or income limits (HUD-assisted).",
    "Settlement Housing Fund": "SHF's re-rental form has no income table for this unit.",
    "MHANY Management": "MHANY's page lists no unit sizes or income limits for this building.",
}


def _official(lst: Listing) -> tuple[str, str]:
    """(source name, official url) back out of the notes parse_page wrote."""
    m = re.search(r"official source \((.*?)\): (\S+)", lst.notes or "")
    return (m.group(1), m.group(2).rstrip(".")) if m else ("unknown", "")


def _tokens(s: str) -> list[str]:
    return [re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", t) for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def reside_match(title: str, urls: list[str]) -> str | None:
    """'346 Kent Ave #1001' -> the Reside page naming unit 1001 that shares the most (2+) address words."""
    m = UNIT_TITLE.match(title)
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


def reside_buildings(title: str, urls: list[str]) -> list[str]:
    """'1440 Amsterdam Avenue #1' -> Reside building pages (no unit in the url) with its numbers and 2+ words."""
    m = UNIT_TITLE.match(title)
    want = set(_tokens(m.group(1))) if m else set()
    nums = {t for t in want if t.isdigit()}
    found = []
    for u in urls:
        toks = set(_tokens(u.rstrip("/").rsplit("/", 1)[-1]))
        if nums and nums <= toks and len(want & toks) >= 2 and "unit" not in toks:
            found.append((len(want & toks), u))
    return [u for _, u in sorted(found, reverse=True)[:3]]


def _in_table(u: Unit, rows: list[list[str]]) -> bool:
    """A table row with the unit's size, rent and lowest minimum income (not the header's building-wide range)."""
    lo = next((b.income_min for b in u.bands if b.income_min), None)
    for row in rows:
        line = " ".join(row)
        if (
            u.size != "unknown"
            and any(parse.size(c) == u.size for c in row)
            and any("$" in c and round(parse.money(c) or 0) == round(u.rent or 0) for c in row)
            and (lo is None or f"{lo:,}" in line)
        ):
            return True
    return False


def _unit(u: dict) -> Unit:
    return Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]})


class Sanel(Adapter):
    name = "sanel"
    title = "SANEL (aggregator)"
    url = "https://sanel.nyc/listings"
    min_listings = 150
    parallel = 4
    _aura = None

    def _doc(self, key: str, load, ttl_h: float = DOC_TTL_H):
        """An official document's parsed content (json-serializable), cached for ttl_h."""
        k = f"doc:{key}"  # a top-level key per document, so concurrent chunks' caches merge without loss
        hit = self.cache.get(k)
        if hit and time.time() - hit["fetched"] < ttl_h * 3600:
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
            dev = self._doc("ml", self._ml_list).get(parse.slug(lst.title))
            if not dev:
                return None, "Not on HPD's open Mitchell-Lama waitlists PDF, so its income-limit category is unknown."
            lst.senior_only = lst.senior_only or dev["senior_only"]
            return [_unit(u) for u in dev["units"]], "Sizes and income caps from HPD's Mitchell-Lama list and limits."
        if src == "Reside":
            return self._reside(lst, official)
        if src == "Settlement Housing Fund":
            return self._shf(lst)
        if src == "Tax Solute":
            return self._tax_solute(lst)
        if src == "St. Nicks Alliance":
            seniors = self._doc("stnicks", lambda: st_nicks.senior_waitlists(fetch.html(st_nicks.INDEX)))
            if parse.name_key(lst.title) in {parse.name_key(n) for n in seniors}:
                lst.senior_only = True
                return None, "St. Nicks lists it under its HUD senior housing (elderly or disabled residents)."
        if m := re.search(r"nyshcr\.my\.site\.com/housing/s/waitlist/(\w+)", official):
            units = nys_housing_search.rule_units(self._doc(f"nys:{m.group(1)}", lambda: self._hcr_rules(m.group(1))))
            return (
                units or None,
                "Tiers from HCR Housing Search." if units else "HCR Housing Search shows no units table.",
            )
        return None, NOT_PUBLISHED.get(src, f"{src} publishes no income table for this listing.")

    def _reside_units(self, page: str) -> list[Unit]:
        return reside.parse_page(page, self._doc(f"page:{page}", lambda: fetch.html(page))).units

    def _reside(self, lst: Listing, official: str) -> tuple[list[Unit] | None, str]:
        rents = {round(u.rent) for u in lst.units if u.rent}
        if "/property/" in official:
            page = official
        else:
            page = reside_match(lst.title, self._doc("reside", self._reside_urls))
            page = page or reside_match(lst.title, self._doc("reside", self._reside_urls, ttl_h=1))  # a new page
        units = self._reside_units(page) if page else []
        if page and page != official and rents and not rents & {round(u.rent) for u in units if u.rent}:
            page = None  # a same-named unit from an older listing (the rent differs)
        if page is None:
            # no unit page: the building's page may tabulate a tier at exactly this unit's rent
            for b in reside_buildings(lst.title, self._doc("reside", self._reside_urls)):
                rows = parse.table_rows(self._doc(f"page:{b}", lambda b=b: fetch.html(b)))
                tier = [u for u in self._reside_units(b) if u.rent and round(u.rent) in rents and _in_table(u, rows)]
                if tier:
                    return tier, f"Tier at this unit's rent from Reside's building page {b}."
            return None, "Reside has no public page for this unit, only its Airtable form."
        if not any(b.income_max for u in units for b in u.bands) and all(u.size == "unknown" for u in units):
            return None, f"Reside's page {page} lists no sizes or income limits."
        return units, f"Unit details from Reside's page {page}."

    def _shf(self, lst: Listing) -> tuple[list[Unit] | None, str]:
        """'30 Kent Street #310' -> that unit on SHF's re-rental form (its table image OCR'd)."""

        def load() -> list[dict]:
            form_url, _ = shf.index_links(fetch.html(shf.URL))
            ocr = lambda img: shf.ocr_table(fetch.get(img).content)  # noqa: E731
            return [asdict(x) for x in shf.read_form(form_url, ocr)[0]] if form_url else []

        m = UNIT_TITLE.match(lst.title)
        if m:
            key, no = shf.addr_key(m.group(1)), m.group(2).upper()
            for d in self._doc("shf", load):
                units = [_unit(u) for u in d["units"] if u["unit_no"] == no]
                if shf.addr_key(d["title"]) == key and units and units[0].bands:
                    return units, "Income table from SHF's re-rental form."
        return None, NOT_PUBLISHED["Settlement Housing Fund"]

    def _tax_solute(self, lst: Listing) -> tuple[list[Unit] | None, str]:
        """The Tax Solute card at the same address (and rent, when SANEL shows one)."""
        page = lambda: [asdict(x) for x in tax_solute.parse_page(fetch.html(tax_solute.URL))[0]]  # noqa: E731
        cards = self._doc("taxsolute", page)
        keys = parse.addr_keys(lst.address or lst.title)
        rents = {round(u.rent) for u in lst.units if u.rent}
        for d in cards:
            if keys & parse.addr_keys(d["address"] or d["title"]):
                units = [_unit(u) for u in d["units"]]
                if units and (not rents or rents & {round(u.rent) for u in units if u.rent}):
                    return units, "Income table from Tax Solute's page."
        return None, "Tax Solute's page has no card for this address."

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

    def enrich(self, lst: Listing, html: str, prev: dict | None = None) -> Listing:
        """A listing -> the official source's units, or a note why there are none.

        SANEL's own units round rents to the dollar and drop unit numbers, and its tier table (when it has one) can
        skip household sizes (2140 Matthews #4A lists 1 and 3 people, not 2), so the official source's units win
        whenever they carry income limits. A tier table stands when the official source has none or cannot be
        read. `prev` is the page's cached listing: without a tier table, when the official source cannot be read,
        its units and note stand (swapping to SANEL's summary would flap every run)."""
        if lst.status == "closed" or lst.kind == "homeownership":
            return lst
        tiered = bool(tier_units(parse.soup(html)))
        try:
            units, note = self._official_units(lst, doc_links(html))
        except (FetchError, RuntimeError, ValueError, KeyError, OSError) as e:
            log.warning("sanel: official source for %s: %s", lst.url, e)
            if tiered:
                return lst
            if prev:
                kept = cached_listing(prev)
                lst.units, lst.notes = kept.units, kept.notes
                return lst
            units, note = None, "Official source unreadable this run; units are SANEL's summary."
        if tiered and not any(b.income_max for u in units or [] for b in u.bands):
            return lst  # the tier table says more than the official source
        if units:
            old = old_chart_note(lst.units)
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
            note += old_chart_note(units)
        elif all(u.size != "unknown" and any(b.income_max for b in u.bands) for u in lst.units):
            return lst  # the facts block already had every size and cap
        lst.notes = f"{lst.notes} {note}"
        return lst

    def chunks(self) -> list[str]:
        """Read the sitemap and plan: new pages, then anything older than MAX_AGE_H, then the stalest share. Pages
        missing from the sitemap for less than MAX_AGE_H are served from the cache."""
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
            if k.startswith("http") and k not in live:
                # the sitemap drops and re-adds pages between reads (SANEL keeps two records for some buildings), so a
                # page is gone only once it has been missing for a whole re-read cycle; until then its copy stands
                if now - c[k].setdefault("absent", now) > MAX_AGE_H * 3600:
                    del c[k]
            elif k.startswith("doc:") and now - c[k]["fetched"] > DOC_DROP_H * 3600:
                del c[k]  # documents no listing has needed for a week
            elif k in live:
                c[k].pop("absent", None)
        absent = [k for k in c if k.startswith("http") and k not in live]
        missing = [u for u in urls if u not in c]
        cached = sorted((u for u in urls if u in c), key=lambda u: c[u]["fetched"])
        stale = [u for u in cached if now - c[u]["fetched"] > MAX_AGE_H * 3600]
        todo = set(missing) | set(cached[: max(len(urls) // REFRESH_SHARE, len(stale))])
        c[PLAN] = spread([u for u in urls if u in todo], [u for u in urls if u not in todo] + absent, self.parallel)
        return list(c[PLAN])

    def fetch_chunk(self, key: str) -> Result:
        """Cached listings for the chunk's other pages; its planned pages fetched, enriched and cached by url."""
        c, part, now = self.cache, self.cache[PLAN][key], time.time()
        out = [cached_listing(c[u]["listing"]) for u in part["keep"] if c[u].get("listing")]
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
                    out.append(cached_listing(hit["listing"]))
                continue
            if lst is None:  # the sitemap lists some pages that 404
                c[url] = {"fetched": now, "listing": None}
                continue
            lst = self.enrich(lst, body, hit.get("listing") if hit else None)
            c[url] = {"fetched": now, "listing": asdict(lst)}
            out.append(lst)
        if len(failed) > MAX_FAILED // int(key.split("/")[1]):  # the run's allowance, shared by its chunks
            raise RuntimeError(f"{len(failed)} SANEL pages failed to fetch, e.g. {failed[:3]}")
        return Result(listings=out)

    def fetch(self) -> Result:
        return self.fetch_serial()


def _get(url: str) -> str | None:
    """Page html, or None when the page is a 404."""
    try:
        return fetch.get(url).text
    except FetchError as e:
        if "404 Not Found" in str(e):
            return None
        raise
