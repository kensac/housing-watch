"""Generic page watcher for sources that publish no structure: fingerprint the visible text + document links.

Declare one subclass per source with `pages` (and `render=True` for JS/Cloudflare sites). Volatile text
(dates, counters, cookie banners) is stripped via `ignore` regexes so only real changes alert.
"""

from __future__ import annotations

import re
from typing import ClassVar
from urllib.parse import urljoin

from .. import fetch, parse
from ..models import Result, Snapshot
from . import Adapter

DOC_RE = re.compile(
    r"\.(pdf|docx?|xlsx?)(\?|$)|/_files/ugd/|img1\.wsimg\.com|forms\.(office|cloud\.microsoft)|"
    r"airtable\.com|rentcafe|apply|application",
    re.I,
)
COMMON_IGNORE = [
    r"©.*",
    r"copyright.*",
    r"all rights reserved.*",
    r"cookie.*",
    r"we use cookies.*",
    r"accept all.*",
    r"\b\d{1,2}:\d{2}\s*(am|pm)\b.*",
    r"last updated.*",
    r"powered by.*",
]


class PageAdapter(Adapter):
    pages: ClassVar[list[str]] = []
    render: ClassVar[bool] = False
    ignore: ClassVar[list[str]] = []
    link_re: ClassVar[re.Pattern | None] = None  # extra links worth tracking (listing detail pages)
    min_text: ClassVar[int] = 80  # fewer visible characters than this = the fetch did not really work

    def fetch(self) -> Result:
        snaps = []
        pats = [re.compile(p, re.I) for p in COMMON_IGNORE + self.ignore]
        for url in self.pages:
            body = fetch.rendered(url) if self.render else fetch.html(url)
            s = parse.soup(body)
            links = set()
            for a in s.find_all("a", href=True):
                href = urljoin(url, a["href"].strip())
                if DOC_RE.search(href) or (self.link_re and self.link_re.search(href)):
                    links.add(href.split("#")[0])
            for img in s.find_all("img", src=True):
                src = urljoin(url, img["src"])
                if self.link_re and self.link_re.search(src):
                    links.add(src)
            txt = "\n".join(ln for ln in parse.text(s).splitlines() if not any(p.fullmatch(ln.strip()) for p in pats))
            if len(txt) < self.min_text:
                raise RuntimeError(f"{url}: only {len(txt)} chars of text (blocked or JS not rendered?)")
            snaps.append(Snapshot(source=self.name, ext_id=url, url=url, text=txt, links=sorted(links)))
        return Result(snapshots=snaps)
