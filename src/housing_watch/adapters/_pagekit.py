"""Helpers for page watchers that need more than `PageAdapter` gives: a snapshot of one part of a page,
extra links, and the quirks of GoDaddy Website Builder sites (CGMR, Infinite Horizons, LARRIS, Your
Neighborhood Housing, the Riverton placeholder).

GoDaddy sites render their text server-side, so a plain GET already has everything a headless browser
shows; rendering only adds GoDaddy's rotating "Airo" promo banner. Their flyers are images on
img1.wsimg.com, lazy-loaded (the <img> src is a placeholder), so image URLs are taken from the raw HTML
and stripped of their per-viewport resize suffix ("/:/rs=w:600,...").
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .. import fetch, parse
from ..models import Result, Snapshot
from ._page import COMMON_IGNORE, DOC_RE, PageAdapter

WSIMG_RE = re.compile(r"(?:https?:)?//img1\.wsimg\.com/isteam/ip/([0-9a-f-]{36})/([^\"'\s/:?&;\\]+)")
GODADDY_IGNORE = [
    r"go from idea to live site.*",  # Airo promo banner (rendered pages only)
    r"try airo.*",
    r"start for free",
    r"\|",
    r"(open|closed) (today|now)",  # business-hours widget
    r"get directions",
    r"this website uses cookies\.?",
    r"accept",
    r"powered by",
]


def norm_link(url: str) -> str:
    url = url.split("#")[0]
    m = WSIMG_RE.search(url)
    if m:
        return f"https://img1.wsimg.com/isteam/ip/{m.group(1)}/{m.group(2)}"
    return url


def wsimg_images(body: str) -> set[str]:
    """Every image a GoDaddy site uploaded, from the raw HTML (lazy images have no real src)."""
    return {f"https://img1.wsimg.com/isteam/ip/{a}/{b}" for a, b in WSIMG_RE.findall(body)}


def visible_text(root, ignore: Iterable[str]) -> str:
    pats = [re.compile(p, re.I) for p in [*COMMON_IGNORE, *ignore]]
    src = root if isinstance(root, BeautifulSoup | str) else str(root)  # parse.text takes a soup or html
    return "\n".join(ln for ln in parse.text(src).splitlines() if not any(p.fullmatch(ln.strip()) for p in pats))


def snapshot(
    adapter: PageAdapter,
    url: str,
    body: str,
    *,
    root: str | None = None,
    extra_links: Iterable[str] = (),
) -> Snapshot:
    """What `PageAdapter.fetch` does for one page, optionally limited to the CSS selector `root`."""
    s = parse.soup(body)
    node: Tag | None = s.select_one(root) if root else s
    if node is None:
        raise RuntimeError(f"{url}: {root!r} not found (page layout changed?)")
    links = {norm_link(x) for x in extra_links}
    for a in node.find_all("a", href=True):
        href = urljoin(url, a["href"].strip())
        if urlparse(href).netloc.endswith("godaddy.com"):  # "made with GoDaddy" footer link
            continue
        if DOC_RE.search(href) or (adapter.link_re and adapter.link_re.search(href)):
            links.add(norm_link(href))
    for img in node.find_all("img", src=True):
        src = urljoin(url, img["src"])
        if adapter.link_re and adapter.link_re.search(src):
            links.add(norm_link(src))
    txt = visible_text(node, adapter.ignore)
    if len(txt) < adapter.min_text:
        raise RuntimeError(f"{url}: only {len(txt)} chars of text (blocked or JS not rendered?)")
    return Snapshot(source=adapter.name, ext_id=url, url=url, text=txt, links=sorted(links))


def external_links(url: str, root: Tag, skip_hosts: Iterable[str] = ()) -> list[str]:
    """Outbound links under `root`, i.e. to hosts other than `url`'s and `skip_hosts`."""
    own = {urlparse(url).netloc.removeprefix("www."), *skip_hosts}
    out = set()
    for a in root.find_all("a", href=True):
        href = urljoin(url, a["href"].strip()).split("#")[0]
        host = urlparse(href).netloc.lower().removeprefix("www.")
        if href.startswith("http") and host and not any(host == h or host.endswith("." + h) for h in own):
            out.add(href)
    return sorted(out)


class GoDaddyPage(PageAdapter):
    """PageAdapter for GoDaddy Website Builder sites: plain GET, flyer images tracked as links."""

    render = False
    ignore = GODADDY_IGNORE

    def fetch(self) -> Result:
        snaps = []
        for url in self.pages:
            body = fetch.rendered(url) if self.render else fetch.html(url)
            snaps.append(snapshot(self, url, body, extra_links=wsimg_images(body)))
        return Result(snapshots=snaps)
