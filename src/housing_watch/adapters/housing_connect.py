"""NYC Housing Connect lotteries, from HPD's public API (the Angular site's own backend).

SearchLotteries returns every lottery currently accepting applications (`rentals` and `sales`); the
advertisement gives one `units[]` row per apartment, collapsed here into tiers (size, AMI, rent, income
table). Set-asides (mobility, vision/hearing) are a share of units, not specific apartments, so they go in
the notes. A lottery drops out of the search once its deadline passes. An ad is re-read only when its
search record changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict

from .. import fetch, parse
from ..models import Band, Listing, Result, Unit
from . import Adapter

API = "https://a806-housingconnectapi.nyc.gov/HPDPublicAPI/api/Lottery"
SEARCH_BODY = {
    "UnitTypes": [],
    "NearbyPlaces": [],
    "NearbySubways": [],
    "Amenities": [],
    "Applied": None,
    "HPDUserId": None,
    "Boroughs": [],
    "Neighborhoods": [],
    "HouseholdSize": None,
    "Income": "",
    "HouseholdType": 1,
    "OwnerTypes": [],
    "PreferanceTypes": [],
    "LotteryTypes": [],
    "Min": None,
    "Max": None,
    "RentalSubsidy": None,
}
PAGE = "https://housingconnect.nyc.gov/PublicWeb/details/{}"
_VOLATILE = {"endIn", "isApplied", "defaultPhoto", "defaultPhotoStream"}


def _tiers(units: list[dict]) -> list[Unit]:
    tiers: dict[tuple, Unit] = {}
    for u in units:
        bands = [
            Band(int(i["houseHoldSize"]), parse.whole(i.get("minimumIncome")), parse.whole(i.get("maximumIncome")))
            for i in sorted(u.get("unitIncome") or [], key=lambda i: i["houseHoldSize"])
        ]
        if not bands and u.get("minimumHouseholdSize"):  # no income table: at least keep the household floor
            bands = [Band(int(u["minimumHouseholdSize"]), None, None)]
        rent = parse.money(u.get("actualRent")) or None
        unit = Unit(size=parse.size(u.get("unitLayoutTypeName")), rent=rent, ami=u.get("unitRegulatoryMechanismAmi"))
        unit.bands = bands
        k = (unit.size, unit.ami, unit.rent, tuple((b.household, b.income_min, b.income_max) for b in bands))
        tiers.setdefault(k, unit)
    return list(tiers.values())


def parse_ad(rec: dict, ad: dict, sale: bool = False) -> Listing:
    """A search record + its GetLotteryAdvertisement response -> Listing."""
    bldgs = ad.get("lotteryBuildings") or []
    b0 = bldgs[0] if bldgs else {}
    m0 = (rec.get("markers") or [{}])[0]
    parts = (b0.get("address") or m0.get("address"), b0.get("city") or m0.get("city"))
    addr = re.sub(r"\s+", " ", ", ".join(x.strip() for x in parts if x and x.strip()))
    zip_ = b0.get("zip") or m0.get("zip") or ""
    boro = parse.borough(f"{addr} NY {zip_}") or parse.borough((rec.get("borough") or "").strip())
    name = re.sub(r"\s+", " ", ad.get("lotteryName") or rec.get("lotteryName") or "").strip()
    prefs = ad.get("lotterySetAsidePreferences") or []
    senior = any(p.get("minAgePreference") for p in prefs) or bool(
        re.search(r"\bsenior|62\s*(\+|years|and older)", f"{name} {ad.get('lotteryDescription') or ''}", re.I)
    )
    how = [f"Apply online at {PAGE.format(rec['lotteryId'])} (Housing Connect account; one application only)"]
    for t in ad.get("lotteryTasks") or []:
        for d in t.get("paperApplicationDelivery") or []:
            where = ", ".join(x.strip() for x in (d.get("address"), d.get("city"), d.get("state")) if x and x.strip())
            how.append(f"or paper: send a self-addressed envelope to {where} {d.get('zip') or ''}".rstrip())
    asides = [f"{p['name']} {p.get('requirementAllocation') or 0:g}%" for p in prefs if p.get("isSetAside")]
    if asides:
        how.append("set-asides: " + ", ".join(asides))
    if ad.get("isMitchelLamaLottery") or rec.get("isMitchelLamaLottery"):
        how.append("Mitchell-Lama waitlist lottery")
    end = ad.get("endDate") or rec.get("lotteryEndDate")
    return Listing(
        source="housing_connect",
        ext_id=str(rec["lotteryId"]),
        title=name,
        url=PAGE.format(rec["lotteryId"]),
        address=addr or None,
        borough=boro,
        status="open",
        kind="homeownership" if sale else "lottery",
        units=_tiers(ad.get("units") or []),
        deadline=end[:10] if end else None,
        senior_only=senior,
        notes="; ".join(how),
    )


def _from_dict(d: dict) -> Listing:
    units = [Unit(**{**u, "bands": [Band(**b) for b in u["bands"]]}) for u in d["units"]]
    return Listing(**{**d, "units": units})


def _fingerprint(rec: dict) -> str:
    stable = {k: v for k, v in rec.items() if k not in _VOLATILE}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()


class HousingConnect(Adapter):
    name = "housing_connect"
    title = "NYC Housing Connect"
    url = "https://housingconnect.nyc.gov/PublicWeb/search-lotteries"
    every_hours = 4
    min_listings = 3  # 18 open rental lotteries on 2026-10-19; it has never been near empty

    def fetch(self) -> Result:
        res = fetch.post_json(f"{API}/SearchLotteries", SEARCH_BODY, headers={"Accept": "application/json"})
        if "rentals" not in res:
            raise RuntimeError(f"unexpected SearchLotteries response: {list(res)[:5]}")
        recs = [(r, False) for r in res.get("rentals") or []] + [(r, True) for r in res.get("sales") or []]
        cache: dict = self.cache.setdefault("ads", {})
        out = []
        for rec, sale in recs:
            lid = str(rec["lotteryId"])
            fp = _fingerprint(rec)
            hit = cache.get(lid)
            if hit and hit.get("fp") == fp:
                out.append(_from_dict(hit["listing"]))
                continue
            ad = fetch.json(f"{API}/GetLotteryAdvertisement?lotteryId={lid}")
            lst = parse_ad(rec, ad, sale)
            cache[lid] = {"fp": fp, "listing": asdict(lst)}
            out.append(lst)
        live = {str(r["lotteryId"]) for r, _ in recs}
        for k in [k for k in cache if k not in live]:
            del cache[k]
        return Result(listings=out)
