"""Your Neighborhood Housing (Bronx marketing agent): GoDaddy home page "Listings" block + /available-listings-1.

The home page names one building (3531 Bruckner Boulevard) with no unit data; the listings page is empty.
A new building name, flyer image or PDF is the change to see. The business-hours widget ("Open today",
"09:00 am - 05:00 pm") changes by weekday and is ignored.
"""

from __future__ import annotations

from ._pagekit import GODADDY_IGNORE, GoDaddyPage


class YourNeighborhoodHousing(GoDaddyPage):
    name = "your_neighborhood_housing"
    title = "Your Neighborhood Housing"
    url = "https://yourneighborhoodhousing.com/"
    pages = [url, "https://yourneighborhoodhousing.com/available-listings-1"]
    every_hours = 12
    ignore = [*GODADDY_IGNORE, r"hours", r"(mon|tue|wed|thu|fri|sat|sun)[a-z]*", r"closed"]
    min_text = 200
