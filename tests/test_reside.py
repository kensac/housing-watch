from pathlib import Path

import pytest

from housing_watch import adapters, runner
from housing_watch.adapters import PLAN, reside
from housing_watch.adapters.reside import Reside, parse_page
from housing_watch.criteria import listing_verdict, unit_verdict

F = Path(__file__).parent / "fixtures" / "reside"


def page(name):
    return parse_page(f"https://residenewyork.com/property/{name}/", (F / f"{name}.html").read_text())


def test_open_studio_unit():
    lst = page("115-stanwix-st-apartment-unit-218")
    assert lst.status == "open" and lst.borough == "Brooklyn"
    (u,) = lst.units
    assert (u.size, u.unit_no, u.rent, u.ami) == ("studio", "218", 2407.89, 130)
    assert (u.bands[0].household, u.bands[0].income_min, u.bands[0].income_max) == (1, 82557, 154440)
    assert listing_verdict(lst).verdict == "eligible"


def test_multi_unit_page():
    lst = page("25-01-queens-plaza-north-units-619-325-and-503")
    assert lst.borough == "Queens"
    assert [u.size for u in lst.units] == ["studio", "1br", "2br"]
    assert listing_verdict(lst).verdict == "eligible"


def test_closed_project():
    lst = page("closed")
    assert lst.status == "closed" and listing_verdict(lst).verdict == "excluded"


def test_page_without_status_label_is_unknown_not_open():
    # 24 Wilson Ave 3D (2026-10-19): no property-status term and no label; Reside's form no longer offers it
    lst = page("no-status-label")
    assert lst.status == "unknown" and [u.size for u in lst.units] == ["1br"]
    v = listing_verdict(lst)
    assert v.verdict == "excluded" and "no status" in v.reason


def test_upcoming_project():
    lst = page("1-9-putnam-avenue")
    assert lst.status == "upcoming" and lst.borough == "Brooklyn"


def test_lxml_namespace_crash_falls_back():
    # this page makes bs4's lxml builder raise ValueError in _getNsTag
    assert page("100-freedom-place-apartment-unit-4p").title.startswith("100 Freedom Place")


def bands(u):
    return [(b.household, b.income_min, b.income_max) for b in u.bands]


def test_word_pasted_table_and_continuation_rows():
    # curly quotes close the cells' attributes; the rows under the unit carry its 2- and 3-person caps
    lst = page("210-w-150-street-apartment-unit-1e")
    (u,) = lst.units
    assert (u.size, u.unit_no, u.rent, u.ami) == ("1br", "1E", 2370.74, 150)
    assert bands(u) == [(1, 86152, 170100), (2, 86152, 194400), (3, 86152, 218700)]
    # the page carries no status label, and Reside's form does not offer #1E: not marketed, so not open
    assert lst.borough == "Manhattan" and lst.status == "unknown" and listing_verdict(lst).verdict == "excluded"


def test_multi_value_cells():
    (u,) = page("2-blue-slip-apartment-unit-7n").units
    assert u.size == "studio" and bands(u) == [(1, 83492, 147420), (2, 83492, 168480)]


def test_min_and_labeled_max_columns():
    (u,) = page("dutch-house-37-05-30th-street-apartment-unit-205").units
    assert bands(u) == [(1, 104442, 147420), (2, 104442, 168480), (3, 104442, 189540)]


def test_unlabeled_max_column_starts_at_the_matching_household():
    # $136,080 is 105% of the 2-person limit; the address zip beats the page's wrong "bronx" city term
    lst = page("dunbar-apartments-246-west-150th-street-apartment-unit-1e")
    assert lst.borough == "Manhattan"
    assert [b.household for b in lst.units[0].bands] == [2, 3, 4, 5]
    assert listing_verdict(lst).verdict == "excluded"


def test_header_range_without_table():
    # $189,540 is 130% of the 2025 3-person limit, so 1 person caps at 130% of the 2025 1-person limit
    lst = page("183-03-hillside-avenue-apartments")
    (u,) = lst.units
    assert lst.status == "upcoming" and lst.borough == "Queens"
    assert (u.size, u.rent) == ("unknown", 2722) and bands(u)[0] == (1, 99738, 147420)
    assert "rent $2,722-$2,823" in lst.notes and u.chart_year == 2025
    assert listing_verdict(lst).reason == "upcoming, details not published yet"
    assert unit_verdict(u)[1] == "income fits on 2026 limits (ad prints 2025 limits: max $147,420), size unknown"


def test_header_range_with_floor_plan_sizes():
    lst = page("101-macombs-place-apartments")
    assert [u.size for u in lst.units] == ["1br", "2br"] and bands(lst.units[0])[0] == (1, 32868, 119030)


def test_rent_only_page():
    lst = page("1692-pacific-street-apartment")
    (u,) = lst.units
    assert (u.size, u.rent, u.bands) == ("unknown", 2406, []) and "only rent" in lst.notes


def test_placeholder_page_says_so():
    lst = page("2512-newkirk-ave-unit-4b")
    assert lst.units == [] and "publishes no unit sizes, rent or income" in lst.notes
    assert lst.status == "unknown" and "no status" in listing_verdict(lst).reason


def test_bronx_by_address_zip_over_wrong_city_term():
    lst = page("781-elsemere-place-ml-6a")
    assert lst.borough == "Bronx" and listing_verdict(lst).verdict == "excluded"


def test_status_from_article_terms():
    # no visible label; the property-status-closed-project term on the <article> decides
    assert page("379-harman-street-apartment-unit-4h").status == "closed"


FIX = sorted(p.stem for p in F.glob("*.html"))


@pytest.fixture
def site(monkeypatch):
    """A fake Reside of 40 pages (the fixtures, repeated) over its two sitemaps: (url -> lastmod, fetched urls)."""
    pages = {f"https://residenewyork.com/property/p{i}-{FIX[i % len(FIX)]}/": "2026-10-01" for i in range(40)}
    calls: list[str] = []

    def html(u, **kw):
        if u in reside.SITEMAPS:
            items = list(pages.items())[::2] if u == reside.SITEMAPS[0] else list(pages.items())[1::2]
            return "".join(f"<url><loc>{x}</loc><lastmod>{lm}</lastmod></url>" for x, lm in items)
        calls.append(u)
        return (F / f"{u.rstrip('/').rsplit('/', 1)[-1].split('-', 1)[1]}.html").read_text()

    monkeypatch.setattr(reside.fetch, "html", html)

    def no_form():
        raise reside.fetch.FetchError("form not available in tests")

    monkeypatch.setattr(reside, "read_form", no_form)  # statuses from the page labels unless a test sets a form
    monkeypatch.setattr(Reside, "min_listings", 1)
    monkeypatch.setattr(adapters, "CHUNK_LEAST", 5)
    return pages, calls


def test_cold_run_spreads_every_page_over_parallel_chunks(site):
    pages, _ = site
    a = Reside()
    assert a.chunks() == ["1/4", "2/4", "3/4", "4/4"]
    plan = a.cache[PLAN]
    assert [len(p["fetch"]) for p in plan.values()] == [10, 10, 10, 10]
    assert sorted(u for p in plan.values() for u, _ in p["fetch"]) == sorted(pages)


def test_warm_run_fetches_only_changed_pages_and_keeps_the_rest(site):
    pages, calls = site
    a = Reside()
    assert len(a.fetch().listings) == 40 and len(calls) == 40
    calls.clear()
    b = Reside(dict(a.cache))
    assert b.chunks() == ["1/1"] and b.cache[PLAN]["1/1"]["fetch"] == []  # nothing changed: cached listings only
    assert len(b.fetch_chunk("1/1").listings) == 40 and calls == []
    moved, gone = list(pages)[:2], list(pages)[-1]
    for u in moved:
        pages[u] = "2026-10-02"
    del pages[gone]
    c = Reside(dict(a.cache))
    assert c.chunks() == ["1/1"]  # 2 pages are below CHUNK_LEAST: one chunk
    res = c.fetch_chunk("1/1")
    assert sorted(calls) == sorted(moved) and gone not in c.cache  # gone from the sitemap: dropped
    assert len(res.listings) == 39 and gone.rstrip("/").rsplit("/", 1)[-1] not in {x.ext_id for x in res.listings}


def test_parser_bump_and_old_layout(site, monkeypatch):
    pages, calls = site
    a = Reside()
    a.fetch()
    calls.clear()
    old = {"pages": {u: a.cache[u] for u in pages}}  # the layout before chunking: one key for every page
    b = Reside(old)
    assert b.chunks() == ["1/1"] and "pages" not in b.cache and len(b.fetch_chunk("1/1").listings) == 40
    assert calls == []
    monkeypatch.setattr(reside, "PARSER", reside.PARSER + 1)
    assert len(Reside(dict(a.cache)).chunks()) == 4  # every page re-parsed


def test_chunks_merged_from_separate_workers_match_one_process(site):
    pages, _ = site
    serial = Reside()
    want = serial.fetch()
    res, cache = runner.fetch_all(Reside())  # each chunk on its own copy of the cache, then merge_cache()
    assert sorted(x.ext_id for x in res.listings) == sorted(x.ext_id for x in want.listings)
    assert {k: v for k, v in cache.items() if k != PLAN} == {k: v for k, v in serial.cache.items() if k != PLAN}
    assert set(cache) - {PLAN} == set(pages)


def test_a_failed_page_keeps_its_last_copy_and_is_retried(site, monkeypatch):
    pages, calls = site
    a = Reside()
    a.fetch()
    flaky, new = list(pages)[0], "https://residenewyork.com/property/p99-" + FIX[0] + "/"
    pages[flaky], pages[new] = "2026-10-02", "2026-10-02"
    ok = reside.fetch.html

    def html(u, **kw):
        if u in (flaky, new):
            raise reside.fetch.FetchError(f"GET {u}: The read operation timed out")
        return ok(u, **kw)

    monkeypatch.setattr(reside.fetch, "html", html)
    b = Reside(dict(a.cache))
    res = b.fetch_chunk(b.chunks()[0])
    assert len(res.listings) == 40  # the flaky page's old listing kept, the new one not yet
    assert b.cache[flaky]["lastmod"] == "2026-10-01" and new not in b.cache  # both fetched again next run
    monkeypatch.setattr(reside, "MAX_FAILED", 1)
    c = Reside(dict(a.cache))
    with pytest.raises(RuntimeError, match="2 Reside pages failed"):
        c.fetch_chunk(c.chunks()[0])


FORM_UNITS = (F / "form_units.txt").read_text().splitlines()  # Reside's form picker on 2026-10-19, 23:40 ET


def test_form_option_labels():
    assert reside.form_option("2 Blue Slip - Apt 9D - $2589.74 -") == ({"2 blue slip"}, {"9D"}, 2589.74)
    assert reside.form_option("2180 Walton Avenue - Apt. 10D - Apt 10B - $3128 -")[1] == {"10D", "10B"}
    assert reside.form_option("36-10 31st Street - Unit 3A - Apt 3A - $3308.31 -")[:2] == ({"36-10 31 st"}, {"3A"})
    assert reside.form_option("3317 Parkside PL Apt 3B - Apt 3B - $1857.63 -")[:2] == ({"3317 parkside pl"}, {"3B"})
    assert reside.form_option("647 Sheffield Ave- Apt. 1A - Apt 1A - $2800.41 -")[:2] == ({"647 sheffield ave"}, {"1A"})
    assert reside.form_option("2061Crotona - Apt 4B - $3787.95 -")[:2] == ({"2061 crotona"}, {"4B"})
    assert reside.form_option("1281 Hoe Ave - Apt 7E - $1601.25 - Senior Preferece 60+")[2] == 1601.25
    assert all(reside.form_option(x)[0] and reside.form_option(x)[1] for x in FORM_UNITS), "every label parses"


def test_the_form_decides_open():
    options = [reside.form_option(x) for x in FORM_UNITS]
    offered = reside.with_form(page("115-stanwix-st-apartment-unit-218"), options)  # in the form
    assert offered.status == "open" and listing_verdict(offered).verdict == "eligible"
    gone = reside.with_form(page("210-w-150-street-apartment-unit-1e"), options)  # not in the form
    assert gone.status == "unknown" and "does not offer" in gone.notes
    up = page("1-9-putnam-avenue")
    assert reside.with_form(up, options).status == "upcoming"  # upcoming projects are never in the form


def test_a_chunked_run_applies_the_form(site, monkeypatch):
    pages, _ = site
    monkeypatch.setattr(reside, "read_form", lambda: FORM_UNITS)
    a = Reside()
    res = a.fetch()
    by = {x.ext_id: x for x in res.listings}
    assert reside.FORM in a.cache and a.cache[reside.FORM]["units"] == FORM_UNITS
    stanwix = [x for k, x in by.items() if k.endswith("115-stanwix-st-apartment-unit-218")]
    assert stanwix and all(x.status == "open" for x in stanwix)
    w150 = [x for k, x in by.items() if k.endswith("210-w-150-street-apartment-unit-1e")]
    assert w150 and all(x.status == "unknown" for x in w150)
    b = Reside(dict(a.cache))  # a warm run keeps judging cached listings against the form
    monkeypatch.setattr(reside, "read_form", lambda: [x for x in FORM_UNITS if "Stanwix" not in x])
    again = {x.ext_id: x for x in b.fetch().listings}
    assert all(x.status == "unknown" for k, x in again.items() if k.endswith("115-stanwix-st-apartment-unit-218"))


def test_a_stale_form_is_not_used(site, monkeypatch):
    a = Reside()
    a.cache[reside.FORM] = {"at": 0, "units": FORM_UNITS}  # 1970: far older than FORM_MAX_AGE
    assert a._form_options() is None


def test_multi_unit_page_takes_the_units_the_form_dropped():
    # "Units 619, 325, and 503": the form (2026-10-22) offers 325 ($3,299, the studio) and 503 ($4,316, the 2BR)
    lst = page("25-01-queens-plaza-north-units-619-325-and-503")
    assert lst.address == "25-01 Queens Plaza North" and reside.parse.units(lst.title) == {"619", "325", "503"}
    labels = [f"25-01 Queens Plaza North - Apt {n} - ${r} -" for n, r in (("325", 3299), ("503", 4316), ("905", 3653))]
    got = reside.with_form(lst, [reside.form_option(x) for x in labels])
    assert got.status == "open" and [u.available for u in got.units] == [None, False, None]
    assert "no longer offers the 1br unit(s)" in got.notes
    v = listing_verdict(got)
    assert v.verdict == "eligible" and [r for _, _, r in v.units][1] == "unit marked rented/closed"
    labels[0] = "25-01 Queens Plaza North - Apt 325 - $3310 -"  # a rent the page does not print: no pairing
    got = reside.with_form(lst, [reside.form_option(x) for x in labels])
    assert got.status == "open" and all(u.available is None for u in got.units)


def test_size_labels_match_a_page_without_unit_numbers_by_rent():
    # the form names 28-08 38th Avenue's units "Apt 1 BR" / "Apt 2 BR"; the page names none, so rents pair them
    lst = page("28-08-38th-avenue-apartment-om")
    assert [(u.size, u.rent, u.unit_no) for u in lst.units] == [
        ("studio", 3423, None),
        ("1br", 3661, None),
        ("2br", 4376, None),
    ]
    options = [reside.form_option(x) for x in FORM_UNITS]  # 2026-10-19: also "Apt 2F - $3423", the studio
    assert all(u.available is None for u in reside.with_form(lst, options).units)
    options = [o for o in options if o[2] != 3423]  # 2026-10-22: the studio is gone
    got = reside.with_form(lst, options)
    assert got.status == "open" and [u.available for u in got.units] == [False, None, None]
    assert listing_verdict(got).verdict == "eligible"


def test_rent_alone_never_matches_a_page_that_names_its_unit():
    lst = page("2-blue-slip-apartment-unit-7n")
    (u,) = lst.units
    assert reside.with_form(lst, [reside.form_option(f"2 Blue Slip - Apt 9D - ${u.rent} -")]).status == "unknown"
