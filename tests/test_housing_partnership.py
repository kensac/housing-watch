from pathlib import Path

from housing_watch.adapters import housing_partnership as hp
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "housing_partnership"
BASE = "https://www.housingpartnership.com/services/current-vacancies/"


def page(slug, active=True):
    return hp.parse_detail(BASE + slug, (F / f"{slug}.html").read_text(), active)


def bands(u):
    return [(b.household, b.income_min, b.income_max) for b in u.bands]


def test_index_split():
    cards = hp.parse_index((F / "index.html").read_text())
    assert len(cards) == 12
    active = [c["name"] for c in cards if c["active"]]
    assert active == ["Ruby", "Belle Gardens (Homeownership)"]
    cove = next(c for c in cards if c["name"] == "The Cove")
    assert cove["url"] == BASE + "the-cove" and cove["address"].startswith("43-12 Hunter Street")


def test_ruby_open_studio_only():
    lst = page("ruby")
    assert (lst.title, lst.borough, lst.status, lst.kind) == ("Ruby", "Manhattan", "open", "re-rental")
    studio, one, two = lst.units
    assert (studio.size, studio.rent, studio.ami, studio.available) == ("studio", 2960.0, 130, True)
    assert bands(studio) == [(1, 101485, 154420), (2, 101485, 176410)]
    assert (one.size, one.rent, one.available) == ("1br", None, False)  # 0 units, no Available tag
    assert bands(one)[0] == (1, None, 154420)
    assert two.size == "2br"
    v = listing_verdict(lst)
    assert v.verdict == "eligible"
    assert [x[1] for x in v.units] == ["eligible", "excluded", "excluded"]
    assert "marketing@housingpartnership.com" in lst.notes


def test_cove_waitlist_over_income():
    lst = page("the-cove", active=False)
    assert (lst.borough, lst.status, lst.kind) == ("Queens", "waitlist", "waitlist")
    assert [(u.size, u.ami, u.rent) for u in lst.units] == [
        ("studio", 130, None),
        ("1br", 130, None),
        ("2br", 130, None),
    ]
    assert bands(lst.units[0]) == [(1, None, 147420), (2, None, 168480)]
    v = listing_verdict(lst)
    assert v.verdict == "excluded" and "147,420" in v.units[0][2]


def test_lyra_waitlist_130_tier():
    lst = page("lyra", active=False)
    assert (lst.borough, lst.status) == ("Manhattan", "waitlist")
    by = {(u.size, u.ami): u for u in lst.units}
    assert bands(by[("studio", 70)])[0] == (1, None, 83160)
    assert bands(by[("studio", 130)])[0] == (1, None, 154440)
    assert bands(by[("1br", 130)])[0] == (1, None, 154440)
    v = listing_verdict(lst)
    assert v.verdict == "possible"  # 130% max fits; no minimum printed
    # the page's own "No vacancies" banner wins even if the index card said active
    assert page("lyra", active=True).status == "waitlist"


def test_waverly_min_and_max():
    lst = page("540-waverly", active=False)
    one = next(u for u in lst.units if u.size == "1br" and u.ami == 130)
    assert bands(one)[0] == (1, 100320, 154440)
    assert lst.borough == "Brooklyn"


def test_belle_gardens_homeownership():
    lst = page("belle-gardens")
    assert (lst.kind, lst.status, lst.borough) == ("homeownership", "open", "Brooklyn")
    one = next(u for u in lst.units if u.size == "1br")
    assert (one.ami, one.available, one.rent) == (130, True, None)
    assert bands(one)[0] == (1, 102425, 141310)
    assert "renee@ariellewren.com" in lst.notes


def test_fetch_caches_inactive(monkeypatch):
    slugs = {"ruby", "the-cove", "lyra", "belle-gardens", "540-waverly"}
    calls = []

    def fake(url):
        calls.append(url)
        if url == hp.INDEX:
            return (F / "index.html").read_text()
        slug = url.rsplit("/", 1)[-1]
        return (F / f"{slug if slug in slugs else 'the-cove'}.html").read_text()

    monkeypatch.setattr(hp.fetch, "html", fake)
    a = hp.HousingPartnership()
    assert len(a.fetch().listings) == 12
    calls.clear()
    res = hp.HousingPartnership(cache=a.cache).fetch()
    assert len(res.listings) == 12
    assert sorted(calls) == sorted([hp.INDEX, BASE + "ruby", BASE + "belle-gardens"])  # only active re-read
