"""Small deterministic helpers shared by adapters: sizes, money, boroughs, html text."""

from __future__ import annotations

import html as _html
import re

from bs4 import BeautifulSoup

from . import ami as _ami

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
    t = str(text).replace("$", "")
    m = re.search(r"\d[\d,]*(?:\.\d+)?", t)
    if not m:
        return None
    num = m.group(0)
    if re.search(r",\d{1,2}$", num) and (more := re.match(r" (\d[\d,]*(?:\.\d+)?)", t[m.end() :])):
        num += more.group(1)  # a thousands group split by a stray space: "1,5 47.67"
    if re.fullmatch(r"\d{1,3}\.\d{3}", num):
        num = num.replace(".", ",")  # a dot as the thousands separator: "2.468"
    return float(num.replace(",", ""))


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


def bands_from_row(hh_text: str, income_text: str, ami_pct: int | None):
    """One ad row ('1-3 people', '$95,452 - $198,510') -> per-household Bands (ami.bands does the caps)."""
    lo, hi = money_range(income_text)
    sizes = household_range(hh_text)
    return _ami.bands(sizes, lo, hi, ami_pct)[0] if sizes else []
