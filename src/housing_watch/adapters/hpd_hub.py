"""NYC HPD re-rentals hub: the page listing every HPD-approved marketing agent's re-rental page.

Snapshots only the page body (div.about-description), so nyc.gov's menus and footer never alert, and
tracks every outbound link in it: a new marketing agent (or an agent moving its listings page) shows up
as a new link. Each new link needs its own adapter. Re-rentals are first come, first served with the
agent directly (HPD/HDC waived Housing Connect for them through an anticipated 2027-04-30).
"""

from __future__ import annotations

from .. import fetch, parse
from ..models import Result, Snapshot
from ._page import PageAdapter
from ._pagekit import external_links, visible_text

URL = "https://www.nyc.gov/site/hpd/services-and-information/find-affordable-housing-re-rentals.page"
ROOT = "div.about-description"
MIN_LINKS = 30  # 35 agents + HDC + HCR on 2026-10-18; far fewer means the layout changed


def parse_hub(html: str) -> tuple[str, list[str]]:
    s = parse.soup(html)
    root = s.select_one(ROOT)
    if root is None:
        raise RuntimeError(f"{URL}: {ROOT} not found (layout changed?)")
    return visible_text(root, []), external_links(URL, root, skip_hosts=["nyc.gov"])


class HpdHub(PageAdapter):
    name = "hpd_hub"
    title = "NYC HPD re-rentals hub (marketing agents)"
    url = URL
    pages = [URL]
    every_hours = 4

    def fetch(self) -> Result:
        text, links = parse_hub(fetch.html(URL))
        if len(links) < MIN_LINKS:
            raise RuntimeError(f"{URL}: only {len(links)} outbound links")
        return Result(snapshots=[Snapshot(source=self.name, ext_id=URL, url=URL, text=text, links=links)])
