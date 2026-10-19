import json
from pathlib import Path

import pytest

from housing_watch import fetch
from housing_watch.adapters import nychdc
from housing_watch.adapters.nychdc import NycHdc, parse_card, parse_flyer
from housing_watch.criteria import listing_verdict, unit_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "nychdc"
CARDS = {c["nid"]: c for c in json.loads((F / "cards.json").read_text())}


def listing(nid):
    return parse_card(CARDS[nid], (F / f"{nid}.txt").read_text())


def tier(lst, size, ami, set_aside=None):
    (u,) = [u for u in lst.units if (u.size, u.ami, u.set_aside) == (size, ami, set_aside)]
    return u


def test_apex_place_140_eligible_110_excluded():
    lst = listing("8656")
    assert (lst.borough, lst.status, lst.kind, lst.senior_only) == ("Queens", "open", "re-rental", False)
    hi = tier(lst, "1br", 140)
    assert hi.rent == 2618 and hi.bands[0] == Band(1, 94080, 166320) and unit_verdict(hi)[0] == "eligible"
    lo = tier(lst, "1br", 110)  # its "1 person" row sits on a different line from the size label
    assert lo.rent == 2206 and lo.bands[0] == Band(1, 79954, 130680) and unit_verdict(lo)[0] == "excluded"
    assert [b.household for b in tier(lst, "2br", 110).bands] == [2, 3, 4, 5]
    assert listing_verdict(lst).verdict == "eligible"
    assert "Rentals@phippsny.org" in lst.notes and "257 PARK AVENUE SOUTH" in lst.notes


def test_carolina_manhattan_165():
    lst = listing("8534")
    assert lst.borough == "Manhattan"
    u = tier(lst, "1br", 165)
    assert u.rent == 3357.67 and u.bands[0] == Band(1, 118823, 196020)
    assert u.set_aside is None  # "mobility disability will receive preference" is a preference, not a set-aside
    assert listing_verdict(lst).verdict == "eligible"


def test_alvista_side_by_side_tiers():
    lst = listing("8667")
    s120, s165 = tier(lst, "studio", 120), tier(lst, "studio", 165)
    assert (s120.rent, s120.bands[0].income_max) == (2859, 142560)
    assert (s165.rent, s165.bands[0]) == (3574, Band(1, 126343, 196020))
    b165 = tier(lst, "1br", 165)
    assert (b165.rent, b165.bands[0]) == (3821, Band(1, 135326, 196020))
    assert unit_verdict(s120)[0] == "excluded" and unit_verdict(b165)[0] == "eligible"
    # the 50% waitlist row "Studio - 2 Bedroom, 1 - 5 People" fans out per size, 1 person capped by AMI
    w = tier(lst, "studio", 50)
    assert w.rent == 1306 and w.bands[0] == Band(1, 48583, 59400) and w.bands[-1] == Band(5, 48583, 91600)


def test_center_blvd_tiers_and_mi():
    lst = listing("8531")  # 5203 Center Blvd
    assert lst.borough == "Queens"
    assert tier(lst, "studio", 130).bands[0] == Band(1, 88635, 154440)
    assert tier(lst, "1br", 130).rent == 3223.51
    assert tier(lst, "1br", 165).rent == 3286  # "$3,286 - $3,477.65" printed over two lines
    mi = tier(lst, "1br", 165, "mobility")
    assert mi.rent == 3477.65 and unit_verdict(mi)[0] == "excluded"
    assert listing_verdict(lst).verdict == "eligible"
    assert "affordablehousingappt@tfcornerstone.com" in lst.notes

    lst = listing("8619")  # 5241 Center Blvd
    assert tier(lst, "1br", 130).bands[0] == Band(1, 106931, 154440)
    assert tier(lst, "studio", 165).rent == 2786.01
    assert tier(lst, "1br", 165, "mobility").rent == 3384.58
    assert listing_verdict(lst).verdict == "eligible"


def test_disability_designated_flyer_and_deadline():
    lst = listing("8671")  # One Flushing 60%: every unit is for a household with a disability
    assert lst.deadline == "2026-11-30"
    assert {u.set_aside for u in lst.units} == {"hearing/vision", "mobility"}
    assert tier(lst, "1br", 60, "mobility").rent == 1114  # not the $101,760 asset limit
    assert listing_verdict(lst).verdict == "excluded"


def test_senior():
    lst = listing("8624")
    assert lst.senior_only and lst.status == "waitlist" and lst.kind == "waitlist"
    assert listing_verdict(lst).verdict == "excluded"
    # one building of four is senior: the listing is not senior-only, that building's tier is
    lst = listing("8763")
    assert not lst.senior_only
    assert tier(lst, "1br", 30, "senior").rent == 803
    assert tier(lst, "2br", 90).bands[0] == Band(2, 99978, 122130)


def test_waitlist_aggregate_row():
    lst = listing("8758")
    assert lst.status == "waitlist" and lst.borough == "Brooklyn"
    u = tier(lst, "1br", 60)
    assert u.rent == 1800 and u.bands[0] == Band(1, 65418, 71280) and u.bands[-1] == Band(7, 65418, 126240)
    assert {u.size for u in lst.units} == {"1br", "2br", "3br"}


def test_bronx_split_label_and_waitlist_vs_immediate():
    lst = listing("8611")
    assert lst.borough == "Bronx" and listing_verdict(lst).verdict == "excluded"
    assert tier(lst, "1br", 60).rent == 1713  # "1" and "Bedroom" printed on separate lines
    s100 = tier(lst, "studio", 100)  # immediate table wins over the "1 - 7 People" waitlist row
    assert s100.rent == 1778 and s100.bands == [Band(1, 64252, 118800), Band(2, 64252, 135700)]
    assert tier(lst, "studio", 30).rent == 795
    assert listing_verdict(listing("8767")).reason == "in Bronx"


def test_unreadable_flyer_still_listed():
    lst = parse_card(CARDS["8534"], None)
    assert lst.units == [] and listing_verdict(lst).verdict == "possible"
    assert parse_flyer("no table here") == []


def test_fetch_paginates_and_caches(monkeypatch):
    cards = list(CARDS.values())
    pages = [cards[i : i + 9] for i in range(0, len(cards), 9)]
    calls = {"pdf": 0}

    def fake_json(url, **kw):
        page = int(url.split("current-page=")[1].split("&")[0])
        return {"json": pages[page - 1], "pagination": {"total-results": len(cards)}}

    def fake_pdf(url):
        calls["pdf"] += 1
        nid = next(c["nid"] for c in cards if url.endswith(c["pdf"]))
        if nid == "8767":
            raise fetch.FetchError("boom")
        return (F / f"{nid}.txt").read_text()

    monkeypatch.setattr(nychdc.fetch, "json", fake_json)
    monkeypatch.setattr(nychdc.fetch, "pdf_text", fake_pdf)
    cache: dict = {"flyers": {"/gone.pdf": "old"}}
    res = NycHdc(cache).fetch()
    assert len(res.listings) == len(cards) and calls["pdf"] == len(cards)
    assert "/gone.pdf" not in cache["flyers"] and len(cache["flyers"]) == len(cards) - 1
    assert next(x for x in res.listings if x.ext_id == "8767").units == []
    NycHdc(cache).fetch()
    assert calls["pdf"] == len(cards) + 1  # only the failed flyer is retried


@pytest.mark.parametrize("nid", sorted(CARDS))
def test_every_fixture_parses(nid):
    lst = listing(nid)
    assert lst.units and all(u.size != "unknown" for u in lst.units)
    assert all(u.bands and u.ami for u in lst.units)
