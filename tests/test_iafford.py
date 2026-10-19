import datetime as dt
import json
from pathlib import Path

from housing_watch.adapters.afny import parse_detail
from housing_watch.adapters.iafford import IAfford
from housing_watch.criteria import listing_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "iafford"


def page(i):
    rec = json.loads((F / f"{i}.json").read_text())["data"]["record"]
    return parse_detail("iafford", IAfford.site, rec, dt.date(2026, 10, 19))


def test_garrison_email_apply():
    lst = page("2350924")
    assert [u.unit_no for u in lst.units] == ["2F", "2J", "4E", "8H"]
    assert all(u.size == "1br" and u.rent == 3500 and u.bands[0] == Band(1, 125623, 154440) for u in lst.units)
    assert "Fradyj@iafford.nyc" in lst.notes and "mail" not in lst.notes.split(";")[0]
    assert lst.url == "https://iaffordny.com/re-rentals/2350924" and listing_verdict(lst).verdict == "eligible"


def test_typo_cap_repaired():
    (u,) = page("2388548").units  # source prints a $15,440 1-person cap
    assert (u.size, u.unit_no, u.bands[0]) == ("studio", "6F", Band(1, 86930, 154440))


def test_borough_from_address_when_missing():
    lst = page("4076564")  # borough_title is null
    assert lst.borough == "Brooklyn" and listing_verdict(lst).reason == "2br not wanted"
    assert page("2278330").borough == "Bronx" and listing_verdict(page("2278330")).verdict == "excluded"


def test_manhattan_studio_past_end_date():
    lst = page("7816340")
    assert lst.borough == "Manhattan" and lst.deadline is None
    (u,) = lst.units
    assert (u.size, u.rent, u.unit_no) == ("studio", 1831.06, "3B")
    assert "C/O IAfford NY" in lst.notes and listing_verdict(lst).verdict == "eligible"
