from pathlib import Path

from sqlalchemy import text

from housing_watch import db
from housing_watch.adapters.clinton import parse_cards
from housing_watch.criteria import listing_verdict

HTML = (Path(__file__).parent / "fixtures" / "clinton" / "affordable.html").read_text()


def by_id(listings):
    return {x.ext_id: x for x in listings}


def test_fully_occupied_cards_are_closed_listings():
    ls = by_id(parse_cards(HTML))
    assert len(ls) == 7 and all(x.status == "closed" for x in ls.values())
    ohm = ls["ohm"]
    assert (ohm.address, ohm.borough, [u.size for u in ohm.units]) == (
        "312 11th Avenue New York, NY 10001",
        "Manhattan",
        ["studio", "1br"],
    )
    assert ls["2850-webster"].senior_only and ls["seaview-site-c-senior-apartments"].senior_only
    assert ls["2605-grand-concourse"].units[0].set_aside == "mobility"
    assert ls["rialto-west"].borough == "Manhattan" and ls["crossroads-plaza"].borough == "Bronx"


def test_card_moving_to_current_vacancies_reopens(store):
    before = parse_cards(HTML)
    # move OHM's card from the "no current vacancies" grid into the (empty) vacancies section
    start = HTML.index('<article class="affordable-property-card">')
    end = HTML.index("</article>", start) + len("</article>")
    card = HTML[start:end]
    empty = (
        '<p class="affordable-property-section__empty">'
        "No current affordable housing vacancies are listed at this time.</p>"
    )
    assert empty in HTML
    html2 = (HTML[:start] + HTML[end:]).replace(empty, f'<div class="affordable-property-grid">{card}</div>')
    after = by_id(parse_cards(html2))
    assert after["ohm"].status == "open" and listing_verdict(after["ohm"]).verdict == "possible"
    c = store
    db.record_listings(c, "clinton", before)
    assert db.record_listings(c, "clinton", list(after.values())) == 1
    c.flush()  # textual SQL does not autoflush
    assert c.scalar(text("SELECT type FROM events ORDER BY id DESC LIMIT 1")) == "reopened"
