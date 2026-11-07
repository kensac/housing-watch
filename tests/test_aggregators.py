"""An aggregator's listing of a posting tracked from its own source is a copy: one listing stands for both."""

import dataclasses

from sqlalchemy import update

from housing_watch import filters as F
from housing_watch import queries as Q
from housing_watch.models import Unit
from housing_watch.notify import digest
from housing_watch.orm import ListingRow
from seed import L, Session, add_listing, event

REAL = "https://residenewyork.com/property/2140-matthews-ave-apartment-unit-4a/"


def put(pg, gone=False):
    """r:4a, Reside's own posting; s:4a, SANEL's copy of it; s:x, a SANEL listing whose source is not tracked."""
    with Session(pg) as s, s.begin():
        add_listing(s, dataclasses.replace(L("r", "4a", "Queens"), url=REAL), gone=gone)
        add_listing(s, dataclasses.replace(L("s", "4a", "Queens"), official_url=REAL.rstrip("/").upper()))
        add_listing(s, dataclasses.replace(L("s", "x", "Queens"), official_url="https://untracked.example/1"))


def keys(xs):
    return sorted(x.key for x in xs)


def test_a_copy_is_marked_and_hidden(pg):
    put(pg)
    by = Q.judged_by_key(pg)
    assert by["s:4a"].copy_of == "r:4a" and by["r:4a"].copies == ["s:4a"]  # url_key ignores case and the slash
    assert by["s:x"].copy_of is None  # its real posting is not one we track: it stays the only record
    assert keys(Q.filter_listings(pg, [])) == ["r:4a", "s:x"]
    assert {u.key for u in Q.filter_units(pg, [])} == {"r:4a", "s:x"}
    asked = F.from_tree({"copy_of": {"empty": False}}, F.LISTING_FIELDS)
    assert keys(Q.filter_listings(pg, asked)) == ["s:4a"]  # shown when a filter asks for copies
    ov = Q.overview(pg)
    assert ov.total == 2 and ov.eligible_live == 2


def test_a_copy_stands_alone_once_the_real_posting_is_gone(pg):
    put(pg, gone=True)
    assert Q.judged_by_key(pg)["s:4a"].copy_of is None
    assert "s:4a" in keys(Q.filter_listings(pg, []))


def test_the_digest_mails_a_posting_once(pg):
    put(pg)
    with Session(pg) as s, s.begin():
        event(s, "r:4a", "new")
        event(s, "s:4a", "new")
        event(s, "s:x", "new")
    subject, plain, _, ids = digest.build(pg)
    assert len(ids) == 3  # every event is marked read
    assert "s 4a" not in plain and "r 4a" in plain and "s x" in plain
    assert digest.urgent(pg)
    with Session(pg) as s, s.begin():
        s.execute(update(ListingRow).where(ListingRow.key.in_(["r:4a", "s:x"])).values(status="closed"))
    assert not digest.urgent(pg)  # only the copy is open: it rings no bell


def test_a_copy_matched_by_site_address_and_unit(pg):
    """SANEL links HDC's index page, not the flyer: the same site, street and unit still make it a copy."""
    hdc = "https://www.nychdc.com/find-re-rentals"

    def direct(n, address, title, unit):
        lst = L("h", n, "Brooklyn", units=[Unit("1br", 2000.0, 80, unit)])
        return dataclasses.replace(lst, url=f"https://www.nychdc.com/files/flyer-{n}.pdf", address=address, title=title)

    def copy(n, address, title, official=hdc):
        return dataclasses.replace(L("s", n, "Brooklyn"), official_url=official, address=address, title=title)

    with Session(pg) as s, s.begin():
        add_listing(s, direct("1", "1 Main Street, Brooklyn, NY", "1 Main St Apartments", "1A"))
        add_listing(s, direct("2", "9 Oak Street, Brooklyn, NY", "9 Oak St #4B", "4B"))
        add_listing(s, direct("3", "9 Oak Street, Brooklyn, NY", "9 Oak St #5C", "5C"))
        add_listing(s, copy("1", "1 Main St", "1 Main St"))
        add_listing(s, copy("2", "9 Oak St", "9 Oak St #4B"))
        add_listing(s, copy("3", "9 Oak St", "9 Oak St"))
        add_listing(s, copy("4", "1 Main St", "1 Main St", official="https://other.example/"))
    by = Q.judged_by_key(pg)
    assert by["s:1"].copy_of == "h:1"  # one listing at that address on that site
    assert by["s:2"].copy_of == "h:2"  # the unit picks one of the two at 9 Oak St
    assert by["s:3"].copy_of is None  # two candidates, no unit to choose: not a copy
    assert by["s:4"].copy_of is None  # same street, another site: not the same posting
