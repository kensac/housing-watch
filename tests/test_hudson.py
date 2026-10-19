from pathlib import Path

import pytest

from housing_watch.adapters._pagekit import snapshot
from housing_watch.adapters.hudson import Hudson, parse_programs
from housing_watch.criteria import listing_verdict

HTML = (Path(__file__).parent / "fixtures" / "hudson" / "availability.html").read_text()
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


def test_closed_status_and_missing_section():
    closed = HTML.replace("Waiting List Open", "Waiting List Closed")
    assert parse_programs(closed)[0].status == "closed"
    with pytest.raises(RuntimeError, match="Affordable"):
        parse_programs(HTML.replace("Affordable/Middle Income", "Other"))


def test_snapshot_is_main_content_only():
    snap = snapshot(Hudson(), URL, HTML, root="main")
    assert snap.text.startswith("Market-Rate Rental Apartments") and "Our Story" not in snap.text
