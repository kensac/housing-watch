from datetime import timedelta
from unittest import mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from housing_watch import digest
from housing_watch.models import Band, Unit
from housing_watch.orm import EventRow
from seed import L, add_listing, event, seed


def pending(eng) -> int:
    with Session(eng) as s:
        return s.scalar(select(func.count()).where(EventRow.notified.is_(None)))


@pytest.fixture
def smtp(monkeypatch):
    for k, v in {"SMTP_USER": "watch@example.org", "SMTP_PASS": "x", "HW_DIGEST_TO": "me@example.org"}.items():
        monkeypatch.setenv(k, v)
    with mock.patch("smtplib.SMTP") as m:
        yield m.return_value.__enter__.return_value


def test_send_marks_notified(pg, smtp):
    seed(pg)
    assert digest.urgent(pg)
    out = digest.send(pg)
    assert out.startswith("sent: Housing watch: 1 eligible, 2 closed, 1 source failing")
    msg = smtp.send_message.call_args.args[0]
    body = msg.get_body(("plain",)).get_content()
    assert "a 1 (Queens)" in body and "ELIGIBLE studio #4B $2,400" in body and "TimeoutError: boom" in body
    assert "a 2" not in body  # Bronx: filtered out, counted in the tail
    assert pending(pg) == 0 and not digest.urgent(pg)
    assert digest.send(pg) == "no events"


def test_dry_run_sends_nothing(pg, smtp):
    seed(pg)
    assert "Housing watch" in digest.send(pg, dry_run=True)
    smtp.send_message.assert_not_called()
    assert pending(pg) == 6


def test_closed_eligible_is_reported(pg, smtp):
    with Session(pg) as s, s.begin():
        add_listing(s, L("c", "1", "Queens", status="closed"))
        event(s, "c:1", "closed")
    subject, plain, _, ids = digest.build(pg)
    assert "Closed or removed" in plain and "c 1" in plain and len(ids) == 1


def test_urgent_needs_open_and_eligible(pg):
    with Session(pg) as s, s.begin():
        add_listing(s, L("c", "1", "Queens", status="waitlist"))
        add_listing(s, L("c", "2", "Queens", units=[Unit("2br", 3000, 130, bands=[Band(1, 1, 200000)])]))
        event(s, "c:1", "new")
        event(s, "c:2", "changed")
    assert not digest.urgent(pg)


def test_baseline_collapses(pg, smtp):
    with Session(pg) as s, s.begin():
        add_listing(s, L("new_src", "1", "Queens"))  # eligible
        add_listing(s, L("new_src", "2", "Brooklyn", units=[]))  # possible
        add_listing(s, L("new_src", "3", "Bronx"))  # excluded
        for k in ("new_src:1", "new_src:2", "new_src:3"):
            event(s, k, "new", detail={"baseline": True})
        event(s, "new_src:https://x", "watching", detail={"baseline": "true"}, age=timedelta(minutes=1))
    subject, plain, htm, ids = digest.build(pg)
    assert subject == "Housing watch: 1 eligible" and len(ids) == 4
    assert "[First seen] new_src 1 (Queens)" in plain and "new_src 2" not in plain
    assert "First run of 1 source(s) (new_src): 3 listings (1 eligible, shown above; 1 possible; 1 excluded)" in plain
    assert "1 pages now watched" in plain and "First run" in htm


def test_possible_and_page_changes_alone_are_silent(pg, smtp):
    with Session(pg) as s, s.begin():
        add_listing(s, L("p", "1", "Brooklyn", units=[]))  # possible: no unit details
        add_listing(s, L("p", "2", "Bronx", status="closed"))  # excluded, so its closing is not news
        event(s, "p:1", "new")
        event(s, "p:2", "closed")
        event(s, "p:https://x", "page_changed", detail={"added": ["new text"]})
    assert digest.send(pg) == "nothing notable; events marked read"
    smtp.send_message.assert_not_called()
    assert pending(pg) == 0


def test_baseline_without_eligible_is_silent(pg, smtp):
    with Session(pg) as s, s.begin():
        add_listing(s, L("new_src", "2", "Brooklyn", units=[]))
        event(s, "new_src:2", "new", detail={"baseline": True})
    assert digest.send(pg) == "nothing notable; events marked read"
    smtp.send_message.assert_not_called()
    assert pending(pg) == 0
