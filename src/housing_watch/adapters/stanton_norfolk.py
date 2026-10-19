"""Stanton Norfolk Inc.: Yardi RentCafe sites for its HPD/HDC buildings (East Village Homes, One Flushing,
Chinatown, Harlem, Lower East Side), all static HTML linked from the portfolio hub.

One Listing per site. Units come from the site's HPD ad PDF ("Income Limits" in the nav: the authoritative
income table) plus any `floorplans.aspx` tier (name "140% AMI - Studio 1 Bath" / "Studio 1 Bath 130% AMI")
the ad does not cover; floor-plan tiers have no income table and their Bed/rent cells are unreliable
(LES prints "Studio" for every plan), so size and AMI come from the plan name and rent is the low end.

Ad tables print one row per household size, with the unit size and rent on the middle row of its group,
so rows are grouped (a new group when the minimum income changes or the household size restarts) and the
k-th size line goes to the k-th group. An ad whose 1-person cap is below the current 1-person limit for its
own AMI is on stale limits (One Flushing's "Mayor Eric Adams" ad): each maximum is recomputed from today's
HUD limits at that AMI, and the minimum (set by the unit's rent, which the ad still prints) is kept. Ad
parses are cached per PDF URL (RentCafe gives every upload a new document id) and refreshed weekly.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse

from .. import ami, fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

HUB = "https://stanton-norfolk.rentcafewebsite.com/"
_REFRESH = 7 * 86400

# Facts the sites do not print in a parseable place.
EXTRA = {
    "east-village-homes-owner-llc": "mail completed paper applications by regular mail only to East Village Homes"
    " c/o Stanton Norfolk Inc., PO Box 981, New York, NY 10002 (the ad's PO Box 991 is for requesting one);"
    " apply online or by mail, never both",
}

_SIZE_LINE = re.compile(r"^\s*(studio|\d\s*bed(?:room)?s?)\b(.*)$", re.I)
_BAND = re.compile(r"(\d)\s+(?:person|people)\s+(\$.*)$", re.I)


def cfemail(hexstr: str) -> str:
    key = int(hexstr[:2], 16)
    return "".join(chr(int(hexstr[i : i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))


def parse_ad(txt: str) -> list[Unit]:
    """pdftotext -layout of an HPD ad -> one Unit per (size, AMI) with per-household Bands."""
    lines = txt.splitlines()
    start = next((i for i, x in enumerate(lines) if re.search(r"unit size", x, re.I)), None)
    if start is None:
        raise ValueError("no 'Unit Size' header in ad")
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"\s*1\s*(rent|$)", lines[i], re.I)),
        len(lines),
    )
    groups: list[list[Band]] = []
    sizes: list[tuple[str, float | None]] = []
    amis: list[int] = []
    for x in lines[start + 1 : end]:
        amis += [int(a) for a in re.findall(r"(\d{2,3})\s*%", x)]
        rest = x
        sm = _SIZE_LINE.match(x)
        if sm:
            m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", sm.group(2))
            sizes.append((parse.size(sm.group(1)), parse.money(m.group(1)) if m else None))
            rest = sm.group(2)[m.end() :] if m else sm.group(2)
        bm = _BAND.search(rest)
        if not bm:
            continue
        hh = int(bm.group(1))
        lo, hi = parse.money_range(bm.group(2))
        prev = groups[-1][-1] if groups else None
        if prev is None or prev.income_min != lo or hh <= prev.household:
            groups.append([])
        groups[-1].append(Band(hh, lo, hi))
    if not sizes or len(sizes) != len(groups):
        raise ValueError(f"ad table: {len(sizes)} size lines vs {len(groups)} income groups")
    uniq = sorted(set(amis))
    units = []
    for k, ((sz, rent), bands) in enumerate(zip(sizes, groups, strict=True)):
        a = uniq[0] if len(uniq) == 1 else (amis[k] if k < len(amis) else None)
        units.append(Unit(size=sz, rent=rent, ami=a, bands=bands))
    return units


def stale_limits(units: list[Unit]) -> bool:
    """True when an ad's 1-person cap is below today's 1-person limit at its own AMI (old HUD year)."""
    for u in units:
        b1 = next((b for b in u.bands if b.household == 1), None)
        cur = ami.cap(u.ami, 1)
        if b1 and b1.income_max and cur and b1.income_max < cur - 50:
            return True
    return False


def parse_floorplans(html: str) -> list[Unit]:
    s = parse.soup(html)
    out, seen = [], set()
    for pane in s.select("div.tab-pane"):
        if pane.select_one("div.tab-pane"):
            continue  # the wrapper pane holds every plan
        name_el = pane.select_one(".floor-plan-name")
        if not name_el:
            continue
        name = name_el.get_text(" ", strip=True)
        sz, a = parse.size(name), parse.ami(name)
        if (sz, a) in seen:
            continue
        seen.add((sz, a))
        avail = (pane.select_one(".availability-count") or name_el).get_text(" ", strip=True)
        rent_cell = next((r[1] for r in parse.table_rows(str(pane)) if r[0] == "Rent" and len(r) > 1), "")
        rents = [parse.money(x) for x in re.findall(r"\$\s*[\d,]+", rent_cell)]
        m = re.search(r"(\d+)\s+available", avail, re.I)
        out.append(
            Unit(
                size=sz,
                ami=a,
                rent=min(rents) if rents else None,
                available=(int(m.group(1)) > 0) if m else None,
            )
        )
    return out


def parse_site(base: str, home: str, floorplans: str | None, ad_text: str | None, ad_url: str | None) -> Listing:
    host = urlparse(base).netloc.split(".")[0]
    s = parse.soup(home)
    t = parse.text(s)
    title = re.sub(r"^.*\|\s*", "", (s.title.string or host) if s.title else host).strip()
    group = re.search(r"homepage for (.+?) [Pp]roperties", t)
    welcome = re.search(r"Welcome to (?:the )?(.+?) [Pp]roperty [Ww]ebsite", t)
    name = group.group(1).strip() if group else (welcome.group(1).strip() if welcome else title)
    # the footer block is the property (or, for the scattered-site groups, the Manhattan office) address
    fi = t.find("apartment search")
    foot = t[fi : fi + 400] if fi >= 0 else ""
    fl = [x.strip(" ,") for x in foot.splitlines()[1:] if x.strip(" ,*")]
    addr_lines = []
    for x in fl:
        if re.match(r"\(\d{3}\)", x):
            break
        if not re.fullmatch(r"beds|studio|\d|search|\*", x, re.I):
            addr_lines.append(x)
    address = ", ".join(addr_lines[1:]) if len(addr_lines) > 1 else None
    closed = bool(re.search(r"waitlist is now closed|no longer accepting", t, re.I))
    units: list[Unit] = []
    notes = []
    if ad_text:
        try:
            units = parse_ad(ad_text)
            if stale_limits(units):
                b1 = next(b for b in units[0].bands if b.household == 1)
                notes.append(
                    f"ad uses old income limits (1-person max ${b1.income_max:,} at {units[0].ami}% AMI): maxima"
                    " shown are today's HUD limits at that AMI, minimums the ad's; confirm with the office"
                )
                for u in units:
                    u.bands = [Band(b.household, b.income_min, ami.cap(u.ami, b.household)) for b in u.bands]
        except ValueError as e:
            notes.append(f"ad table not parsed ({e})")
    elif ad_url:
        notes.append(f"ad PDF unreadable: {ad_url}")
    covered = {(u.size, u.ami): u for u in units}
    for u in parse_floorplans(floorplans) if floorplans else []:
        if (u.size, u.ami) in covered:
            covered[(u.size, u.ami)].available = u.available  # "(1 Available)" on the matching plan
        elif u.size != "unknown":
            units.append(u)
    # how to apply: online guest login, application PDF, mail address and emails printed on the ad/site
    login = next(
        (a["href"] for a in s.find_all("a", href=True) if "guestlogin" in a["href"] and "Applicant" in a.get_text()),
        None,
    )
    app_pdf = next(
        (urljoin(base, a["href"]) for a in s.find_all("a", href=True) if re.search(r"application", a.get_text(), re.I)),
        None,
    )
    emails = {cfemail(x["data-cfemail"]) for x in s.find_all(attrs={"data-cfemail": True})}
    emails |= set(re.findall(r"[\w.]+@stantonnorfolk\.org", ad_text or ""))
    flat_ad = re.sub(r"\s+", " ", ad_text or "")
    mail = re.search(r"([A-Z][\w ]+ c/o Stanton Norfolk Inc\.?,? P\.?O\.? Box \d+,? New York,? NY \d{5})", flat_ad)
    phone = re.search(r"\(\d{3}\) \d{3}-\d{4}|\d{3}-\d{3}-\d{4}", t)
    how = []
    if login:
        how.append(f"online {login.split('?')[0]}")
    if app_pdf:
        how.append(f"application PDF {app_pdf}")
    if mail:
        how.append(f"request by mail: {mail.group(1)}")
    if emails:
        how.append("email " + ", ".join(sorted(emails)))
    if phone:
        how.append(f"phone {phone.group(0)}")
    if host in EXTRA:
        how.append(EXTRA[host])
    notes.insert(0, "Apply: " + "; ".join(how) if how else "Apply: see site")
    return Listing(
        source="stanton_norfolk",
        ext_id=host,
        title=name.title() if name.isupper() else name,
        url=base,
        address=address,
        borough=parse.borough(address, flat_ad[:400]),
        status="closed" if closed else "open",
        kind="waitlist",
        units=units,
        notes=". ".join(notes),
    )


class StantonNorfolk(Adapter):
    name = "stanton_norfolk"
    title = "Stanton Norfolk Inc."
    url = HUB
    every_hours = 12  # open waiting lists, processed in order received
    min_listings = 4  # the hub links five property sites

    def _ad(self, url: str) -> str | None:
        cache = self.cache.setdefault("ads", {})
        self._seen.add(url)
        hit = cache.get(url)
        if hit and time.time() - hit["at"] < _REFRESH:
            return hit["text"]
        try:
            txt = fetch.pdf_text(url)
        except fetch.FetchError:
            return None  # e.g. the Lower East Side ad link 503s
        cache[url] = {"at": time.time(), "text": txt}
        return txt

    def fetch(self) -> Result:
        self._seen: set[str] = set()
        hub = parse.soup(fetch.html(HUB))
        sites = []
        for a in hub.find_all("a", href=True):
            u, label = urlparse(a["href"]), a.get_text(strip=True)
            if u.netloc.endswith(".rentcafewebsite.com") and "stanton-norfolk" not in u.netloc and label:
                base = f"https://{u.netloc}/"
                if base not in sites and label.lower() != "commercial":
                    sites.append(base)
        if len(sites) < self.min_listings:
            raise RuntimeError(f"hub links only {len(sites)} property sites")
        out = []
        for base in sites:
            home = fetch.html(base)
            s = parse.soup(home)
            ad = next(
                (
                    urljoin(base, x["href"])
                    for x in s.find_all("a", href=True)
                    if re.search(r"income limits", x.get_text(), re.I) and ".pdf" in x["href"].lower()
                ),
                None,
            )
            try:
                fp = fetch.html(urljoin(base, "floorplans.aspx"))
            except fetch.FetchError:
                fp = None
            out.append(parse_site(base, home, fp, self._ad(ad) if ad else None, ad))
        ads = self.cache.get("ads", {})
        for k in [k for k in ads if k not in self._seen]:
            del ads[k]
        return Result(listings=out)
