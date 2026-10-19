from pathlib import Path

import pytest

from housing_watch.adapters._pagekit import snapshot
from housing_watch.adapters.hudson import AGENTS, Hudson, parse_programs, with_agent_units
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "hudson"
HTML = (F / "availability.html").read_text()
URL = "https://www.hudsoninc.com/availability/"


def test_three_programs():
    bergen, clark, central = parse_programs(HTML)
    assert [(x.ext_id, x.borough, x.status, x.kind, x.units[0].ami) for x in (bergen, clark, central)] == [
        ("bergen-gardens", "Brooklyn", "waitlist", "waitlist", 125),
        ("the-clark", "Brooklyn", "open", "lottery", 130),
        ("la-central-a-b", "Bronx", "open", "lottery", 130),
    ]
    assert clark.url == "http://renttheclark.com" and "100-130% AMI" in clark.notes
    assert listing_verdict(bergen).reason == "125% AMI caps 1 person below $150,000"
    assert listing_verdict(clark).verdict == "possible"
    assert listing_verdict(central).reason == "in Bronx"


def test_clark_tiers_from_marketing_agent():
    clark = parse_programs(HTML)[1]
    with_agent_units(clark, AGENTS["the-clark"], (F / "hp-the-clark.html").read_text())
    assert [(u.size, u.ami) for u in clark.units] == [("studio", 130), ("1br", 130), ("2br", 130), ("3br", 130)]
    assert [(b.household, b.income_max) for b in clark.units[0].bands] == [(1, 154440), (2, 176410)]
    assert clark.status == "open" and "housingpartnership.com" in clark.notes
    # sizes and maxima now known; Housing Partnership prints no minimum income
    assert listing_verdict(clark).reason == "income range incomplete"


def test_closed_status_and_missing_section():
    closed = HTML.replace("Waiting List Open", "Waiting List Closed")
    assert parse_programs(closed)[0].status == "closed"
    with pytest.raises(RuntimeError, match="Affordable"):
        parse_programs(HTML.replace("Affordable/Middle Income", "Other"))


def test_snapshot_is_main_content_only():
    snap = snapshot(Hudson(), URL, HTML, root="main")
    assert snap.text.startswith("Market-Rate Rental Apartments") and "Our Story" not in snap.text
