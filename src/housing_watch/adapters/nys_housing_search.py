"""NYS HCR Housing Search (housingsearch.hcr.ny.gov): statewide lotteries, waitlists and HCR Mitchell-Lama.

The site is a Salesforce Experience Cloud (aura) app. No browser is needed: the same Apex actions the
page calls are plain form POSTs that work for the guest user. They are sent to the org's own domain,
nyshcr.my.site.com, which serves the identical site with a complete TLS chain (housingsearch.hcr.ny.gov
omits its GlobalSign intermediate, so httpx and byparr's Firefox both refuse it, and hcr.ny.gov is behind
Cloudflare). Per run:

1. GET /housing/s/ for the aura framework id (fwuid) and app version, which change with Salesforce
   releases.
2. HousingSearchV4.getWaitListRecords with an empty filter: every card (~120 statewide). Kept: NYC
   counties only.
3. Per open NYC record, the units table the detail page shows: WaitlistRuleControllerML.
   getWaitlistRulesMLForTable for Mitchell-Lama (per size: rent or carrying charge, purchase price,
   household and income range, per-size Open/Closed), ListingRuleController.getWaitlistRules otherwise
   (per size x household x AMI: income range, rent; a rule with no income figures gets HUD's cap for
   its AMI category). Batched into a few POSTs.

Caveats: an ML row's income range spans every unit of that size ("1 to 2, $50,488 to $204,983"), so $150k
fits only the pricier units, and co-ops need an equity payment and wait years. When a record has no
units table, the card's min-max income (taken over every size and household) is kept as an upper bound.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections import defaultdict
from datetime import datetime

import httpx

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

BASE = "https://nyshcr.my.site.com/housing"
PUBLIC = "https://housingsearch.hcr.ny.gov/housing/s/waitlist/"
NYC_COUNTIES = {
    "Bronx": "Bronx",
    "Kings": "Brooklyn",
    "New York": "Manhattan",
    "Queens": "Queens",
    "Richmond": "Staten Island",
}
SEARCH_INPUT = {
    "RentLow": None,
    "RentHigh": None,
    "UnitType": None,
    "Income": None,
    "SelectedCity": None,
    "SelectedCounty": None,
    "SelectedZip": None,
    "LocationType": "City",
    "Project": None,
    "isHearingVisionChecked": False,
    "isMobilityChecked": False,
    "isRentalChecked": False,
    "isCoOpChecked": False,
    "selectedAmenities": [],
    "houseHoldSize": None,
}
BATCH = 15
APPLY = "Apply: NY.gov ID login on Housing Search (REGISTER/LOGIN TO APPLY), or the listing's How To Apply tab."


def borough(county: str) -> str | None:
    """'Kings (Brooklyn)' -> 'Brooklyn'; non-NYC counties -> None."""
    return NYC_COUNTIES.get(re.sub(r"\s*\(.*\)$", "", county or "").strip())


def deadline(text: str | None) -> str | None:
    try:
        return datetime.strptime((text or "").strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None  # "Ongoing", "Check Listing"


def _money(v) -> str:
    return f"${v:,.0f}" if isinstance(v, int | float) else "?"


def merge_units(units: list[Unit]) -> list[Unit]:
    """One Unit per key (the database fingerprints by key): rows of the same size are combined, keeping the
    lowest rent, the widest income range per household, and open if any row is open."""
    by: dict[str, Unit] = {}
    for u in units:
        k = u.key()
        if k not in by:
            by[k] = Unit(u.size, u.rent, u.ami, u.unit_no, list(u.bands), u.set_aside, u.available)
            continue
        m = by[k]
        rents = [r for r in (m.rent, u.rent) if r]
        m.rent = min(rents) if rents else None
        if m.available is not None or u.available is not None:
            m.available = bool(m.available) or bool(u.available)
        bands = {b.household: b for b in m.bands}
        for b in u.bands:
            o = bands.get(b.household)
            if o is None:
                bands[b.household] = b
            else:
                los = [x for x in (o.income_min, b.income_min) if x is not None]
                his = [x for x in (o.income_max, b.income_max) if x is not None]
                bands[b.household] = Band(b.household, min(los) if los else None, max(his) if his else None)
        m.bands = [bands[h] for h in sorted(bands)]
    return list(by.values())


def ml_units(rows: list[dict]) -> list[Unit]:
    out = []
    for r in rows:
        bands = []
        for h in r.get("households") or []:
            for n in range(int(h.get("minHousehold") or 1), int(h.get("maxHousehold") or 1) + 1):
                bands.append(Band(n, parse.whole(h.get("minIncome")), parse.whole(h.get("maxIncome"))))
        out.append(
            Unit(
                size=parse.size(r.get("unitType")),
                rent=r.get("minRent") or None,
                bands=bands,
                available=(r.get("Status") or "").lower() == "open" if r.get("Status") else None,
            )
        )
    return merge_units(out)


def rule_units(rows: list[dict]) -> list[Unit]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r.get("unitSize"), r.get("amiCategory"), r.get("unitType"))].append(r)
    out = []
    for (size, ami, utype), rs in groups.items():
        bands = []
        for r in rs:
            for n in parse.household_range(str(r.get("householdSize") or "")):
                lo, hi = parse.whole(r.get("minHouseholdIncome")), parse.whole(r.get("maxHouseholdIncome"))
                # some rules print only the AMI category (Inwood 405-407 W 206th): the cap follows from it
                bands.append(Band(n, lo, hi if hi is not None else parse.ami_max(parse.ami(ami), n)))
        rent = next((r["monthlyRent"] for r in rs if r.get("monthlyRent")), None)
        out.append(Unit(size=parse.size(size), rent=rent, ami=parse.ami(ami), unit_no=utype or None, bands=bands))
    return merge_units(out)


def to_listing(rec: dict, rules: list[dict] | None) -> Listing:
    ml = bool(rec.get("IsMitchellLama"))
    units = (ml_units(rules) if ml else rule_units(rules)) if rules else []
    lo, hi = parse.whole(rec.get("MinimumIncome")), parse.whole(rec.get("MaximumIncome"))
    if not units and hi:
        units = [Unit(size="unknown", bands=[Band(1, lo, hi)])]  # card range: an upper bound over all sizes
    typ = rec.get("Type") or ""
    closed = (rec.get("Status") or "").lower() == "closed"
    bits = [rec.get("projectType") or "", "Mitchell-Lama" if ml else "", f"{typ} {rec.get('Status', '')}".strip()]
    notes = "; ".join(b for b in bits if b) + f". Card income range {_money(lo)}-{_money(hi)}."
    if ml and rules:
        notes += " Per-size ranges span every unit of that size; co-ops need an equity payment; waits run years."
        for r in rules:
            pp = r.get("minPurchasePrice")
            notes += (
                f" {r.get('unitType')}: {_money(r.get('minRent'))}-{_money(r.get('maxRent'))}/mo"
                + (f", equity {_money(pp)}-{_money(r.get('maxPurchasePrice'))}" if pp else "")
                + f" ({r.get('Status')})."
            )
    notes += " " + APPLY
    return Listing(
        source="nys_housing_search",
        ext_id=rec["Id"],
        title=rec.get("ProjectName") or rec["Id"],
        url=PUBLIC + rec["Id"],
        address=", ".join(x for x in (rec.get("Cities"), rec.get("County")) if x) or None,
        borough=borough(rec.get("County") or ""),
        status="closed" if closed else ("open" if typ == "Lottery" else "waitlist"),
        kind="lottery" if typ == "Lottery" else "waitlist",
        units=units,
        deadline=deadline(rec.get("ApplicationEndDate")),
        senior_only=bool((rec.get("seniorLabel") or "").strip()),
        notes=notes,
    )


class Aura:
    """Guest calls to the site's Apex actions, as the page's own JavaScript makes them."""

    def __init__(self):
        page = urllib.parse.unquote(fetch.html(f"{BASE}/s/"))
        fw = re.search(r'"fwuid":"([^"]+)"', page)
        app = re.search(r'"(APPLICATION@markup://siteforce:communityApp)":"([^"]+)"', page)
        if not fw or not app:
            raise RuntimeError(f"{BASE}/s/: aura fwuid/app version not found (site changed?)")
        self.ctx = json.dumps(
            {
                "mode": "PROD",
                "fwuid": fw.group(1),
                "app": "siteforce:communityApp",
                "loaded": {app.group(1): app.group(2)},
                "dn": [],
                "globals": {},
                "uad": True,
            }
        )

    def call(self, calls: list[tuple[str, str, dict]]) -> list:
        actions = [
            {
                "id": f"{i};a",
                "descriptor": "aura://ApexActionController/ACTION$execute",
                "callingDescriptor": "UNKNOWN",
                "params": {
                    "namespace": "",
                    "classname": cls,
                    "method": method,
                    "params": params,
                    "cacheable": False,
                    "isContinuation": False,
                },
            }
            for i, (cls, method, params) in enumerate(calls)
        ]
        url = f"{BASE}/s/sfsites/aura?r=1&aura.ApexAction.execute={len(actions)}"
        data = {
            "message": json.dumps({"actions": actions}),
            "aura.context": self.ctx,
            "aura.pageURI": "/housing/s/",
            "aura.token": "null",
        }
        try:
            r = httpx.post(url, data=data, headers={"User-Agent": fetch.UA}, timeout=90)
            r.raise_for_status()
            got = r.json()["actions"]
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise fetch.FetchError(f"aura {url}: {e}") from e
        out = []
        for a in sorted(got, key=lambda a: int(a["id"].split(";")[0])):
            if a.get("state") != "SUCCESS":
                raise RuntimeError(f"aura action {a.get('id')} {a.get('state')}: {a.get('error')}")
            rv = a.get("returnValue")
            out.append(rv.get("returnValue") if isinstance(rv, dict) else rv)
        return out


class NysHousingSearch(Adapter):
    name = "nys_housing_search"
    title = "NYS HCR Housing Search"
    url = "https://housingsearch.hcr.ny.gov/housing/s/"
    every_hours = 12
    min_listings = 20  # NYC records: 51 of 122 on 2026-10-18

    def fetch(self) -> Result:
        aura = Aura()
        (records,) = aura.call([("HousingSearchV4", "getWaitListRecords", {"input": SEARCH_INPUT})])
        if not records or len(records) < 50:
            raise RuntimeError(f"search returned only {len(records or [])} records statewide")
        nyc = [r for r in records if borough(r.get("County") or "")]
        want = [r for r in nyc if (r.get("Status") or "").lower() != "closed"]
        rules: dict[str, list] = {}
        for i in range(0, len(want), BATCH):
            chunk = want[i : i + BATCH]
            calls = [
                ("WaitlistRuleControllerML", "getWaitlistRulesMLForTable", {"waitlistId": r["Id"]})
                if r.get("IsMitchellLama")
                else ("ListingRuleController", "getWaitlistRules", {"waitlistId": r["Id"]})
                for r in chunk
            ]
            for r, rv in zip(chunk, aura.call(calls), strict=True):
                rules[r["Id"]] = rv or []
        listings = [to_listing(r, rules.get(r["Id"])) for r in nyc]
        if len(listings) < self.min_listings:
            raise RuntimeError(f"only {len(listings)} NYC records")
        return Result(listings=listings)
