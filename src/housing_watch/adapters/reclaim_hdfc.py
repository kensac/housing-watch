"""Reclaim HDFC (Sycamore Birch Management): Rent Manager SiteManager page for 10 scattered Bronx walk-ups.

The page publishes only "Application Period : <status>" and "Tenant Selection : <status>" plus the
addresses: no unit sizes, rents, AMI or income limits. It becomes one Listing (open while the
application period reads "Applications Accepted", otherwise closed; Bronx, so the criteria exclude it
by borough) and a page snapshot. The "Click HERE for Application" FileHandler link returned an empty
file on 2026-10-18; contact is https://sbmgmt.sitemanager.rentmanager.com/Contact.aspx.
"""

from __future__ import annotations

import re

from .. import fetch, parse
from ..models import Listing, Result
from ._page import PageAdapter
from ._pagekit import snapshot

URL = "https://sbmgmt.sitemanager.rentmanager.com/RECLAIMHDFC.aspx"
ADDR_RE = re.compile(r"^\d[\d-]*\s+.+,\s*Bronx\s+NY$", re.I)


def parse_page(html: str) -> Listing:
    text = parse.text(html)
    app = re.search(r"Application Period\s*:\s*(.+)", text)
    sel = re.search(r"Tenant Selection\s*:\s*(.+)", text)
    if not app:
        raise RuntimeError(f"{URL}: no 'Application Period' status (layout changed?)")
    addrs = [ln for ln in text.splitlines() if ADDR_RE.match(ln.strip())]
    period, selection = app.group(1).strip(), sel.group(1).strip() if sel else "n/a"
    return Listing(
        source="reclaim_hdfc",
        ext_id="reclaim-hdfc",
        title="Reclaim HDFC (scattered-site Bronx walk-ups)",
        url=URL,
        address="; ".join(addrs) or None,
        borough="Bronx",
        status="open" if re.search(r"accept", period, re.I) else "closed",
        kind="re-rental",
        notes=(
            f"Application period: {period}; tenant selection: {selection}. {len(addrs)} buildings, no elevator."
            " No sizes, rents or income limits published. Contact Sycamore Birch via the site's Contact page."
        ),
    )


class ReclaimHdfc(PageAdapter):
    name = "reclaim_hdfc"
    title = "Reclaim HDFC (Sycamore Birch)"
    url = URL
    pages = [URL]
    min_text = 300
    link_re = re.compile(r"FileHandler\.ashx", re.I)

    def fetch(self) -> Result:
        body = fetch.html(URL)
        return Result(listings=[parse_page(body)], snapshots=[snapshot(self, URL, body)])
