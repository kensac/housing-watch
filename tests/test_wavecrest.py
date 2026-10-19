from pathlib import Path

from housing_watch.adapters.wavecrest import index_links, parse_flyer
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "wavecrest"
LINKS = {
    u.split("WavecrestManagement-")[-1][:-4]: (h, u, b)
    for h, u, b in index_links((F / "opportunities.html").read_text())
}


def flyer(name):
    h, u, b = LINKS[name]
    return parse_flyer(h, u, b, (F / f"{name}.txt").read_text())


def bands(u):
    return [(b.household, b.income_min, b.income_max) for b in u.bands]


def test_index_lists_five_flyers():
    assert len(LINKS) == 5
    assert LINKS["CanterburyHouse"][0] == "1331 Bay Street, Staten Island"


def test_rockabill_split_labels_and_centred_rents():
    lst = flyer("Rockabill65thStreetLLC")
    assert (lst.borough, lst.senior_only, lst.address) == ("Queens", False, None)
    studio, br1, br2 = lst.units
    assert (studio.size, studio.rent, bands(studio)) == ("studio", 1816.0, [(1, 58780, 113400), (2, 58780, 129600)])
    assert (br1.size, br1.rent, bands(br1)[0]) == ("1br", 1990.0, (1, 63800, 113400))
    assert (br2.size, br2.rent, bands(br2)[-1]) == ("2br", 2155.0, (5, 68925, 175000))
    assert "email Rentals@twmt.biz" in lst.notes and "87-14 116th Street Richmond Hill" in lst.notes
    v = listing_verdict(lst)
    assert v.verdict == "excluded" and v.units[0][2] == "over income (max $113,400)"


def test_canterbury_senior_staten_island():
    lst = flyer("CanterburyHouse")
    assert (lst.borough, lst.senior_only, lst.kind) == ("Staten Island", True, "waitlist")
    assert [(u.size, u.rent, u.set_aside, bands(u)[0]) for u in lst.units] == [
        ("1br", 1012.0, "senior", (1, 33630, 56700)),
        ("1br", 1065.0, "senior", (1, 35220, 68040)),
    ]
    assert listing_verdict(lst).reason == "senior only"


def test_outside_nyc_has_no_borough():
    lst = flyer("LaPorteApartments")
    assert lst.borough is None and lst.notes.startswith("Outside NYC (Mt. Vernon)")
    assert [(u.size, u.rent) for u in lst.units] == [
        ("studio", 1586.0),
        ("1br", 1401.0),
        ("1br", 1694.0),
        ("2br", 2019.0),
    ]
    assert "www.ApplyLaPorte.com" in lst.notes
    assert flyer("UnionSeniorPlaza").borough is None  # Hempstead zip 11550; office zip 11418 must not count


def test_west_61st_manhattan_senior_studio():
    lst = flyer("33WestEndAveAssocLP")
    assert (lst.borough, lst.senior_only) == ("Manhattan", True)
    (u,) = lst.units
    assert (u.size, u.rent, bands(u)) == ("studio", 954.0, [(1, 31500, 68040), (2, 31500, 77760)])
    assert "regular mail only" in lst.notes
