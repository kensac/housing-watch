"""Infinite Horizons (ihrerentals.com): GoDaddy photo gallery + every /house-* detail page it links.

The unit/rent/income tables are screenshots on img1.wsimg.com, so the flyer image URLs are tracked as
links: a new flyer is a new listing even when no text changes. Detail pages are discovered from the
gallery each run (the sitemap also lists unedited template pages, /house-4../house-7, which are not
listings). The home page lists nothing. Page text and flyer can disagree (/house-1 says "temporarily
closed" over a flyer for another building); a human reads the image.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Result
from ._pagekit import GODADDY_IGNORE, GoDaddyPage, snapshot, wsimg_images

GALLERY = "https://ihrerentals.com/photo-gallery"


def detail_pages(html: str) -> list[str]:
    s = parse.soup(html)
    out = {urljoin(GALLERY, a["href"]).split("#")[0] for a in s.find_all("a", href=True)}
    return sorted(u for u in out if re.fullmatch(r"https://ihrerentals\.com/house-[\w-]+", u))


class InfiniteHorizons(GoDaddyPage):
    name = "infinite_horizons"
    title = "Infinite Horizons (ihrerentals.com)"
    url = GALLERY
    pages = [GALLERY]
    ignore = GODADDY_IGNORE
    link_re = re.compile(r"ihrerentals\.com/house-|streeteasy\.com|foresthillsmha", re.I)
    min_text = 300

    def fetch(self) -> Result:
        body = fetch.html(GALLERY)
        snaps = [snapshot(self, GALLERY, body, extra_links=wsimg_images(body))]
        for url in detail_pages(body):
            page = fetch.html(url)
            snaps.append(snapshot(self, url, page, extra_links=wsimg_images(page)))
        return Result(snapshots=snaps)
