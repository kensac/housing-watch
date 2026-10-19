import re
from pathlib import Path

import pytest

from housing_watch.adapters import mns
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "mns"
DETAIL = "https://www.mns.com/details/240428/rental/morris+heights/bronx"


def test_index_rows():
    (row,) = mns.parse_index((F / "affordable_units.html").read_text())
    assert row == {"url": DETAIL, "address": "1730 Harrison AVE", "unit": "4N", "beds": "1", "price": 2653.0}


def test_detail_bands_and_borough():
    row = mns.parse_index((F / "affordable_units.html").read_text())[0]
    lst = mns.parse_detail(DETAIL, (F / "240428.html").read_text(), row)
    assert (lst.ext_id, lst.borough, lst.title) == ("240428", "Bronx", "1730 Harrison AVE #4N")
    (u,) = lst.units
    assert (u.size, u.rent, u.unit_no) == ("1br", 2653.0, "4N")
    # only the 2-person max is printed ($77,760 = 60% on the 2025 chart); the 1-person cap is derived from it
    assert [(b.household, b.income_min, b.income_max) for b in u.bands] == [(1, 35000, 68040), (2, 35000, 77760)]
    assert "Vladimir Baron 347-381-0549" in lst.notes and "718-222-0211" in lst.notes
    assert listing_verdict(lst).verdict == "excluded"


def test_income_parsing_variants():
    bands, ami = mns.parse_income("MINIMUM INCOME: $90,000\nMAXIMUM INCOME:\n1 person: $154,440\n2 person: $176,410")
    assert ami is None and [(b.household, b.income_max) for b in bands] == [(1, 154440), (2, 176410)]
    bands, _ = mns.parse_income("nothing here")
    assert bands == []


def test_zero_vacancies_page_still_valid(monkeypatch):
    body = (F / "affordable_units.html").read_text()
    empty = re.sub(r'<tr class="afordable_listing".*?</tr>', "", body, flags=re.S)
    monkeypatch.setattr(mns.fetch, "html", lambda url: empty)
    res = mns.MNS().fetch()
    assert res.listings == [] and len(res.snapshots) == 1
    assert "Availability" in res.snapshots[0].text


def test_layout_change_raises():
    with pytest.raises(RuntimeError):
        mns.parse_index("<html><body>Access denied</body></html>")


def test_detail_cached(monkeypatch):
    pages = {mns.URL: (F / "affordable_units.html").read_text(), DETAIL: (F / "240428.html").read_text()}
    calls = []
    monkeypatch.setattr(mns.fetch, "html", lambda url: calls.append(url) or pages[url])
    a = mns.MNS()
    a.fetch()
    b = mns.MNS(cache=a.cache)
    res = b.fetch()
    assert calls.count(DETAIL) == 1 and res.listings[0].units[0].rent == 2653.0
