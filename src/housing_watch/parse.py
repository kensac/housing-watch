"""Small deterministic helpers shared by adapters: sizes, money, boroughs, html text."""

from __future__ import annotations

import html as _html
import re

from bs4 import BeautifulSoup

_SIZE_PATTERNS = [
    (r"\b(sro|single room)\b", "sro"),
    (r"\bstudio|\b0\s*-?\s*(br|bd|bed|bedroom)s?\b|efficiency", "studio"),
    (r"\b(1|one)\s*-?\s*(br|bd|bed|bedroom)s?\b|\b1\s*bed", "1br"),
    (r"\b(2|two)\s*-?\s*(br|bd|bed|bedroom)s?\b", "2br"),
    (r"\b(3|three)\s*-?\s*(br|bd|bed|bedroom)s?\b", "3br"),
    (r"\b(4|four)\s*-?\s*(br|bd|bed|bedroom)s?\b", "4br"),
    (r"\b(5|five)\s*-?\s*(br|bd|bed|bedroom)s?\b", "5br"),
    (r"\b(6|six)\s*-?\s*(br|bd|bed|bedroom)s?\b", "6br"),
]


def size(text: str | None) -> str:
    t = (text or "").lower().strip()
    if t in {"0", "0br"}:
        return "studio"
    if re.fullmatch(r"[1-6]", t):
        return f"{t}br"
    for pat, name in _SIZE_PATTERNS:
        if re.search(pat, t):
            return name
    return "unknown"


def money(text: str | float | int | None) -> float | None:
    if text is None:
        return None
    if isinstance(text, int | float):
        return float(text)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(text).replace("$", ""))
    return float(m.group(0).replace(",", "")) if m else None


def whole(text: str | float | int | None) -> int | None:
    v = money(text)
    return int(round(v)) if v is not None else None


def money_range(text: str) -> tuple[int | None, int | None]:
    """'$82,557 – $154,440' / '$107,479-$154,440' / '$80,511.- $154,440' -> (82557, 154440)."""
    nums = [int(float(x.replace(",", ""))) for x in re.findall(r"\d[\d,]{3,}(?:\.\d+)?", text or "")]
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], None
    return nums[0], nums[1]


def household_range(text: str) -> list[int]:
    """'1 person' -> [1]; '1-3 people' / '1–3' -> [1,2,3]."""
    nums = [int(x) for x in re.findall(r"\d+", text or "")]
    if not nums:
        return []
    if len(nums) >= 2 and re.search(r"\d\s*(-|–|—|to)\s*\d", text):
        return list(range(nums[0], nums[1] + 1))
    return [nums[0]]


def ami(text: str | None) -> int | None:
    m = re.search(r"(\d{2,3})\s*%", text or "")
    return int(m.group(1)) if m else None


BOROUGHS = {
    "manhattan": "Manhattan",
    "new york, ny": "Manhattan",
    "brooklyn": "Brooklyn",
    "queens": "Queens",
    "long island city": "Queens",
    "astoria": "Queens",
    "jamaica": "Queens",
    "flushing": "Queens",
    "forest hills": "Queens",
    "ridgewood": "Queens",
    "bronx": "Bronx",
    "staten island": "Staten Island",
}

# 5-digit zip prefixes -> borough (USPS). Checked before names, which are ambiguous ("New York").
_ZIP = [
    (range(10001, 10283), "Manhattan"),
    (range(10451, 10476), "Bronx"),
    (range(10301, 10315), "Staten Island"),
    (range(11201, 11257), "Brooklyn"),
    (range(11004, 11110), "Queens"),
    (range(11351, 11698), "Queens"),
]


def borough(*texts: str | None) -> str | None:
    blob = " ".join(t for t in texts if t)
    for z in re.findall(r"\b(1[01]\d{3})\b", blob):
        zi = int(z)
        for rng, name in _ZIP:
            if zi in rng:
                return name
    low = blob.lower()
    for k, v in BOROUGHS.items():
        if k in low:
            return v
    return None


def text(html_or_soup) -> str:
    """Visible text of a page, scripts/styles dropped, whitespace collapsed one line per block."""
    soup = html_or_soup if isinstance(html_or_soup, BeautifulSoup) else BeautifulSoup(html_or_soup, "lxml")
    for t in soup(["script", "style", "noscript", "svg", "template"]):
        t.decompose()
    raw = soup.get_text("\n")
    lines = [re.sub(r"\s+", " ", _html.unescape(x)).strip() for x in raw.splitlines()]
    return "\n".join(x for x in lines if x)


def soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except ValueError:
        # bs4's lxml builder chokes on some malformed namespaced attributes (_getNsTag)
        return BeautifulSoup(html, "html.parser")


def table_rows(soup_or_html) -> list[list[str]]:
    s = soup_or_html if isinstance(soup_or_html, BeautifulSoup) else soup(soup_or_html)
    out = []
    for tr in s.find_all("tr"):
        cells = [re.sub(r"\s+", " ", c.get_text(" ")).strip() for c in tr.find_all(["td", "th"])]
        if any(cells):
            out.append(cells)
    return out


# HUD 2026 NYC 100% AMI by household size (130% of these = the $154,440 / $176,410 / ... seen on every ad).
AMI100 = {1: 118_800, 2: 135_700, 3: 152_700, 4: 169_600, 5: 183_200, 6: 196_800, 7: 210_400, 8: 224_000}


def ami_max(ami_pct: int | None, household: int) -> int | None:
    """Income cap for a household size at an AMI %, for ads that print one range across several sizes."""
    if not ami_pct or household not in AMI100:
        return None
    return int(round(AMI100[household] * ami_pct / 100 / 10) * 10)  # HUD rounds to $10


def bands_from_row(hh_text: str, income_text: str, ami_pct: int | None):
    """One ad row -> per-household Bands. A row covering several sizes gets each size's cap from AMI."""
    from .models import Band

    sizes = household_range(hh_text)
    lo, hi = money_range(income_text)
    if not sizes:
        return []
    if len(sizes) == 1:
        return [Band(sizes[0], lo, hi)]
    if not ami_pct and hi and sizes[-1] in AMI100:
        # no AMI printed: infer the % from the top size's cap, then derive each size's cap from it
        ami_pct = round(hi / AMI100[sizes[-1]] * 100, 1)
    out = []
    for h in sizes:
        cap = ami_max(ami_pct, h)
        out.append(Band(h, lo, hi if h == sizes[-1] else cap))
    return out
