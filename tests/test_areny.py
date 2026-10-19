from pathlib import Path

import pytest

from housing_watch.adapters import areny
from housing_watch.adapters.areny import ARENY, parse_availability
from housing_watch.criteria import listing_verdict

HTML = (Path(__file__).parent / "fixtures" / "areny" / "affordable-housing-marketing.html").read_text()
NONE = "We don’t have any apartments available right now, but complete our Housing Interest Form"
POSTED = "Now leasing at 12 Example Street, Brooklyn: Studio $2,100 (130% AMI), 1 Bedroom $2,450"


def test_nothing_available():
    assert parse_availability(HTML) == []


def test_posted_block_becomes_a_listing():
    (lst,) = parse_availability(HTML.replace(NONE, POSTED))
    assert (lst.ext_id, lst.status, lst.borough) == ("availability", "open", "Brooklyn")
    assert [(u.size, u.rent, u.ami) for u in lst.units] == [("studio", 2100, 130), ("1br", 2450, None)]
    assert "12 Example Street" in lst.notes and areny.INTEREST_FORM in lst.notes
    assert listing_verdict(lst).verdict == "possible"


def test_layout_change_is_loud():
    with pytest.raises(RuntimeError, match="not found"):
        parse_availability(HTML.replace("Looking for an affordable apartment?", "Hello"))


def test_snapshot_then_flush_when_listing_disappears(monkeypatch):
    pages = iter([HTML, HTML.replace(NONE, POSTED), HTML, HTML])
    monkeypatch.setattr(areny.fetch, "html", lambda url: next(pages))
    cache: dict = {}
    first = ARENY(cache).fetch()
    assert first.listings == [] and len(first.snapshots) == 1
    snap = first.snapshots[0]
    assert snap.links == [areny.INTEREST_FORM] and snap.text.startswith("Affordable Housing Marketing")
    assert len(ARENY(cache).fetch().listings) == 1
    flush = ARENY(cache).fetch()
    assert flush.listings == [] and flush.snapshots == []  # runner records [] -> listing gone
    assert len(ARENY(cache).fetch().snapshots) == 1
