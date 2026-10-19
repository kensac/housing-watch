"""Per-process politeness: threads never multiply the rate one site sees."""

import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import pytest

from housing_watch import fetch


class Probe:
    def __init__(self, delay=0.05):
        self.delay, self.lock = delay, threading.Lock()
        self.inflight, self.peak, self.starts = defaultdict(int), defaultdict(int), defaultdict(list)
        self.total = self.total_peak = 0

    def __call__(self, host):
        with self.lock:
            self.inflight[host] += 1
            self.total += 1
            self.peak[host] = max(self.peak[host], self.inflight[host])
            self.total_peak = max(self.total_peak, self.total)
            self.starts[host].append(time.monotonic())
        time.sleep(self.delay)
        with self.lock:
            self.inflight[host] -= 1
            self.total -= 1


class Resp:
    status_code, text, content, headers = 200, "ok", b"ok", {}

    def raise_for_status(self):
        pass

    def json(self):
        return {"solution": {"status": 200, "response": "<html>ok</html>"}}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(fetch, "_MIN_GAP", 0.02)
    monkeypatch.setattr(fetch, "_last", {})


def test_one_request_in_flight_per_host(monkeypatch):
    probe = Probe()
    monkeypatch.setattr(fetch._client, "get", lambda url, headers=None: probe(urlparse(url).netloc) or Resp())
    urls = [f"https://{h}.example/{i}" for h in "ab" for i in range(4)]
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(fetch.get, urls))
    assert probe.peak == {"a.example": 1, "b.example": 1}
    assert probe.total_peak == 2  # different hosts still run side by side
    for starts in probe.starts.values():
        assert all(b - a >= 0.02 for a, b in zip(starts, starts[1:], strict=False))


def test_one_render_at_a_time(monkeypatch):
    probe = Probe()
    monkeypatch.setattr(fetch, "BYPARR_URL", "http://byparr:8191")
    monkeypatch.setattr(fetch.httpx, "post", lambda *a, **kw: probe("byparr") or Resp())
    with ThreadPoolExecutor(4) as ex:
        out = list(ex.map(fetch.rendered, [f"https://{h}.example/" for h in "abcd"]))
    assert out == ["<html>ok</html>"] * 4 and probe.peak["byparr"] == 1


def test_rendered_without_byparr_needs_byparr(monkeypatch):
    monkeypatch.setattr(fetch, "BYPARR_URL", "")
    with pytest.raises(fetch.NeedsByparr):
        fetch.rendered("https://a.example/")
    assert not issubclass(fetch.NeedsByparr, fetch.FetchError)  # adapters skipping a bad page must not eat it


def test_render_retries_until_expected_text(monkeypatch):
    pages = iter(["<html>shell</html>", "<html>shell</html>", "<html>Available Re-Rentals</html>"])
    waits = []

    class R(Resp):
        def __init__(self, body):
            self.body = body

        def json(self):
            return {"solution": {"status": 200, "response": self.body}}

    def post(*a, json=None, **kw):
        waits.append(json["waitInSeconds"])
        return R(next(pages))

    monkeypatch.setattr(fetch, "BYPARR_URL", "http://byparr:8191")
    monkeypatch.setattr(fetch.httpx, "post", post)
    assert "Available" in fetch.rendered("https://t.example/", wait_ms=6000, expect="Available Re-Rentals")
    assert waits == [6, 12, 18]  # longer each try
    pages = iter(["<html>shell</html>"] * 3)
    with pytest.raises(fetch.FetchError, match="missing after 3 renders"):
        fetch.rendered("https://t.example/", expect="Available Re-Rentals")
