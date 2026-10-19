from pathlib import Path

from housing_watch import db
from housing_watch.adapters.mitchell_lama import income_limits, parse_open_lists, pdf_snapshot
from housing_watch.criteria import listing_verdict

F = Path(__file__).parent / "fixtures" / "mitchell_lama"
TEXT = (F / "open-lists.txt").read_text()
LIMITS = income_limits((F / "program.html").read_text())


def by_id():
    return {x.ext_id: x for x in parse_open_lists(TEXT, LIMITS)}


def test_income_limits_table():
    assert LIMITS["rental_fed"][1] == 95040
    assert LIMITS["coop_fed"][1] == LIMITS["non_fed"][1] == 148500
    assert LIMITS["non_fed"][8] == 279875


def test_every_development_parsed_with_its_agent():
    ls = by_id()
    assert len(ls) == 16
    a = ls["arlington-terrace"]
    assert (a.address, a.borough, a.kind, a.status) == (
        "89 Holland Avenue, Staten Island, New York 10303",
        "Staten Island",
        "waitlist",
        "waitlist",
    )
    assert "RY Management Inc." in a.notes and "212-534-6140" in a.notes
    # agent whose first line sits above the development's name line
    assert "Rose Associates Inc." in ls["goodwill-terrace"].notes
    assert ls["goodwill-terrace"].address == "4-21 27 Avenue Astoria, NY 11102"
    assert "Project Find" in ls["hamilton-house"].notes and ls["hamilton-house"].senior_only
    assert ls["tanya-towers"].senior_only and not ls["ocean-village"].senior_only


def test_sizes_status_and_bands():
    ls = by_id()
    g = ls["goodwill-terrace"]  # Rental, not federally assisted: Open Open Closed None None
    assert [(u.size, u.available) for u in g.units] == [("studio", True), ("1br", True), ("2br", False)]
    assert g.units[0].bands[0].income_max == 148500
    at = ls["atlantic-terminal-2"]  # only Four Plus open
    assert [u.size for u in at.units if u.available] == ["4br"]
    assert ls["bay-towers"].units[0].bands[0].income_max == 95040  # federally assisted rental
    # $150k single is over every 2026 HPD limit, so nothing qualifies
    assert all(listing_verdict(x).verdict == "excluded" for x in ls.values())
    assert "over income" in str(listing_verdict(g).units)


def test_new_revision_with_a_change_is_detected():
    url = "https://www.nyc.gov/assets/hpd/downloads/pdfs/services/ML-waiting-Lists-Status.pdf"
    v1 = TEXT
    # Goodwill Terrace's studio list closes in the next revision
    v2 = TEXT.replace("(Rev 05-21-2026)", "(Rev 11-02-2026)").replace(
        "Rental      Open     Open     Closed   None       None",
        "Rental      Closed   Open     Closed   None       None",
    )
    assert v2 != v1
    c = db.connect(":memory:")
    assert db.record_snapshots(c, "mitchell_lama", [pdf_snapshot("mitchell_lama", url, v1)], now=1) == 1
    assert db.record_snapshots(c, "mitchell_lama", [pdf_snapshot("mitchell_lama", url, v1)], now=2) == 0
    assert db.record_snapshots(c, "mitchell_lama", [pdf_snapshot("mitchell_lama", url, v2)], now=3) == 1
    db.record_listings(c, "mitchell_lama", parse_open_lists(v1, LIMITS), now=1)
    assert db.record_listings(c, "mitchell_lama", parse_open_lists(v2, LIMITS), now=3) == 1  # Goodwill changed
