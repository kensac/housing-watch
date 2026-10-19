import json
from pathlib import Path

from housing_watch.adapters.mgny import parse_record
from housing_watch.criteria import listing_verdict
from housing_watch.models import Band

RECS = {r["slug"]: r for r in json.loads((Path(__file__).parent / "fixtures" / "mgny" / "listings.json").read_text())}


def rec(slug):
    return parse_record(RECS[slug])


def test_per_household_rows():
    lst = rec("house-55-apartments")
    assert lst.borough == "Brooklyn" and lst.status == "open"
    assert [(u.size, u.rent, u.ami) for u in lst.units] == [
        ("studio", 3543, 130),
        ("1br", 3600, 130),
        ("1br", 3784, 130),
    ]
    assert lst.units[2].bands == [Band(1, 135360, 154440), Band(2, 135360, 176410), Band(3, 135360, 198510)]
    assert "info@House55lottery.com" in lst.notes and lst.url in lst.notes
    assert listing_verdict(lst).verdict == "eligible"


def test_spanning_row_gets_one_person_cap():
    lst = rec("865-rogers-avenue")
    (u,) = lst.units
    assert (u.size, u.rent, u.ami) == ("studio", 2157.75, 130)
    assert u.bands == [Band(1, 80289, 154440), Band(2, 80289, 176410)]
    assert listing_verdict(lst).verdict == "eligible"


def test_old_limits_judged_on_current_chart():
    lst = rec("27-15-astoria-boulevard")
    (u,) = lst.units
    assert u.rent == 2163 and u.bands[0] == Band(1, 82115, 147420) and u.bands[-1] == Band(3, 82115, 189540)
    assert u.chart_year == 2025  # over the printed $147,420 but within 2026's $154,440
    assert listing_verdict(lst).reason == "eligible unit(s)" and "on 2026 limits" in listing_verdict(lst).units[0][2]


def test_multi_rent_row_splits_units():
    lst = rec("jasper-hunters-point")
    assert lst.borough == "Queens"
    assert [(u.size, u.rent, u.bands) for u in lst.units] == [
        ("1br", 3300, [Band(1, 118766, 198510)]),
        ("1br", 3500, [Band(1, 125623, 198510)]),
    ]
    assert listing_verdict(lst).verdict == "eligible"


def test_bronx_ami_column_and_80pct_tier():
    lst = rec("445-gerard-avenue-apartments")
    assert lst.borough == "Bronx" and listing_verdict(lst).verdict == "excluded"
    assert [(u.size, u.ami) for u in lst.units] == [("1br", 130), ("2br", 80), ("1br", 80)]
    assert lst.units[2].bands[0] == Band(1, 65178, 95040)


def test_two_bedroom_only_and_no_table():
    assert listing_verdict(rec("111-willoughby-street")).reason == "2br not wanted"
    forten = rec("forten")
    assert forten.borough == "Manhattan" and listing_verdict(forten).verdict == "excluded"
    bronx = rec("27-east-198-street")
    assert bronx.units == [] and bronx.borough == "Bronx" and listing_verdict(bronx).verdict == "excluded"


def test_premium_one_bedroom_without_count_column():
    lst = rec("1215fulton12halsey")
    (u,) = lst.units
    assert (u.size, u.rent, u.ami, u.bands[0]) == ("1br", 3500, 130, Band(1, 126515, 154440))
    assert lst.borough == "Brooklyn" and "1215fulton@mgnyconsulting.com" in lst.notes
