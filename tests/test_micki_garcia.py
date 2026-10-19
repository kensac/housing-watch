import gzip
import json
from pathlib import Path

from housing_watch.adapters.micki_garcia import gallery, page_map, parse_income_page, parse_project
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "micki_garcia"
BASE = "https://www.mickigarciarealty.com"


def html(name):
    return gzip.decompress((F / f"{name}.html.gz").read_bytes()).decode()


def test_gallery_has_all_items_past_show_more():
    pages = page_map(html("portfolio"))
    assert pages["xrrua"]["slug"] == "copy-of-550-dekalb-ave"  # 55 Carlton: slugs lie
    items = gallery(json.loads((F / "portfolio-page.json").read_text()), pages)
    assert len(items) == 16
    assert items[0] == ("152 East 116th Street", f"{BASE}/1st-project")
    assert ("83 Bushwick Place", f"{BASE}/team-1") in items
    assert ("55 Carlton Avenue", f"{BASE}/copy-of-550-dekalb-ave") in items
    assert ("550 DeKalb Avenue", f"{BASE}/copy-of-1325-southern-blvd") in items


def test_project_pages():
    done = parse_project("83 Bushwick Place", f"{BASE}/team-1", html("team-1"))
    assert (done.status, done.borough, done.kind) == ("closed", "Brooklyn", "re-rental")
    assert listing_verdict(done).verdict == "excluded"
    own = parse_project("152 East 116th Street", f"{BASE}/1st-project", html("1st-project"))
    assert (own.status, own.kind, own.borough) == ("closed", "homeownership", "Manhattan")
    cur = parse_project("550 DeKalb Avenue", f"{BASE}/copy-of-1325-southern-blvd", html("copy-of-1325-southern-blvd"))
    assert (cur.status, cur.borough, cur.units) == ("open", "Brooklyn", [])
    assert listing_verdict(cur).verdict == "possible" and "212-694-3893" in cur.notes
    ys = parse_project("69-65 Yellowstone Blvd", f"{BASE}/copy-of-37-05-30th-st", html("copy-of-37-05-30th-st"))
    assert (ys.status, ys.borough) == ("open", "Queens")  # WHERE: "Forrest Hills" (sic)


def test_homeownership_income_table():
    lst = parse_income_page(f"{BASE}/nme3-income-requirements-140", html("nme3-income-requirements-140"), "")
    assert lst.address == "207 West 140th Street" and lst.kind == "homeownership"
    assert len(lst.units) == 13
    u = next(u for u in lst.units if u.unit_no == "3C")
    assert u.size == "1br" and u.rent is None
    assert [(b.household, b.income_min, b.income_max) for b in u.bands] == [
        (1, 119909, 147420),
        (2, 119909, 168480),
        (3, 119909, 189540),
    ]
    assert "$259,234-$510,174" in lst.notes
    from housing_watch import parse

    sales = parse.text(html("nme3"))
    assert parse_income_page(lst.url, html("nme3-income-requirements-140"), sales).borough == "Manhattan"
    assert listing_verdict(lst).verdict == "possible"
