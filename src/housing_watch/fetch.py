"""HTTP, JS/Cloudflare rendering (byparr), PDF text and OCR. All deterministic, no AI.

Politeness is per process, shared by every worker thread: at most one request in flight to a host,
a minimum gap between request starts to it, and one byparr render at a time. More threads therefore
never mean a higher rate for any one site.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections import defaultdict
from urllib.parse import urlparse

import httpx

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
BYPARR_URL = os.environ.get("BYPARR_URL", "")  # e.g. http://byparr:8191 on hetzner's proxy network
_MIN_GAP = float(os.environ.get("HW_HOST_GAP", "0.5"))  # politeness per host, seconds
_last: dict[str, float] = {}
_host_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
_render_lock = threading.Lock()

_client = httpx.Client(
    headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"},
    follow_redirects=True,
    timeout=httpx.Timeout(45.0, connect=15.0),
)


class FetchError(RuntimeError):
    pass


class NeedsByparr(Exception):
    """fetch.rendered() was called on a worker without BYPARR_URL; the fleet re-queues the job on `byparr`.

    Not a FetchError, so adapters that skip one failed page do not swallow it.
    """


def _wait_gap(host: str) -> None:
    wait = _last.get(host, 0) + _MIN_GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last[host] = time.monotonic()


@contextlib.contextmanager
def _slot(url: str):
    """Hold the host's one in-flight slot for the duration of a request."""
    host = urlparse(url).netloc
    with _host_locks[host]:
        _wait_gap(host)
        yield


def _polite(url: str) -> None:
    """Gap only, for callers that use _client directly; waits out any request in flight to the host."""
    host = urlparse(url).netloc
    with _host_locks[host]:
        _wait_gap(host)


def get(url: str, *, headers: dict | None = None, retries: int = 2, allow_304: bool = False) -> httpx.Response:
    """GET with retries. allow_304=True returns a 304 (send If-None-Match / If-Modified-Since in headers)."""
    err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with _slot(url):
                r = _client.get(url, headers=headers)
            if r.status_code in (429, 502, 503, 504) and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            if allow_304 and r.status_code == 304:
                return r
            r.raise_for_status()
            return r
        except httpx.HTTPError as e:
            err = e
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
    raise FetchError(f"GET {url}: {err}")


def post_json(url: str, body: dict, *, headers: dict | None = None) -> dict:
    try:
        with _slot(url):
            r = _client.post(url, json=body, headers=headers)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise FetchError(f"POST {url}: {e}") from e


def conditional(url: str, cache: dict) -> tuple[bytes | None, bool]:
    """Fetch url, re-using cache {etag, last_modified} validators. Returns (content, changed).

    content is None when the server answered 304 (caller keeps its cached parse).
    """
    h = {}
    if cache.get("etag"):
        h["If-None-Match"] = cache["etag"]
    if cache.get("last_modified"):
        h["If-Modified-Since"] = cache["last_modified"]
    r = get(url, headers=h or None, allow_304=True)
    if r.status_code == 304:
        return None, False
    cache["etag"] = r.headers.get("etag")
    cache["last_modified"] = r.headers.get("last-modified")
    return r.content, True


def html(url: str, **kw) -> str:
    return get(url, **kw).text


def json(url: str, **kw):
    try:
        return get(url, **kw).json()
    except ValueError as e:
        raise FetchError(f"JSON {url}: {e}") from e


def rendered(url: str, wait_ms: int = 4000) -> str:
    """HTML after JavaScript (and any Cloudflare challenge) ran, via byparr (FlareSolverr API)."""
    if not BYPARR_URL:
        raise NeedsByparr(f"render {url}: BYPARR_URL not set on this worker")
    try:
        with _render_lock, _slot(url):  # byparr drives one browser: one render at a time
            r = httpx.post(
                f"{BYPARR_URL.rstrip('/')}/v1",
                json={"cmd": "request.get", "url": url, "maxTimeout": 90000, "waitInSeconds": wait_ms // 1000},
                timeout=120,
            )
        r.raise_for_status()
        sol = r.json().get("solution") or {}
        body = sol.get("response") or ""
        if not body or int(sol.get("status", 200)) >= 400:
            raise FetchError(f"render {url}: status {sol.get('status')}")
        return body
    except (httpx.HTTPError, ValueError) as e:
        raise FetchError(f"render {url}: {e}") from e


def pdf_text(url: str) -> str:
    """pdftotext -layout of a PDF; falls back to OCR when the PDF is a scan."""
    data = get(url).content
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.pdf")
        with open(p, "wb") as f:
            f.write(data)
        out = subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, text=True, timeout=120).stdout
        if len(out.strip()) > 80:
            return out
        if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
            return out
        subprocess.run(["pdftoppm", "-r", "200", "-png", "-l", "4", p, os.path.join(d, "pg")], timeout=180)
        pages = sorted(x for x in os.listdir(d) if x.startswith("pg"))
        return "\n".join(_ocr_file(os.path.join(d, x)) for x in pages)


def ocr_image(url: str) -> str:
    data = get(url).content
    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(urlparse(url).path)[1] or ".png") as f:
        f.write(data)
        f.flush()
        return _ocr_file(f.name)


def _ocr_file(path: str) -> str:
    if not shutil.which("tesseract"):
        raise FetchError("tesseract not installed")
    return subprocess.run(["tesseract", path, "-", "--psm", "6"], capture_output=True, text=True, timeout=180).stdout
