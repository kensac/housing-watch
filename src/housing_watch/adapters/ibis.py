"""Ibis Advisors: Power Pages portal of rent-stabilized re-rentals (so far all 130% AMI, all in the Bronx).

The home page is server-rendered: one card (heading + blurb + optional "Apply" link) per listing. Cards
without a link are "Coming Soon" and have no detail page yet. A detail page carries the address/unit as
text, but its "Available Units and Income Requirements" table is a PNG screenshot, OCR'd with tesseract
(`--psm 4`: the default `--psm 6` drops the data row). The table gives one income range for several
household sizes ("1-3 People $87,507 - $198,510"), so per-size caps come from `bands_from_row` with the
AMI inferred from the top size's cap: that keeps ads still on older (2025) limits honest. OCR text is
cached per image URL (the screenshot name carries its timestamp, so a new table means a new URL).
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import time
from urllib.parse import quote, urljoin

from .. import fetch, parse
from ..models import Listing, Result, Snapshot, Unit
from . import Adapter

BASE = "https://ibis.powerappsportals.com/"
_REFRESH = 7 * 86400  # re-OCR a cached image after a week, in case it was replaced under the same name


def ocr_png(data: bytes) -> str:
    """Tesseract on raw image bytes; psm 4 (single column of rows) reads these one-row tables cleanly."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.png")
        with open(p, "wb") as f:
            f.write(data)
        try:
            out = subprocess.run(["tesseract", p, "-", "--psm", "4"], capture_output=True, text=True, timeout=120)
        except FileNotFoundError as e:
            raise fetch.FetchError("tesseract not installed") from e
        return out.stdout


def parse_cards(html: str) -> list[dict]:
    """Home page -> [{title, blurb, href}] in page order (href None for Coming Soon cards)."""
    s = parse.soup(html)
    h2 = s.find(lambda t: t.name == "h2" and "Re-Rentals" in t.get_text())
    if h2 is None:
        raise RuntimeError("ibis: 'Affordable Housing Re-Rentals' heading missing (page changed or blocked)")
    cards: list[dict] = []
    for el in h2.find_all_next(["h1", "h3", "h4", "p", "a"]):
        txt = re.sub(r"[\s​]+", " ", el.get_text(" ")).strip()
        if el.name == "p" and re.search(r"copyright", txt, re.I):
            break
        if not txt:
            continue
        if el.name in ("h1", "h3", "h4"):
            cards.append({"title": txt, "blurb": "", "href": None})
        elif cards and el.name == "p" and not cards[-1]["blurb"]:
            cards[-1]["blurb"] = txt
        elif cards and el.name == "a" and re.search(r"apply", txt, re.I) and el.get("href"):
            cards[-1]["href"] = urljoin(BASE, el["href"])
    return cards


def parse_table(ocr: str) -> list[Unit]:
    """OCR of the income table -> Units. Rows: '<size> $rent <h-h> People $min - $max'."""
    flat = re.sub(r"\s+", " ", ocr)
    a = parse.ami(flat)
    units = []
    row = re.compile(
        r"(studio|\d\s*-?\s*bed(?:room)?s?)\W+\$\s*([\d,]+\.?\d*)\W+(\d\s*(?:-|–|to)?\s*\d?)\s*(?:people|persons?)"
        r"\W+(\$?\s*[\d,]+(?:\.\d+)?\s*(?:-|–|to)\s*\$?\s*[\d,]+(?:\.\d+)?)",
        re.I,
    )
    for m in row.finditer(flat):
        # ami_pct=None: infer the % from the top size's printed cap (catches ads still on older limits)
        bands = parse.bands_from_row(m.group(3), m.group(4), None)
        units.append(Unit(size=parse.size(m.group(1)), rent=parse.money(m.group(2)), ami=a, bands=bands))
    return units


def parse_detail(url: str, html: str, ocr: str | None, card: dict) -> Listing:
    t = parse.text(html)
    i, j = t.find("Sign in"), t.find("Applicant Requirements")
    head = re.sub(r"[​]+", "", t[i + 7 : j] if 0 <= i < j else "").strip()
    lines = [x.strip() for x in head.splitlines() if x.strip()]
    m = re.search(r"#\s*([0-9]+[A-Z]?|[A-Z][0-9]+)\b", head)
    unit_no = m.group(1) if m else None
    street = re.sub(r"\s*#\s*\w+$", "", lines[0]) if lines else card["title"]
    address = ", ".join([street, *(re.sub(r"\s*#\s*\w+$", "", x) for x in lines[1:2])])
    units = parse_table(ocr) if ocr else []
    if len(units) == 1:
        units[0].unit_no = unit_no
    s = parse.soup(html)
    form = next((a["href"] for a in s.find_all("a", href=True) if "forms.app" in a["href"]), None)
    notes = f"Apply: forms.app {form} (one form per unit)" if form else "Apply: see listing page"
    if not units:
        notes += "; income table image not read"
        units = [Unit(size=parse.size(f"{card['title']} {card['blurb']}"), unit_no=unit_no)]
    return Listing(
        source="ibis",
        ext_id=url.rstrip("/").split("/")[-1],
        title=f"{street}{' #' + unit_no if unit_no else ''}",
        url=url,
        address=address,
        borough=parse.borough(head) or parse.borough(card["blurb"]),
        status="open",
        units=units,
        notes=notes,
    )


def coming_soon(card: dict) -> Listing:
    blob = f"{card['title']} {card['blurb']}"
    m = re.search(r"\bat (\d[\w\s.-]*?(?:Ave(?:nue)?|St(?:reet)?|Blvd|Pl(?:ace)?|Rd|Road)\b)", card["blurb"])
    title = re.sub(r"^coming soon\s*-\s*", "", card["title"], flags=re.I)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return Listing(
        source="ibis",
        ext_id=f"coming-soon-{slug}",
        title=title,
        url=BASE,
        address=m.group(1) if m else None,
        borough=parse.borough(blob),
        units=[Unit(size=parse.size(blob))],
        notes="Coming soon: no detail page or application yet",
    )


class Ibis(Adapter):
    name = "ibis"
    title = "Ibis Advisors"
    url = BASE
    min_listings = 1  # listings, or the home-page snapshot when no card is up

    def _ocr(self, img_url: str) -> str:
        self._seen.add(img_url)
        cache = self.cache.setdefault("ocr", {})
        hit = cache.get(img_url)
        if hit and time.time() - hit["at"] < _REFRESH:
            return hit["text"]
        data = fetch.get(img_url).content
        digest = hashlib.sha256(data).hexdigest()
        if hit and hit.get("sha") == digest:
            hit["at"] = time.time()
            return hit["text"]
        txt = ocr_png(data)
        cache[img_url] = {"at": time.time(), "sha": digest, "text": txt}
        return txt

    def fetch(self) -> Result:
        self._seen: set[str] = set()
        home = fetch.html(BASE)
        cards = parse_cards(home)
        out = []
        for c in cards:
            if not c["href"]:
                out.append(coming_soon(c))
                continue
            body = fetch.html(c["href"])
            # the table is an image hosted on the portal itself (icons come from a CDN); a page can also carry
            # a photo, so screenshots go first and the first image whose OCR holds a table row wins
            imgs = [
                i["src"]
                for i in parse.soup(body).find_all("img", src=True)
                if i["src"].startswith("/") and re.search(r"\.(png|jpe?g)$", i["src"], re.I)
            ]
            imgs.sort(key=lambda x: "screenshot" not in x.lower())
            ocr = None
            try:
                for img in imgs:
                    txt = self._ocr(urljoin(BASE, quote(img)))
                    if parse_table(txt):
                        ocr = txt
                        break
            except fetch.FetchError:
                pass  # no tesseract: keep the listing (size from the card), flagged in notes
            out.append(parse_detail(c["href"], body, ocr, c))
        ocr_cache = self.cache.get("ocr", {})
        for k in [k for k in ocr_cache if k not in self._seen]:
            del ocr_cache[k]
        if not out:
            txt = parse.text(home)
            return Result(snapshots=[Snapshot(source=self.name, ext_id=BASE, url=BASE, text=txt)])
        return Result(listings=out)
