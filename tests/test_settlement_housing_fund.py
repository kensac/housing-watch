import json
from pathlib import Path

from helpers import bands
from housing_watch.adapters.settlement_housing_fund import (
    form_api_url,
    index_links,
    parse_form,
    parse_pronto,
    read_blocks,
    registration_listing,
)
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "settlement_housing_fund"
FORM = json.loads((F / "form.json").read_text())
FORM_URL = "https://forms.cloud.microsoft/Pages/ResponsePage.aspx?id=16LZ_9BUgkmpOsX39EKwQ4y0VvZoZjhPj428CE41l_lUNjVJTk9ESks4MlEyU0lZTjlDQVJDTjVHWC4u"


def test_index_links():
    form, links = index_links((F / "find-housing.html").read_text())
    assert form == FORM_URL
    assert [n for n, _, _ in links] == ["Waterside Plaza", "Sawyer Place", "3333 Broadway"]
    assert links[2][1] == "3333 Broadway, New York, NY 10031"
    assert form_api_url(form).startswith(
        "https://forms.office.com/formapi/api/ffd9a2d7-54d0-4982-a93a-c5f7f442b043/users/"
        "f656b48c-6668-4f38-8f8d-bc084e3597f9/light/runtimeForms("
    )


def test_form_without_tables_still_lists_units():
    by = {lst.title: lst for lst in parse_form(FORM, FORM_URL, {})}
    assert [u.unit_no for u in by["509 Third Avenue"].units] == ["8F", "8G"]
    (u,) = by["30 Kent Street"].units
    assert (u.size, u.ami, u.unit_no, u.rent) == ("1br", 130, "310", None)
    assert by["30 Kent Street"].borough == "Brooklyn" and by["7 Dey Street"].borough == "Manhattan"
    assert listing_verdict(by["515 W 36th Street"]).verdict == "possible"  # 130% but no income table


def test_form_with_ocr_tables(shf_tables):
    imgs = {}
    for q in FORM["questions"]:
        if q["type"] == "Question.Choice" and q["image"]["resourceUrl"]:
            png = "table-130.png" if "130%" in q["title"] else "table-40-60.png"
            imgs[q["image"]["resourceUrl"]] = shf_tables[png]
    by = {lst.title: lst for lst in parse_form(FORM, FORM_URL, imgs)}
    assert len(by) == 7
    want = {  # (title, unit): (size, rent, min income, 1-person max)
        ("509 Third Avenue", "8F"): ("studio", 2733, 96995),
        ("509 Third Avenue", "8G"): ("studio", 2733, 96995),
        ("515 W 36th Street", "11J"): ("studio", 2244, 80229),
        ("572 Eleventh Avenue", "4H"): ("studio", 2362, 85269),
        ("7 Dey Street", "16D"): ("studio", 2671, 94869),
        ("30 Kent Street", "310"): ("1br", 2858, 101692),
    }
    for (title, no), (size, rent, lo) in want.items():
        u = next(u for u in by[title].units if u.unit_no == no)
        assert (u.size, u.rent, u.ami, u.set_aside) == (size, rent, 130, None)
        assert bands(u)[0] == (1, lo, 154440)
        assert listing_verdict(by[title]).verdict == "eligible"
    assert bands(by["30 Kent Street"].units[0])[2] == (3, 101692, 198510)
    w24 = {u.unit_no: u for u in by["280 West 24th Street"].units}
    assert (w24["2D"].size, w24["2D"].rent, w24["2D"].set_aside) == ("2br", 3857, "mobility")
    assert bands(w24["2D"])[0] == (2, 140229, 176410)
    assert (w24["5R"].ami, w24["5R"].rent, w24["5R"].set_aside) == (40, 686, "senior")
    assert bands(w24["5R"]) == [(1, 28766, 47520), (2, 28766, 54280)]
    assert by["146 Pierrepont Street"].units[0].set_aside == "mobility"
    assert listing_verdict(by["280 West 24th Street"]).verdict == "excluded"


def test_read_blocks_fixes_misread_dollar_sign():
    block = [
        ["6/4/26"],
        ['515 West 36" Street'],
        ["Unit 1)"],
        ["130% Studio"],
        ["§2,244"],
        ["1 person", "? people"],
        ["$80,229 - $154,440", "580,229 - $176,410"],
    ]
    (row,) = read_blocks([block], 130)
    assert row["key"] == "515 36" and row["rent"] == 2244
    assert [(b.household, b.income_min, b.income_max) for b in row["bands"]] == [(1, 80229, 154440), (2, 80229, 176410)]


def test_read_blocks_live_shapes():
    """OCR of the 2026-11-01 tables: a "$" read as "5", a blank rent cell beside a unit number, a building with
    no house number."""
    blocks = [
        [["7/23/26"], ['280 West 24" Street'], ["Unit 2D\n(Mobility set-aside unit)"], ["130% 2-Bed"], ["53,857"],
         ["2 people", "3 people"], ["$140,229 - $176,410", "$140,229 - $198,510"]],
        [["8/4/26"], ["146 Pierrepont Street"], ["Unit 6D\n(Mobility set-aside unit)"], ["40% Studio"], ["\u2014"],
         ["1 person", "2 people"], ["925,989 - $47,520", "$25,989 - $54,280"]],
        [["10/28/26"], ["The Biltmore"], ["Unit 15K"], ["130% Studia"], ["53,049"], ["1 person", "2 people"],
         ["$107,829 - $154,440", "$107,829 - $176,410"]],
    ]  # fmt: skip
    rows = read_blocks(blocks, 130)
    assert [(r["key"], r["rent"]) for r in rows] == [("280 24", 3857), ("146 pierrepont", None), ("the biltmore", 3049)]
    assert [(b.household, b.income_min, b.income_max) for b in rows[2]["bands"]] == [
        (1, 107829, 154440),
        (2, 107829, 176410),
    ]


def test_pronto_waterside():
    lst = parse_pronto(json.loads((F / "pronto-7409.json").read_text()), "https://bit.ly/WatersidePlazaListings")
    assert (lst.borough, lst.deadline, len(lst.units)) == ("Manhattan", "2027-08-10", 11)
    st = next(u for u in lst.units if u.unit_no == "10-06H")
    assert (st.size, st.rent, st.ami) == ("studio", 2428, 130) and bands(st)[0] == (1, 88458, 154440)
    br = next(u for u in lst.units if u.unit_no == "10-18K")
    assert (br.size, br.rent, br.ami) == ("1br", 3094, 160) and bands(br)[0] == (1, 112046, 190080)
    assert "registration/0aa5f255" in lst.notes and listing_verdict(lst).verdict == "eligible"


def test_pronto_sawyer_is_outside_nyc():
    lst = parse_pronto(json.loads((F / "pronto-482.json").read_text()), "https://bit.ly/SawyerPlaceListings")
    assert lst.borough is None and "Outside NYC" in lst.notes and len(lst.units) == 16


def test_registration_only_link_takes_window_from_form_config():
    cfg = json.loads((F / "pronto-reg-3333.json").read_text())
    reg = "https://prontohousing.io/accounts/registration/f680505a-dd30-4f42-8668-a128e4465baf/"
    lst = registration_listing("3333 Broadway", "3333 Broadway, New York, NY 10031", "https://bit.ly/x", reg, cfg)
    assert (lst.borough, lst.deadline, lst.units) == ("Manhattan", "2026-12-31", [])
    assert "2024-10-30 to 2026-12-31" in lst.notes and "first come, first served" in lst.notes
    assert "3333-broadway@prontohousing.io" in lst.notes and "publishes sizes, rents or income" in lst.notes
    assert listing_verdict(lst).reason == "no unit details published"
