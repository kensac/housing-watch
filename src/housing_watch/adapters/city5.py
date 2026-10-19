"""City5 (HPD-listed marketing agent): city5.nyc has never been readable, so this watches for it to come back.

On 2026-10-18 the domain was registered (expires 2026-11-15) on Cloudflare nameservers but had no A
record, so every client fails at DNS. A run that cannot reach the site (DNS, connection or HTTP error)
does not fail: it returns a snapshot whose text is a fixed "unreachable" line (no error detail, so it is
identical every run and never alerts twice). The first run that gets a real page (the agent's site, or
a parking page if the domain lapses) differs from that line, so it is reported as a page change.
"""

from __future__ import annotations

import socket
from urllib.parse import urlparse

from ..fetch import FetchError
from ..models import Result, Snapshot
from ._page import PageAdapter

URL = "https://city5.nyc/"
UNREACHABLE = "city5.nyc unreachable (DNS, connection or HTTP error): nothing published"


def unreachable(name: str) -> Result:
    return Result(snapshots=[Snapshot(source=name, ext_id=URL, url=URL, text=UNREACHABLE)])


class City5(PageAdapter):
    name = "city5"
    title = "City5"
    url = URL
    pages = [URL]
    every_hours = 4
    min_text = 1  # once it resolves, even a one-line "coming soon" page is news

    def fetch(self) -> Result:
        try:
            socket.getaddrinfo(urlparse(URL).hostname, 443)
        except OSError:
            return unreachable(self.name)  # skip fetch's retries: there is nothing to connect to
        try:
            return super().fetch()
        except FetchError:
            return unreachable(self.name)
