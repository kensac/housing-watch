"""Related Management's Affordable Housing Group (ahgleasing.com): one-unit re-rentals + accessibility waitlists.

The home page names each opportunity and links a PDF ad (directly, or via an intermediate page such as
/accessibility-units/). Each ad holds size, AMI, rent and a per-household-size income table, read with
`pdftotext -layout`. The ad's "clicking here" application link is only in the PDF's link annotations (/URI).
PDFs are fetched conditionally (ETag / Last-Modified) and their parses cached by URL.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import zlib
from urllib.parse import urljoin, urlparse

from .. import fetch, parse
from ..models import Band, Listing, Result, Snapshot, Unit
from . import Adapter

HOME = "https://ahgleasing.com/"
_HH_ROW = re.compile(r"(?:^|\s)([1-8])\s*(?:persons?|people)?\s+(\$[\d,]+\s*[-–]\s*\$[\d,]+)\s*$", re.I)
_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4"}
_SIZE = re.compile(r"\b(studio|efficiency)\b|\b(one|two|three|four|[1-4])\b(?:\s+\S+){0,4}?\s+bed(room)?s?\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@related\.com", re.I)


def pdf_uris(data: bytes) -> list[str]:
    """Link annotations of a PDF, from raw and zlib-compressed streams, in order, de-duplicated."""
    found = re.findall(rb"/URI\s*\(([^)]*)\)", data)
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            found += re.findall(rb"/URI\s*\(([^)]*)\)", zlib.decompress(m.group(1)))
        except zlib.error:
            continue
    out: list[str] = []
    for u in found:
        s = u.decode("latin-1").strip()
        if s and s not in out:
            out.append(s)
    return out


def _pdftotext(data: bytes) -> str:
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.pdf")
        with open(p, "wb") as f:
            f.write(data)
        return subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, text=True, timeout=120).stdout


def _size(region: str) -> str:
    m = re.search(
        r"\b(studio|efficiency)\b|\b(one|two|three|four|[1-4])\b(?:\s+\S+){0,4}?\s+bed(room)?s?\b", region, re.I
    )
    if not m:
        return "unknown"
    if m.group(1):
        return "studio"
    return parse.size(f"{_WORDS.get(m.group(2).lower(), m.group(2))} bedroom")


def _set_aside(region: str) -> str | None:
    kinds = [k for k in ("mobility", "visual", "hearing") if re.search(k, region, re.I)]
    if kinds == ["mobility"] and "and/or" in region:
        kinds += ["visual", "hearing"]  # "Mobility Impairment and/or" + "Visual/Hearing" printed past the rows
    if re.search(r"\bsenior|62 years", region, re.I):
        kinds.append("senior")
    return "/".join(kinds) or None


def parse_ad(text: str) -> list[Unit]:
    """Units of one ad. Household rows end a line ('2 people  $130,629 - $176,410', or just '2  $61,881 - ...');
    a run of increasing household sizes is one unit, whose size/rent/AMI/set-aside are read from the text
    between the previous run and the end of this one (layout puts the size label mid-run)."""
    lines = text.splitlines()
    blocks: list[dict] = []
    region: list[str] = []
    for ln in lines:
        m = _HH_ROW.search(ln)
        if not m:
            region.append(ln)
            continue
        hh = int(m.group(1))
        if not blocks or hh <= blocks[-1]["rows"][-1][0]:
            blocks.append({"rows": [], "region": region})
            region = []
        b = blocks[-1]
        b["region"] = b["region"] + region + [ln[: m.start()]]
        region = []
        b["rows"].append((hh, m.group(2)))
    units = []
    for b in blocks:
        reg = " ".join(re.sub(r"\s+", " ", x) for x in b["region"])
        amis = re.findall(r"(\d{2,3})\s*%", reg)
        a = int(amis[-1]) if amis else None
        rent = next((parse.money(x) for x in re.findall(r"\$\s?\d[\d,]*(?:\.\d\d)?(?!\s*[-–])", reg)), None)
        bands: list[Band] = []
        for hh, rng in b["rows"]:
            bands += parse.bands_from_row(str(hh), rng, a)
        units.append(Unit(size=_size(reg), rent=rent, ami=a, bands=bands, set_aside=_set_aside(reg)))
    return units


def parse_listing(
    title: str, url: str, ad_url: str, ad_text: str, uris: list[str], section: str, page_text: str = ""
) -> Listing:
    units = parse_ad(ad_text)
    access = bool(re.search(r"accessib|impairment", section + " " + ad_text[:600], re.I))
    for u in units:
        if access and not u.set_aside:
            u.set_aside = "mobility/visual/hearing"
    m = re.search(r"unit\s*#\s*([0-9]+[a-z]?)", ad_text, re.I)
    if m and len(units) == 1:
        units[0].unit_no = m.group(1).upper()
    waitlist = bool(re.search(r"wait(ing)?\s*list", section + " " + ad_text[:400], re.I))
    closed = bool(re.search(r"\b(filled|no longer accepting|closed to new)\b", ad_text[:400], re.I))
    addr = title.split(" at ", 1)[1].strip() if " at " in title else None
    app = next((u for u in uris if re.search(r"app|\.pdf", u, re.I) and u != ad_url), None)
    email = (_EMAIL.findall(ad_text) or ["AHGApplications@related.com"])[0]
    how = f"download the application {app} and email it to {email}" if app else f"request an application from {email}"
    return Listing(
        source="related_ahg",
        ext_id=urlparse(ad_url).path.rsplit("/", 1)[-1].removesuffix(".pdf").lower(),
        title=title.split(" at ", 1)[0].strip(),
        url=ad_url,
        address=addr,
        borough=parse.borough(title) or parse.borough(ad_text[:600]),
        status="closed" if closed else ("waitlist" if waitlist else "open"),
        kind="waitlist" if waitlist else "re-rental",
        units=units,
        senior_only=bool(re.search(r"\bseniors? (only|housing)|62 years (of age )?(and|or) older", ad_text, re.I)),
        notes=f"Apply: {how}."
        + ("" if waitlist else " One unit per ad, first come first served, no waitlist.")
        + (f" ({page_text})" if page_text else ""),
    )


def home_entries(html: str, base: str = HOME) -> list[tuple[str, str, str]]:
    """(title, href, section heading) for each opportunity named on the home page."""
    s = parse.soup(html)
    out = []
    for a in s.find_all("a", href=True):
        t = re.sub(r"\s+", " ", a.get_text(" ")).strip()
        href = urljoin(base, a["href"])
        if " at " not in t or "ahgleasing.com" not in urlparse(href).netloc:
            continue
        head = a.find_previous(string=re.compile(r"VACANCY|WAITING LIST|OPPORTUNITY", re.I))
        out.append((t, href, re.sub(r"\s+", " ", str(head or "")).strip()))
    return out


class RelatedAHG(Adapter):
    name = "related_ahg"
    title = "Related Affordable Housing Group"
    url = HOME
    every_hours = 4  # single first-come units, "no waitlist once filled"
    min_listings = 1  # a listing per ad, or a snapshot of the home page when it names none

    def _pdf(self, url: str) -> dict:
        """{'text', 'uris'} for a PDF, re-downloaded only when its ETag/Last-Modified moved."""
        cache = self.cache.setdefault("pdfs", {})
        hit = cache.get(url)
        hdrs = {}
        if hit and hit.get("etag"):
            hdrs["If-None-Match"] = hit["etag"]
        if hit and hit.get("modified"):
            hdrs["If-Modified-Since"] = hit["modified"]
        if hdrs:
            fetch._polite(url)
            try:
                r = fetch._client.get(url, headers=hdrs)
                if r.status_code == 304:
                    return hit
            except Exception:  # noqa: BLE001 - fall through to a plain fetch
                pass
        r = fetch.get(url)
        data = r.content
        sha = hashlib.sha256(data).hexdigest()
        if hit and hit.get("sha") == sha:
            ent = {**hit, "etag": r.headers.get("etag"), "modified": r.headers.get("last-modified")}
        else:
            ent = {"sha": sha, "text": _pdftotext(data), "uris": pdf_uris(data)}
            ent.update(etag=r.headers.get("etag"), modified=r.headers.get("last-modified"))
        cache[url] = ent
        return ent

    def fetch(self) -> Result:
        home = fetch.html(HOME)
        if "Affordable Housing Group" not in home:
            raise RuntimeError("ahgleasing.com home page does not look right")
        out = []
        seen = set()
        for title, href, section in home_entries(home):
            ads = [href] if href.lower().endswith(".pdf") else []
            note = ""
            if not ads:  # intermediate page (accessibility units): its PDF links are the ads
                sub = parse.soup(fetch.html(href))
                ads = [urljoin(href, a["href"]) for a in sub.find_all("a", href=True) if ".pdf" in a["href"].lower()]
                if re.search(r"general waiting list[^.]*closed", parse.text(sub), re.I):
                    note = "general waiting list closed; accessibility applicants only"
            for ad in ads:
                if ad in seen:
                    continue
                seen.add(ad)
                pdf = self._pdf(ad)
                out.append(parse_listing(title, href, ad, pdf["text"], pdf["uris"], section, note))
        live = seen
        cache = self.cache.get("pdfs", {})
        for u in [u for u in cache if u not in live]:
            del cache[u]
        if out:
            return Result(listings=out)
        txt = "\n".join(ln for ln in parse.text(home).splitlines() if not re.fullmatch(r"\w+ \d{1,2}, \d{4}", ln))
        return Result(snapshots=[Snapshot(source=self.name, ext_id=HOME, url=HOME, text=txt)])
