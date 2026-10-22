"""CGMR Compliance Partners: a GoDaddy page that has listed nothing so far (nav and footer only).

The URL HPD links (/housing-opportunities) is a 404; the site's own nav uses /housing-opportunities-1.
Any listing, PDF or flyer image that appears there is the change worth seeing.
"""

from __future__ import annotations

from ._pagekit import GODADDY_IGNORE, GoDaddyPage


class Cgmr(GoDaddyPage):
    name = "cgmr"
    title = "CGMR Compliance Partners"
    url = "https://cgmrcompliance.com/housing-opportunities-1"
    pages = [url]
    ignore = GODADDY_IGNORE
    min_text = 150  # the empty page is ~280 chars of repeated nav; less means it did not load
