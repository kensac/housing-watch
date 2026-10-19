import shutil
from pathlib import Path

import pytest

from housing_watch.adapters.ibis import BASE, coming_soon, ocr_png, parse_cards, parse_detail
from housing_watch.criteria import PROFILE, listing_verdict, unit_verdict

F = Path(__file__).parent / "fixtures" / "ibis"


def cards():
    return parse_cards((F / "home.html").read_text())


def detail(slug):
    card = next(c for c in cards() if c["href"] and c["href"].endswith(slug))
    return parse_detail(card["href"], (F / f"{slug}.html").read_text(), (F / f"{slug}.ocr.txt").read_text(), card)


def test_home_cards():
    cs = cards()
    assert [c["href"] for c in cs] == [
        BASE + "1492-Bryant-Ave-3C",
        None,
        BASE + "2809-Sedgwick-Ave",
        BASE + "1254-Fteley-Ave",
    ]
    soon = coming_soon(cs[1])
    assert soon.borough == "Bronx" and soon.address == "1492 Bryant Ave" and soon.units[0].size == "studio"


def test_bryant_1br_one_range_across_sizes():
    lst = detail("1492-Bryant-Ave-3C")
    assert (lst.title, lst.address, lst.borough) == ("1492 Bryant Ave #3C", "1492 Bryant Ave, Bronx, NY 10460", "Bronx")
    (u,) = lst.units
    assert (u.size, u.rent, u.ami, u.unit_no) == ("1br", 1689.88, 130, "3C")
    # "1-3 People $57,938 - $198,510": the 1-person cap is the 2026 130% limit, not $198,510
    assert [(b.household, b.income_min, b.income_max) for b in u.bands] == [
        (1, 57938, 154440),
        (2, 57938, 176410),
        (3, 57938, 198510),
    ]
    assert unit_verdict(u)[0] == "eligible"
    assert listing_verdict(lst).verdict == "excluded"  # Bronx
    assert "forms.app/tyradvisors/application-4-2-1-1-1-1-1-1" in lst.notes


def test_sedgwick_studio_on_2025_limits_is_over_income():
    lst = detail("2809-Sedgwick-Ave")
    (u,) = lst.units
    assert (u.size, u.rent, u.unit_no, lst.borough) == ("studio", 2513.2, None, "Bronx")
    # $168,480 is the 2025 2-person 130% cap, so the implied 1-person cap is under $150k
    assert u.bands[0].household == 1 and u.bands[0].income_max < PROFILE.income
    assert unit_verdict(u)[0] == "excluded"


def test_fteley_unit_after_zip():
    lst = detail("1254-Fteley-Ave")
    (u,) = lst.units
    assert (lst.title, lst.address) == ("1254 Fteley Avenue #4E", "1254 Fteley Avenue, Bronx, NY 10472")
    assert (u.size, u.rent, u.unit_no, u.bands[0].income_min, u.bands[0].income_max) == (
        "1br",
        2552.31,
        "4E",
        87507,
        154440,
    )


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_ocr_reads_data_row():
    txt = ocr_png((F / "1254-Fteley-Ave.png").read_bytes())
    assert "$2,552.31" in txt and "198,510" in txt
