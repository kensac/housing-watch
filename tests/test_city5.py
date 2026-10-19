import socket
from pathlib import Path

from housing_watch import db, fetch
from housing_watch.adapters import city5

F = Path(__file__).parent / "fixtures" / "city5"


def _no_dns(*a, **k):
    raise socket.gaierror(8, "nodename nor servname provided, or not known")


def test_unreachable_is_a_stable_snapshot_and_coming_back_is_a_change(monkeypatch):
    monkeypatch.setattr(city5.socket, "getaddrinfo", _no_dns)
    down = city5.City5().fetch().snapshots
    assert [s.text for s in down] == [city5.UNREACHABLE]
    c = db.connect(":memory:")
    assert db.record_snapshots(c, "city5", down, now=1) == 1  # first sight: "watching"
    assert db.record_snapshots(c, "city5", city5.City5().fetch().snapshots, now=2) == 0  # still down: silent

    monkeypatch.setattr(city5.socket, "getaddrinfo", lambda *a, **k: [("ok",)])
    monkeypatch.setattr(fetch, "html", lambda url, **k: (F / "back.html").read_text())
    up = city5.City5().fetch().snapshots
    assert "Now leasing" in up[0].text and "https://city5.nyc/apply.pdf" in up[0].links
    assert db.record_snapshots(c, "city5", up, now=3) == 1
    ev = c.execute("SELECT type FROM events ORDER BY id DESC LIMIT 1").fetchone()
    assert ev["type"] == "page_changed"


def test_connection_or_http_failure_is_unreachable_too(monkeypatch):
    monkeypatch.setattr(city5.socket, "getaddrinfo", lambda *a, **k: [("ok",)])

    def boom(url, **k):
        raise fetch.FetchError(f"GET {url}: connection refused")

    monkeypatch.setattr(fetch, "html", boom)
    assert city5.City5().fetch().snapshots[0].text == city5.UNREACHABLE
