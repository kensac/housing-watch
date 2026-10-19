from pathlib import Path

from housing_watch.adapters.stanton_norfolk import cfemail, parse_ad, parse_site
from housing_watch.criteria import listing_verdict, unit_verdict

F = Path(__file__).parent / "fixtures" / "stanton_norfolk"


def site(host, ad=True):
    ad_txt = (F / f"{host}.ad.txt").read_text() if ad and (F / f"{host}.ad.txt").exists() else None
    return parse_site(
        f"https://{host}.rentcafewebsite.com/",
        (F / f"{host}.home.html").read_text(),
        (F / f"{host}.fp.html").read_text(),
        ad_txt,
        "https://example/ad.pdf",
    )


def bands(u):
    return [(b.household, b.income_min, b.income_max) for b in u.bands]


def test_east_village_140_1br_eligible():
    lst = site("east-village-homes-owner-llc")
    assert (lst.title, lst.borough, lst.status) == ("East Village Homes", "Manhattan", "open")
    assert lst.address.startswith("302 East 2nd Street")
    u = lst.units[0]
    assert (u.size, u.ami, u.rent) == ("1br", 140, 2911.0)
    assert bands(u) == [(1, 104126, 166320), (2, 104126, 189980), (3, 104126, 213780)]
    assert unit_verdict(u)[0] == "eligible"
    # the 140% studio is only on floorplans.aspx: no income table, so "possible"
    studio = next(x for x in lst.units if (x.size, x.ami) == ("studio", 140))
    assert studio.rent == 2127.0 and unit_verdict(studio)[0] == "possible"
    low = next(x for x in lst.units if (x.size, x.ami) == ("studio", 80))
    assert unit_verdict(low)[0] == "excluded"
    assert listing_verdict(lst).verdict == "eligible"
    assert "PO Box 981" in lst.notes and "guestlogin.aspx" in lst.notes


def test_one_flushing_stale_ad_is_possible_not_excluded():
    units = parse_ad((F / "ofowner3-ny.ad.txt").read_text())
    assert [(u.size, u.rent, u.ami) for u in units] == [
        ("studio", 1884.0, 130),
        ("1br", 2378.0, 130),
        ("2br", 2696.0, 130),
    ]
    assert bands(units[1]) == [(1, 84412, 128570), (2, 84412, 146900), (3, 84412, 165230)]
    assert bands(units[2])[0] == (2, 96206, 146900)
    lst = site("ofowner3-ny")
    assert lst.borough == "Queens" and "old income limits" in lst.notes
    assert "oneflushing@stantonnorfolk.org" in lst.notes
    assert listing_verdict(lst).verdict == "possible"
    two = next(x for x in lst.units if x.size == "2br")
    assert two.available is True  # "(1 Available)" on floorplans.aspx


def test_chinatown_80_ami_excluded():
    lst = site("chinatown")
    assert (lst.title, lst.borough) == ("Chinatown", "Manhattan")
    s, one = lst.units[0], lst.units[1]
    assert (s.size, s.rent, bands(s)) == ("studio", 1321.0, [(1, 48583, 95040), (2, 48583, 108560)])
    assert (one.size, one.rent, one.bands[0].income_max) == ("1br", 1713.0, 95040)
    assert listing_verdict(lst).verdict == "excluded"
    assert "marketing@stantonnorfolk.org" in lst.notes and "PO Box 981" in lst.notes


def test_harlem_waitlist_closed_and_les_without_ad():
    h = site("harlem")
    assert h.status == "closed" and listing_verdict(h).verdict == "excluded"
    les = site("lower-east-side", ad=False)
    assert les.borough == "Manhattan" and "ad PDF unreadable" in les.notes
    assert {u.ami for u in les.units if u.size == "1br"} == {50, 60, 75, 80, 100}
    assert listing_verdict(les).verdict == "excluded"


def test_cfemail():
    assert cfemail("e78a86958c82938e8980a79493868993888989889581888b8cc9889580") == "marketing@stantonnorfolk.org"
