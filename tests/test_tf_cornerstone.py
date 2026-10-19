import json
from pathlib import Path

import pytest

from housing_watch.adapters.tf_cornerstone import parse_api
from housing_watch.criteria import listing_verdict, unit_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "tf_cornerstone"


@pytest.fixture(scope="module")
def listings():
    res = parse_api(json.loads((F / "api.json").read_text()))
    return {lst.address: lst for lst in res.listings}


def test_buildings_and_boroughs(listings):
    assert {a: lst.borough for a, lst in listings.items()} == {
        "5241 Center Blvd": "Queens",
        "5203 Center Blvd": "Queens",
        "505 W 37th St": "Manhattan",
        "455 W 37th St": "Manhattan",
        "595 Dean St": "Brooklyn",
    }
    assert {a: listing_verdict(lst).verdict for a, lst in listings.items()} == {
        "5241 Center Blvd": "eligible",
        "5203 Center Blvd": "eligible",
        "505 W 37th St": "excluded",
        "455 W 37th St": "excluded",
        "595 Dean St": "eligible",
    }
    assert all(
        lst.status == "open" and "AffordableHousingAPPT@TFCornerstone.com" in lst.notes for lst in listings.values()
    )


def test_5241_tiers(listings):
    u = listings["5241 Center Blvd"].units
    assert [(x.size, x.rent, x.ami) for x in u] == [
        ("1br", 3010.81, 130),
        ("studio", 2786.01, 165),
        ("studio", 2857.08, 165),
        ("1br", 3384.58, 165),
    ]
    assert u[0].bands == [Band(1, 106931, 154440), Band(2, 106931, 176410), Band(3, 106931, 198510)]
    assert u[3].bands[0] == Band(1, 119746, 196020)
    assert all(unit_verdict(x)[0] == "eligible" for x in u)


def test_5203_set_aside_and_low_ami(listings):
    u = listings["5203 Center Blvd"].units
    low = u[0]
    assert (low.size, low.rent, low.ami, unit_verdict(low)[0]) == ("studio", 764.57, 50, "excluded")
    s130 = [x for x in u if x.ami == 130]
    assert [(x.size, x.rent) for x in s130] == [("studio", 2489.18), ("1br", 3223.51), ("2br", 3643.99)]
    assert [unit_verdict(x)[0] for x in s130] == ["eligible", "eligible", "excluded"]
    mi = [x for x in u if x.rent == 3477.65]
    assert [x.set_aside for x in mi] == ["mobility", None]
    assert unit_verdict(mi[0]) == ("excluded", "set aside (mobility)")
    assert unit_verdict(mi[1])[0] == "eligible"
    # the site prints "1 Person" on all three rows of this tier: read as 1, 2, 3
    typo = next(x for x in u if x.rent == 3391.15)
    assert [b.household for b in typo.bands] == [1, 2, 3]
    assert typo.bands[0] == Band(1, 119971, 196020)


def test_595_dean(listings):
    u = listings["595 Dean St"].units
    assert [x.rent for x in u if x.size == "studio"] == [2290.0, 2352.97, 2358.7, 2410.22, 2423.55]
    assert [x.rent for x in u if x.size == "1br"] == [2831.22, 2846.88]
    assert {x.ami for x in u} == {130}
    assert u[3].bands[0] == Band(1, 85927, 154440)  # "$85,927.55" min keeps whole dollars


def test_w37_excluded(listings):
    for a in ("505 W 37th St", "455 W 37th St"):
        assert {x.ami for x in listings[a].units} == {40}
        assert all(unit_verdict(x)[0] == "excluded" for x in listings[a].units)


def test_no_buildings_is_a_snapshot():
    res = parse_api({"Data": []})
    assert not res.listings and len(res.snapshots) == 1
