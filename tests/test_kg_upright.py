from pathlib import Path

from housing_watch.adapters import kg_upright as kg
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "kg_upright"


def page():
    email = kg.app_email((F / "application-p1-2.txt").read_text())
    return {lst.ext_id: lst for lst in kg.parse_page((F / "current-vacancies.html").read_text(), None, email)}


def rows(lst):
    return [(u.size, u.rent, u.ami, u.set_aside) for u in lst.units]


def band(u, h):
    b = next(b for b in u.bands if b.household == h)
    return b.income_min, b.income_max


def test_verdant_fort_greene_eligible():
    v = page()["verdant-fort-greene"]
    assert (v.title, v.borough, v.status) == ("Verdant Fort Greene", "Brooklyn", "open")
    assert v.address == "240 Willoughby Street, Brooklyn, New York 11201"
    assert rows(v) == [
        ("studio", 2990.0, 130, None),
        ("1br", 3595.0, 130, None),
        ("1br", 3661.0, 130, None),
        ("1br", 3640.0, 130, None),
        ("2br", 4376.0, 130, None),
        ("2br", 4347.0, 130, None),
    ]
    assert band(v.units[0], 1) == (107383, 154440)
    assert band(v.units[1], 1) == (128880, 154440)
    assert band(v.units[2], 3) == (131143, 198510)
    assert [b.household for b in v.units[4].bands] == [2, 3, 4, 5]
    verdict = listing_verdict(v)
    assert verdict.verdict == "eligible"
    assert [x for _, x, _ in verdict.units][:4] == ["eligible"] * 4
    assert "VerdantFG@kgupright.com" in v.notes and "Verdant-Fort-Green-Application.pdf" in v.notes


def test_other_widgets():
    ls = page()
    assert sorted(ls) == ["diego-beekman", "the-garnette", "the-kira", "verdant-fort-greene"]
    diego = ls["diego-beekman"]
    assert diego.borough == "Bronx" and listing_verdict(diego).verdict == "excluded"
    assert [(u.size, u.rent) for u in diego.units] == [
        ("studio", 2525.0),
        ("1br", 2762.0),
        ("2br", 2885.0),
        ("3br", 3811.0),
    ]
    kira = ls["the-kira"]
    assert kira.borough == "Queens" and kira.address == "153-19 (aka 153-24 90th Rd), Jamaica, Queens, NY 11432"
    (u,) = kira.units
    assert (u.size, u.rent, u.ami, band(u, 1), band(u, 2)) == ("studio", 1737.0, 80, (63360, 90720), (63360, 103680))
    assert "Coming Soon" in kira.notes
    garnette = ls["the-garnette"]
    assert garnette.borough == "Brooklyn"
    assert rows(garnette) == [("studio", 1259.15, 100, "hearing/visual"), ("2br", 2914.98, 150, None)]
    assert band(garnette.units[0], 1) == (105668, 118800)
    assert listing_verdict(garnette).verdict == "excluded"


def test_ocr_style_lines():
    lines = [
        "Studio 130% AREA MEDIAN $2,990.00 $220,480.00 1 people $107,383 - $154,440",
        "2 people $107,383 - $176,410",
    ]
    (u,) = kg.units_from_lines(lines)
    assert (u.size, u.rent, u.ami, band(u, 1), band(u, 2)) == (
        "studio",
        2990.0,
        130,
        (107383, 154440),
        (107383, 176410),
    )


def test_snapshot_includes_widget_text():
    snap = kg.snapshot((F / "current-vacancies.html").read_text(), "kg_upright")
    assert "$107,383 - $154,440" in snap.text and "The Kira" in snap.text
    assert any("Verdant-Fort-Green-Application.pdf" in x for x in snap.links)
    assert not any("godaddy" in x for x in snap.links)
