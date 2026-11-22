import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from housing_watch import adapters, parse
from housing_watch.adapters import PLAN, sanel
from housing_watch.adapters.sanel import Sanel, parse_page, reside_buildings, reside_match, tier_units
from housing_watch.eligibility import listing_verdict
from housing_watch.fetch import FetchError
from housing_watch.fleet import runner
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "sanel"
BASE = "https://sanel.nyc/affordable-housing/"


def url(name):
    return BASE + name.replace("__", "/")


def page(name):
    return parse_page(url(name), (F / f"{name}.html").read_text())


def test_lottery_tiers_grouped_per_household():
    lst = page("queens__domi-1224sz")
    assert (lst.title, lst.borough, lst.kind, lst.status, lst.deadline) == (
        "DOMI",
        "Queens",
        "lottery",
        "open",
        "2026-11-10",
    )
    assert lst.address == "29-24 40th Ave, Queens, NY 11101"
    assert "housingconnect.nyc.gov/PublicWeb/details/7693" in lst.notes
    assert [(u.size, u.rent, u.ami) for u in lst.units] == [
        ("studio", 1842, 70),
        ("1br", 1962, 70),
        ("1br", 3784, 130),
        ("2br", 4518, 130),
        ("3br", 5200, 130),
    ]
    u130 = lst.units[2]
    assert u130.bands[:2] == [Band(1, 135360, 154440), Band(2, 135360, 176410)]
    v = listing_verdict(lst)
    assert v.verdict == "eligible"
    assert [u.ami for u, uv, _ in v.units if uv == "eligible"] == [130]


def test_multi_household_row_uses_per_size_cap():
    lst = page("manhattan__waterside-plaza-unit-10-18k-4d4ya4")
    assert lst.borough == "Manhattan" and lst.deadline == "2027-08-10"
    (u,) = lst.units
    assert (u.size, u.rent, u.ami) == ("1br", 3094, 160)
    # "1–3 people $112,046 – $244,320": $244,320 is the 3-person cap, 1 person caps at 160% = $190,080
    assert u.bands == [Band(1, 112046, 190080), Band(2, 112046, 217120), Band(3, 112046, 244320)]
    assert listing_verdict(lst).verdict == "eligible"


def test_old_year_limits_detected():
    # "130%, 1–2 people, $77,303 – $168,480": $168,480 is the 2025 2-person cap, so 1 person is $147,420
    lst = page("queens__sven-unit-21n-jhmw82")
    (u,) = lst.units
    assert u.size == "studio" and u.bands == [Band(1, 77303, 147420), Band(2, 77303, 168480)] and u.chart_year == 2025
    assert (
        listing_verdict(lst).units[0][2] == "eligible on 2026 limits (ad prints 2025 limits: max $147,420)"
        and "2025 chart" in lst.notes
    )


def test_facts_income_single_tier():
    # no tier table, but the facts block's Household/Income/AMI describe the one tier exactly
    lst = page("queens__one-lic-multiple-units-available-0926-unit-13d-1vl36e")
    (u,) = lst.units
    assert (u.size, u.rent, u.ami) == ("1br", 2704, 130)
    assert u.bands == [Band(1, 98590, 154440), Band(2, 98590, 176410), Band(3, 98590, 198510)]
    assert listing_verdict(lst).verdict == "eligible"


def test_facts_income_without_household_on_2025_chart():
    # Taxace prints no household range: $189,540 is the 2025 3-person cap at 130%
    lst = page("brooklyn__962-bushwick-avenue-unit-2a-1ron8b")
    assert [b.income_max for b in lst.units[0].bands] == [147420, 168480, 189540]
    assert lst.units[0].chart_year == 2025 and "2025 chart" in lst.notes
    assert listing_verdict(lst).units[0][2] == "eligible on 2026 limits (ad prints 2025 limits: max $147,420)"


def test_facts_income_range_of_caps():
    # "1–3 people, 40%, $45,360–$58,320" runs from the 1-person cap to the 3-person cap: no minimum
    (u,) = page("brooklyn__bridge-rockaway-x2fass").units
    assert u.bands[0] == Band(1, None, 45360)


def test_facts_income_multi_tier_is_an_upper_bound():
    # Studio·1BR·2BR, $68,126–$198,510 spans tiers: the cap bounds every tier, the minimum is a cheap tier's
    lst = page("brooklyn__1832-fulton-street-apartments-121mtv")
    assert [u.size for u in lst.units] == ["studio", "1br", "2br"]
    assert lst.units[0].bands[0] == Band(1, None, 154440)
    assert listing_verdict(lst).reason == "income range incomplete"


def test_bronx_excluded_by_url_borough():
    lst = page("bronx__86-west-174-st-unit-3b-1l583o")
    assert lst.borough == "Bronx" and listing_verdict(lst).reason == "in Bronx"


def test_no_tier_table_is_possible():
    lst = page("queens__apex-place-apartments-lsp339")
    assert lst.borough == "Queens" and lst.kind == "re-rental"
    assert [u.size for u in lst.units] == ["1br", "2br"]
    assert listing_verdict(lst).verdict == "possible"


def test_waitlist_without_details():
    lst = page("queens__aura-1bpj4o")
    assert (lst.kind, lst.status) == ("waitlist", "waitlist")
    assert [u.size for u in lst.units] == ["unknown"]
    assert "prontohousingrentals.com/listings/property/12030" in lst.notes
    assert listing_verdict(lst).verdict == "possible"


def test_closed_senior_sale_and_preference():
    closed = page("manhattan__lyra-waiting-list-2v6pjo")
    assert closed.status == "closed" and listing_verdict(closed).verdict == "excluded"
    senior = page("brooklyn__cabs-senior-45ors")
    assert senior.senior_only and listing_verdict(senior).reason == "senior only"
    sale = page("brooklyn__belle-gardens-gre5ps")
    assert sale.kind == "homeownership"
    # "Senior Preference 60+" is a preference, not a restriction
    pref = page("brooklyn__1718-president-street-unit-6d-sh3g4v")
    assert not pref.senior_only and listing_verdict(pref).reason == "2br not wanted"


def test_not_found_page_raises():
    with pytest.raises(ValueError):
        page("unknown-borough__1052-greene-ave-unit-4a-tuq7sq")


def test_fetch_caches_and_skips_404(monkeypatch):
    names = ["queens__domi-1224sz", "queens__aura-1bpj4o", "unknown-borough__1052-greene-ave-unit-4a-tuq7sq"]
    sitemap = "".join(f"<url><loc>{url(n)}</loc></url>" for n in names) + f"<url><loc>{BASE}queens</loc></url>"
    calls = []

    def get(u):
        calls.append(u)
        if u.startswith(BASE + "unknown-borough"):
            return None
        return (F / (u.removeprefix(BASE).replace("/", "__") + ".html")).read_text()

    monkeypatch.setattr(sanel.fetch, "html", lambda u, **kw: sitemap)
    monkeypatch.setattr(sanel.fetch, "json", lambda u, **kw: doc("pronto-12030.json"))
    monkeypatch.setattr(sanel, "_get", get)
    monkeypatch.setattr(sanel, "GAP_S", 0)
    monkeypatch.setattr(Sanel, "min_listings", 1)
    a = Sanel()
    assert sorted(x.title for x in a.fetch().listings) == ["Aura", "DOMI"]
    assert len(calls) == 3
    calls.clear()
    b = Sanel(a.cache)  # everything fresh: nothing refetched except the rolling share (len // 8 == 0)
    assert sorted(x.title for x in b.fetch().listings) == ["Aura", "DOMI"] and calls == []

    def boom(u):
        raise FetchError("down")

    monkeypatch.setattr(sanel, "_get", boom)
    monkeypatch.setattr(sanel, "MAX_AGE_H", -1)  # everything stale: a failed refetch keeps the cached copy
    assert sorted(x.title for x in Sanel(a.cache).fetch().listings) == ["Aura", "DOMI"]


def test_page_missing_from_one_sitemap_read_is_kept(monkeypatch):
    # SANEL's sitemap drops and re-adds a page between reads; it is gone only after MAX_AGE_H missing
    names = ["queens__domi-1224sz", "queens__aura-1bpj4o"]
    sitemap = {"xml": "".join(f"<url><loc>{url(n)}</loc></url>" for n in names)}
    monkeypatch.setattr(sanel.fetch, "html", lambda u, **kw: sitemap["xml"])
    monkeypatch.setattr(sanel.fetch, "json", lambda u, **kw: doc("pronto-12030.json"))
    monkeypatch.setattr(sanel, "_get", lambda u: (F / (u.removeprefix(BASE).replace("/", "__") + ".html")).read_text())
    monkeypatch.setattr(sanel, "GAP_S", 0)
    monkeypatch.setattr(Sanel, "min_listings", 1)
    a = Sanel()
    a.fetch()
    sitemap["xml"] = f"<url><loc>{url(names[0])}</loc></url>"
    assert sorted(x.title for x in Sanel(a.cache).fetch().listings) == ["Aura", "DOMI"]
    a.cache[url(names[1])]["absent"] -= sanel.MAX_AGE_H * 3600 + 1
    assert [x.title for x in Sanel(a.cache).fetch().listings] == ["DOMI"] and url(names[1]) not in a.cache


def doc(name):
    t = (F / name).read_text()
    return json.loads(t) if name.endswith(".json") else t


def enriched(name, monkeypatch, cache=None, **docs):
    """parse_page + enrich, with each official url answered from a fixture (any other url fails the test)."""

    def answer(u, **kw):
        k = next((k for k in docs if k in u), None)
        assert k, f"unexpected fetch {u}"
        return doc(docs[k])

    for f in ("json", "html", "pdf_text"):
        monkeypatch.setattr(sanel.fetch, f, answer)
    a = Sanel()
    a.cache.update(cache or {})
    return a.enrich(page(name), (F / f"{name}.html").read_text()), a


def test_hdc_flyer_replaces_summary(monkeypatch):
    lst, a = enriched("queens__apex-place-apartments-lsp339", monkeypatch, nychdc="hdc-apex-flyer.txt")
    assert [(u.size, u.rent, u.ami) for u in lst.units] == [("1br", 2206, 110), ("2br", 2550, 110), ("1br", 2618, 140)]
    assert listing_verdict(lst).verdict == "eligible" and "HDC's flyer" in lst.notes
    assert lst.official_url and lst.official_url.lower().endswith(".pdf")  # the flyer is the real posting
    assert any(k.startswith("doc:hdc:") for k in a.cache)  # cached: the next run does not re-read it


def test_unreadable_official_source_keeps_cached_units(monkeypatch):
    """A failed official read keeps the last enriched units, not SANEL's summary (which flapped the listing)."""
    name = "queens__apex-place-apartments-lsp339"
    monkeypatch.setattr(sanel.fetch, "html", lambda u, **kw: f"<url><loc>{url(name)}</loc></url>")
    monkeypatch.setattr(sanel.fetch, "pdf_text", lambda u, **kw: doc("hdc-apex-flyer.txt"))
    monkeypatch.setattr(sanel, "_get", lambda u: (F / f"{name}.html").read_text())
    monkeypatch.setattr(sanel, "GAP_S", 0)
    monkeypatch.setattr(sanel, "MAX_AGE_H", -1)  # every run re-reads the page
    monkeypatch.setattr(Sanel, "min_listings", 1)
    a = Sanel()
    (good,) = a.fetch().listings
    assert [(u.size, u.rent) for u in good.units] == [("1br", 2206), ("2br", 2550), ("1br", 2618)]

    def down(u, **kw):
        raise FetchError("down")

    monkeypatch.setattr(sanel.fetch, "pdf_text", down)
    cache = {k: v for k, v in a.cache.items() if not k.startswith("doc:")}  # the flyer must be re-read
    (again,) = Sanel(cache).fetch().listings
    assert (again.units, again.notes) == (good.units, good.notes)
    (first,) = Sanel().fetch().listings  # nothing cached yet: SANEL's summary, said so
    assert first.units == page(name).units and "unreadable this run" in first.notes


def test_housing_connect_ad_replaces_summary(monkeypatch):
    lst, _ = enriched(
        "brooklyn__1832-fulton-street-apartments-121mtv", monkeypatch, GetLotteryAdvertisement="hc-7643.json"
    )
    assert len(lst.units) == 7 and listing_verdict(lst).verdict == "eligible"
    assert next(u for u in lst.units if u.ami == 130 and u.size == "studio").bands[0] == Band(1, 94869, 154440)


def test_pronto_waitlist_bands(monkeypatch):
    lst, _ = enriched("queens__aura-1bpj4o", monkeypatch, prontohousing="pronto-12030.json")
    assert not any(u.unit_no for u in lst.units)  # the waitlist's tiers, not its named units
    assert Band(1, 125966, 154440) in lst.units[0].bands and "Income bands from Pronto" in lst.notes


def test_pronto_registration_link_and_stamped_caps(monkeypatch):
    # SANEL links the SVEN waitlist by registration uuid; Pronto stamps $227,500 (2025, 5 people) on every row
    lst, _ = enriched(
        "queens__sven-fpyq55", monkeypatch, **{"search": "pronto-search.json", "properties/4934": "pronto-4934.json"}
    )
    top = next(u for u in lst.units if u.bands[-1].income_max == 210600)
    assert [b.income_max for b in top.bands] == [147420, 168480, 189540, 210600] and top.chart_year == 2025
    assert listing_verdict(lst).verdict == "eligible" and "2025 chart" in lst.notes  # 2026's 1-person 130% fits
    assert lst.official_url == "https://prontohousingrentals.com/listings/property/4934"  # not the registration page


def test_mitchell_lama_from_hpd(monkeypatch):
    lst, _ = enriched(
        "staten-island__arlington-terrace-1qm7aq",
        monkeypatch,
        **{"mitchell-lama-program": "ml-program.html", "ML-waiting": "ml-open-lists.txt"},
    )
    assert lst.units and all(b.income_max for u in lst.units for b in u.bands)
    assert listing_verdict(lst).verdict == "excluded"


def test_reside_root_link_matched_by_unit(monkeypatch):
    urls = [
        "https://residenewyork.com/property/36-10-31st-street-apartments/",
        "https://residenewyork.com/property/36-10-31-street-apartment-unit-3a/",
        "https://residenewyork.com/property/36-10-31st-street-apartment-unit-3b/",
    ]
    assert reside_match("36-10 31st Street #3A", urls) == urls[1]
    assert (
        reside_match(
            "1440 Amsterdam Avenue #1", ["https://residenewyork.com/property/1471-amsterdam-avenue-apartments/"]
        )
        is None
    )
    monkeypatch.setattr(Sanel, "_reside_urls", lambda self: urls)
    lst, _ = enriched(
        "queens__36-10-31st-street-unit-3a-1qm2xf", monkeypatch, **{"unit-3a": "reside-36-10-31-unit-3a.html"}
    )
    assert lst.units[0].bands[0] == Band(1, 120011, 154440) and listing_verdict(lst).verdict == "eligible"
    assert lst.official_url == urls[1]  # SANEL linked Reside's home page; the unit page is the posting


def test_reside_unit_page_newer_than_cached_list(monkeypatch):
    unit = "https://residenewyork.com/property/36-10-31-street-apartment-unit-3a/"
    monkeypatch.setattr(Sanel, "_reside_urls", lambda self: [unit])
    old = {"doc:reside": {"fetched": time.time() - 2 * 3600, "data": []}}  # read before the unit page existed
    lst, _ = enriched(
        "queens__36-10-31st-street-unit-3a-1qm2xf", monkeypatch, old, **{"unit-3a": "reside-36-10-31-unit-3a.html"}
    )
    assert listing_verdict(lst).verdict == "eligible"


def test_reside_building_tier_at_unit_rent(monkeypatch):
    urls = [
        "https://residenewyork.com/property/28-08-38th-avenue-apartments/",  # header range only: no tier
        "https://residenewyork.com/property/28-08-38th-avenue-apartment-om/",
        "https://residenewyork.com/property/2588-7th-ave-apartment-unit-4j/",
    ]
    assert reside_buildings("28-08 38th Avenue #2F", urls) == urls[:2]
    assert reside_buildings("2588 7th Ave #4N", urls) == []
    monkeypatch.setattr(Sanel, "_reside_urls", lambda self: urls)
    lst, _ = enriched(
        "queens__28-08-38th-avenue-unit-2f-1jnbzw",
        monkeypatch,
        **{
            "apartment-om": "reside-28-08-38th-avenue-apartment-om.html",
            "avenue-apartments": "reside-28-08-38th-avenue-apartments.html",
        },
    )
    (u,) = lst.units  # SANEL: $3,423, size unknown; the building table's $3,423 row is a 130% studio
    assert (u.size, u.rent, u.ami, u.bands[0]) == ("studio", 3423, 130, Band(1, 122298, 154440))
    assert listing_verdict(lst).verdict == "eligible" and "building page" in lst.notes


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_shf_unit_from_re_rental_form(monkeypatch, shf_tables):
    shf = Path(__file__).parent / "fixtures" / "settlement_housing_fund"
    form = json.loads((shf / "form.json").read_text())
    pngs = {
        q["image"]["resourceUrl"]: "table-130.png" if "130%" in q["title"] else "table-40-60.png"
        for q in form["questions"]
        if q["type"] == "Question.Choice" and q["image"]["resourceUrl"]
    }
    monkeypatch.setattr(sanel.fetch, "get", lambda u, **kw: SimpleNamespace(content=pngs[u]))
    monkeypatch.setattr(sanel.shf, "ocr_table", shf_tables.__getitem__)  # the OCR itself is tested in its own file
    docs = {
        "find-housing": "../settlement_housing_fund/find-housing.html",
        "formapi": "../settlement_housing_fund/form.json",
    }
    lst, a = enriched("brooklyn__30-kent-street-unit-310-11i1pi", monkeypatch, **docs)
    (u,) = lst.units
    assert (u.size, u.unit_no, u.rent, u.bands[0]) == ("1br", "310", 2858, Band(1, 101692, 154440))
    assert listing_verdict(lst).verdict == "eligible" and "doc:shf" in a.cache


def test_tax_solute_card(monkeypatch):
    # the card prints household 1-3 but income rows from 2 people only
    lst, _ = enriched("brooklyn__217-greene-st-2ctnxc", monkeypatch, affordablelivingnyc="tax-solute-home.html")
    assert lst.units[0].rent == 2773.15 and listing_verdict(lst).reason == "needs household of 2+"


def test_st_nicks_senior_building(monkeypatch):
    lst, _ = enriched("brooklyn__ellery-court-eosufd", monkeypatch, stnicksalliance="../st_nicks/index.html")
    assert lst.senior_only and listing_verdict(lst).reason == "senior only"


def test_nothing_public_is_explained(monkeypatch):
    lst, _ = enriched("brooklyn__270-pulaski-street-u12rpt", monkeypatch, stnicksalliance="../st_nicks/index.html")
    assert listing_verdict(lst).reason == "no income table" and "lists no rents or income limits" in lst.notes


def test_official_source_down_keeps_summary(monkeypatch):
    def down(u, **kw):
        raise FetchError("down")

    monkeypatch.setattr(sanel.fetch, "pdf_text", down)
    lst = Sanel().enrich(
        page("queens__apex-place-apartments-lsp339"), (F / "queens__apex-place-apartments-lsp339.html").read_text()
    )
    assert [u.size for u in lst.units] == ["1br", "2br"] and "unreadable" in lst.notes


def test_concurrent_chunks_keep_each_others_documents(monkeypatch):
    names = ["queens__domi-1224sz", "queens__aura-1bpj4o", "queens__apex-place-apartments-lsp339"]
    sitemap = "".join(f"<url><loc>{url(n)}</loc></url>" for n in names)
    monkeypatch.setattr(sanel.fetch, "html", lambda u, **kw: sitemap)
    monkeypatch.setattr(sanel.fetch, "json", lambda u, **kw: doc("pronto-12030.json"))
    monkeypatch.setattr(sanel.fetch, "pdf_text", lambda u, **kw: doc("hdc-apex-flyer.txt"))
    monkeypatch.setattr(sanel, "_get", lambda u: (F / (u.removeprefix(BASE).replace("/", "__") + ".html")).read_text())
    monkeypatch.setattr(sanel, "GAP_S", 0)
    monkeypatch.setattr(Sanel, "min_listings", 1)
    monkeypatch.setattr(adapters, "CHUNK_LEAST", 1)
    a = Sanel({"docs": {"ml": {"fetched": 0, "data": {}}}})  # older layout, and a document unused for a week
    assert a.chunks() == ["1/3", "2/3", "3/3"] and "docs" not in a.cache and "doc:ml" not in a.cache
    res, cache = runner.fetch_all(Sanel())  # one page per chunk, each chunk on its own copy of the cache
    assert sorted(x.title for x in res.listings) == ["APEX PLACE APARTMENTS", "Aura", "DOMI"]
    # every chunk's documents (tier-table pages read their official source too: "hc")
    assert {k.split(":")[1] for k in cache if k.startswith("doc:")} == {"pronto", "hdc", "hc"}
    assert set(cache) - {PLAN} >= {url(n) for n in names}


def test_official_units_beat_a_lossy_tier_table(monkeypatch):
    """SANEL's tier table rounds the rent, drops the unit number and skips 2 people; Reside's unit page has all."""
    name = "bronx__2140-matthews-ave-unit-4a-10ostm"
    assert [b.household for b in tier_units(parse.soup(doc(name + ".html")))[0].bands] == [1, 3]
    lst, _ = enriched(name, monkeypatch, **{"unit-4a": "reside-2140-matthews-unit-4a.html"})
    (u,) = lst.units
    assert (u.unit_no, u.rent, [b.household for b in u.bands]) == ("4A", 2941.02, [1, 2, 3])
    assert "Unit details from Reside's page" in lst.notes


def test_a_tier_table_stands_when_the_official_source_fails(monkeypatch):
    name = "bronx__2140-matthews-ave-unit-4a-10ostm"
    html = doc(name + ".html")

    def down(u, **kw):
        raise FetchError(f"GET {u}: 503")

    monkeypatch.setattr(sanel.fetch, "html", down)
    lst = Sanel({}).enrich(parse_page(url(name), html), html)
    (u,) = lst.units
    assert (u.unit_no, u.rent, [b.household for b in u.bands]) == (None, 2941.0, [1, 3])


def test_for_unit_keeps_only_the_listings_own_unit():
    from housing_watch.adapters.sanel import for_unit
    from housing_watch.models import Listing, Unit

    def lst(title, rent=None):
        return Listing("sanel", "x", title, "u", units=[Unit("1br", rent)] if rent else [])

    building = [
        Unit("1br", 2593.0, 130, "20-07K"),
        Unit("1br", 3094.0, 160, "10-14E"),
        Unit("3br", 5037.0, 160, "40-25C"),
    ]
    assert for_unit(lst("Waterside Plaza #20-07K"), building) == [building[0]]  # Pronto answers for the building
    assert for_unit(lst("Waterside Plaza #99-99Z"), building) is None  # named units, none of them this one
    assert for_unit(lst("Waterside Plaza"), building) == building  # the listing is the building
    tiers = [Unit("1br", 2593.0, 130), Unit("2br", 3100.0, 130)]
    assert for_unit(lst("Waterside Plaza #20-07K"), tiers) == tiers  # tiers name no unit
    repeated = [Unit("1br", 2244.0, 80, "002A"), Unit("2br", 2700.0, 80, "002A")]  # one number, two addresses
    assert for_unit(lst("Eastchester Heights #002A", 2244.0), repeated) == [repeated[0]]
    assert for_unit(lst("Eastchester Heights #002A"), repeated) is None  # no rent to tell them apart
    same = [Unit("1br", 2244.0, 80, "002A"), Unit("1br", 2244.0, 80, "002A")]
    assert for_unit(lst("Eastchester Heights #2A", 2244.0), same) == [same[0]]  # identical: either is this one


def test_official_url_from_the_page_and_from_older_cached_notes():
    from housing_watch.adapters.sanel import _official

    name = "bronx__2140-matthews-ave-unit-4a-10ostm"
    lst = parse_page(url(name), doc(name + ".html"))
    assert lst.official_url == "https://residenewyork.com/property/2140-matthews-ave-apartment-unit-4a/"
    assert Sanel.aggregator and _official(lst)[1] == lst.official_url  # a cached copy without the field has it
