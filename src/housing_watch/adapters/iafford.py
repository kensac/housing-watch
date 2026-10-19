"""iAfford NY (iaffordny.com) re-rentals: the same webaryco mini_lotteries API as AFNY, Origin iaffordny.com.

Its Angular page has no links (cards are click handlers), so the API is the only practical source.
Most listings apply by standard mail to "<listing title> C/O IAFFORD NY, 365 Flushing Ave Suite #503";
a few (The Garrison) by email, which is taken from the listing text.
"""

from __future__ import annotations

from .afny import WebarycoAdapter


class IAfford(WebarycoAdapter):
    name = "iafford"
    title = "iAfford NY"
    url = "https://iaffordny.com/re-rentals"
    site = "https://iaffordny.com"
    every_hours = 4  # first come, first served
    min_listings = 3
