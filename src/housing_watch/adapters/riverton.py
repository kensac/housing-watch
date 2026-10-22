"""Riverton Square (East Harlem, 1,229 regulated units): watched through its own sites, not StreetEasy.

HPD links StreetEasy's building page, but StreetEasy answers every automated client (curl, headless
Chromium, byparr) with a PerimeterX "Press & Hold" 403, so its rentals stay a manual check in a real
browser. What can be watched deterministically: rivertonlottery.com (the A&E lottery site, still showing
the 2018 lottery and its waitlist-ad PDF) and rivertonsquare.com (a GoDaddy "Launching Soon"
placeholder). A new lottery, PDF or a real site replacing the placeholder shows up as a change.
"""

from __future__ import annotations

import re

from ._pagekit import GODADDY_IGNORE, GoDaddyPage


class Riverton(GoDaddyPage):
    name = "riverton"
    title = "Riverton Square"
    url = "https://rivertonlottery.com/"
    pages = ["https://rivertonlottery.com/", "https://rivertonsquare.com/"]
    ignore = [*GODADDY_IGNORE, r"drop us a line!", r"send"]
    link_re = re.compile(r"housingconnect|streeteasy\.com|rivertonlottery\.com/wp-content/uploads", re.I)
    min_text = 300
