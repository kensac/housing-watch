from pathlib import Path

from housing_watch.adapters.spring import build_listings, units_from_tsv
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "spring"


def tsv(url):
    return (F / (url.rsplit("/", 1)[-1].rsplit(".", 1)[0] + ".tsv")).read_text()


def listings():
    return {lst.ext_id: lst for lst in build_listings((F / "apartments-for-rent.html").read_text(), tsv)}


def bands(u):
    return [(b.household, b.income_min, b.income_max) for b in u.bands]


def test_sections_and_buildings():
    ls = listings()
    assert sorted(ls) == [
        "1488-new-york-avenue-available-units",
        "radroc-immediate-leasing",
        "radroc-waiting-list",
        "the-bedford-waiting-list",
    ]
    senior = ls["1488-new-york-avenue-available-units"]
    assert senior.senior_only and senior.borough == "Brooklyn" and senior.status == "open"
    assert ls["the-bedford-waiting-list"].borough == "Bronx"
    assert ls["the-bedford-waiting-list"].status == "waitlist"
    assert "marketing@springleasing.net" in senior.notes
    assert all(listing_verdict(x).verdict == "excluded" for x in ls.values())


def test_senior_section8_studio_zero_minimum():
    (u,) = listings()["1488-new-york-avenue-available-units"].units
    assert (u.size, u.rent, u.ami) == ("studio", None, 50)  # 50% read off the HUD cap (vertical label)
    assert bands(u) == [(1, 0, 56700), (2, 0, 64800)]


def test_radroc_side_by_side_tables():
    units = listings()["radroc-immediate-leasing"].units
    got = [(u.size, u.rent, u.ami, bands(u)) for u in units]
    assert got == [
        ("studio", 1258.0, 70, [(1, 46447, 79380), (2, 46447, 90720)]),
        ("studio", 1258.0, 80, [(1, 46447, 90720), (2, 46447, 103680)]),
        ("1br", 1584.0, 70, [(1, 58059, 79380), (2, 58059, 90720), (3, 58059, 102060)]),
        ("1br", 1584.0, 80, [(1, 58059, 90720), (2, 58059, 103680), (3, 58059, 116640)]),
    ]


def test_waitlist_range_rows_never_use_7_person_max_for_1():
    units = listings()["radroc-waiting-list"].units
    eighty = [u for u in units if u.ami == 80]
    assert [u.size for u in eighty] == ["studio", "1br", "2br", "3br"]
    assert (eighty[0].rent, eighty[1].rent, eighty[3].rent) == (1258.0, None, 2349.0)
    one = next(b for b in eighty[0].bands if b.household == 1)
    seven = next(b for b in eighty[0].bands if b.household == 7)
    assert (one.income_min, seven.income_max) == (46422, 171120)
    assert one.income_max < 100_000


def test_unreadable_table_gives_no_units():
    assert units_from_tsv("level\tpage_num\n") == []


def test_ocr_cached_by_content_hash(monkeypatch):
    from types import SimpleNamespace

    from housing_watch.adapters import spring

    body = {"v": b"png-1"}
    calls = []
    monkeypatch.setattr(spring.fetch, "get", lambda url: SimpleNamespace(content=body["v"]))
    monkeypatch.setattr(spring, "ocr_tsv", lambda path: calls.append(path) or f"tsv{len(calls)}")
    cache, seen = {}, set()
    assert spring._ocr_cached(cache, "https://x/a.png", seen) == "tsv1"
    assert spring._ocr_cached(cache, "https://x/a.png", seen) == "tsv1" and len(calls) == 1
    body["v"] = b"png-2"
    assert spring._ocr_cached(cache, "https://x/a.png", seen) == "tsv2" and len(calls) == 2
