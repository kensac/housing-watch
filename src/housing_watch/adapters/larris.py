"""LARRIS Marketing & Compliance: GoDaddy re-rentals page, empty so far.

The empty page still says "If this apartment isn't what you're looking for", so the boilerplate is not a
listing; a unit table (property, unit type, AMI, rent, income limits) appearing is the change to see.
"""

from __future__ import annotations

from ._pagekit import GODADDY_IGNORE, GoDaddyPage


class Larris(GoDaddyPage):
    name = "larris"
    title = "LARRIS Marketing & Compliance"
    url = "https://larrismarketing.com/re-rentals"
    pages = [url]
    ignore = GODADDY_IGNORE
    min_text = 600  # the empty page's boilerplate alone is ~1,400 chars
