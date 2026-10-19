import json
from pathlib import Path

import pytest

from housing_watch.adapters import stuytown
from housing_watch.adapters.stuytown import StuyTown, parse_api, parse_eligibility
from housing_watch.criteria import listing_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "stuytown"
ELIG = (F / "eligibility.html").read_text()


def test_eligibility_uses_2026_desktop_table():
    e = parse_eligibility(ELIG)
    assert e["households"] == {"1": [1, 3], "2": [2, 5], "3": [3, 7]}
    assert e["caps"]["1"] == 196020 and e["caps"]["3"] == 251955 and e["caps"]["7"] == 347160
    assert stuytown._ami(e["caps"]) == 165


def _run(monkeypatch, api_file):
    calls = []
    monkeypatch.setattr(stuytown.fetch, "json", lambda url: json.loads((F / api_file).read_text()))
    monkeypatch.setattr(stuytown.fetch, "html", lambda url: calls.append(url) or ELIG)
    return StuyTown({}).fetch(), calls


def test_nothing_posted(monkeypatch):
    res, calls = _run(monkeypatch, "api-empty.json")
    assert res.listings == [] and res.snapshots == [] and calls == []  # eligibility not fetched


def test_posted_units_become_listings(monkeypatch):
    # api-synthetic.json: the placeholder unit model from the site's own bundle, made a 1BR, plus a studio
    res, calls = _run(monkeypatch, "api-synthetic.json")
    assert calls == [stuytown.ELIGIBILITY]
    one, studio = res.listings
    assert one.ext_id == "P~NYPC21~123456789" and one.borough == "Manhattan" and one.status == "open"
    assert one.title == "Peter Cooper Village: 431 East 20th Street #0M-C"
    assert one.url == "https://affordable-housing.stuytown.com/apartments/units?unitSpk=P~NYPC21~123456789"
    (u,) = one.units
    assert (u.size, u.rent, u.ami, u.unit_no) == ("1br", 3850, 165, "0M-C")
    assert u.bands == [Band(1, 132000, 196020), Band(2, 132000, 223905), Band(3, 132000, 251955)]
    assert listing_verdict(one).verdict == "eligible"
    assert "available 2026-11-01" in one.notes
    assert studio.units[0].size == "studio" and studio.units[0].bands == []
    assert listing_verdict(studio).verdict == "possible"


def test_bad_api_shape_is_loud():
    with pytest.raises(RuntimeError, match="unexpected"):
        parse_api([{"error": "nope"}])
