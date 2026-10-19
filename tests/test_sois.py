from datetime import date
from pathlib import Path

from housing_watch.adapters.sois import build_listing, deadline_iso, mail_to, parse_cards, parse_hpd_ad
from housing_watch.criteria import listing_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "sois"
ADS = {"Geel Webster Avenue": "geel-webster.txt", "WHITLOCK POINT": "whitlock-point.txt"}


def listings(today=date(2026, 10, 18)):
    out = []
    for c in parse_cards((F / "current-projects-1.html").read_text()):
        out.append(build_listing(c, (F / ADS[c["title"]]).read_text(), today))
    return out


def test_cards_status_and_pdf():
    cards = parse_cards((F / "current-projects-1.html").read_text())
    assert [c["title"] for c in cards] == ["Geel Webster Avenue", "WHITLOCK POINT"]
    assert all(c["status"] == "APPLICATIONS ARE CLOSED" for c in cards)
    assert cards[0]["pdf"].startswith("https://img1.wsimg.com/blobby/go/") and cards[0]["pdf"].endswith(".pdf")
    assert cards[1]["address"].startswith("1001 Whitlock Avenue")


def test_side_by_side_tables():
    geel, whitlock = listings()
    assert (geel.status, geel.borough, geel.deadline, geel.kind) == ("closed", "Bronx", "2025-04-07", "lottery")
    assert len(geel.units) == 9
    s40, s50 = geel.units[0], geel.units[1]
    assert (s40.size, s40.rent, s40.ami, s40.bands) == (
        "studio",
        654.0,
        40,
        [Band(1, 26229, 43480), Band(2, 26229, 49720)],
    )
    assert (s50.size, s50.rent, s50.ami) == ("studio", 855.0, 50)
    assert s50.bands[0] == Band(1, 33120, 54350)
    br60 = geel.units[7]
    assert (br60.size, br60.rent, br60.ami, br60.bands[0]) == ("1br", 1328.0, 60, Band(1, 49886, 65220))
    assert len(whitlock.units) == 16
    last = whitlock.units[-1]
    assert (last.size, last.rent, last.ami, last.bands[-1]) == ("3br", 2949.0, 80, Band(7, 108378, 154080))
    assert "Purchase, NY 10577" in whitlock.notes
    assert all(listing_verdict(x).verdict == "excluded" for x in (geel, whitlock))


def test_card_status_overrides_future_deadline():
    geel, _ = listings(today=date(2020, 1, 1))
    assert geel.status == "closed"


def test_helpers():
    assert deadline_iso("must be postmarked no later than March 20th, 2026") == "2026-03-20"
    assert deadline_iso("nothing here") is None
    assert mail_to("Mail completed application to:\n\n   A c/o B\n   1 Main St\n\nOther") == "A c/o B, 1 Main St"
    assert parse_hpd_ad("no table") == []
