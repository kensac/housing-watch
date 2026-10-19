"""HPD (city) Mitchell-Lama: the program page, its open-waitlists PDF and its short-waitlists PDF.

The open-lists PDF ("NYC MITCHELL-LAMA DEVELOPMENTS WITH OPEN EXTERNAL WAIT LISTS (Rev mm-dd-yyyy)") is
parsed into one waitlist Listing per development, with one Unit per bedroom size that has units
(Open/Closed; "None" = no such units), the managing agent to request an application from in the notes
($75 non-refundable fee), and income bands from the program page's current income-limit table by
category (federally assisted rental / federally assisted co-op / non-federally assisted). In 2026 the
1-person cap is $148,500 at best, so a $150k single applicant is over income everywhere; the bands make
the criteria say so, and update themselves when HPD publishes new limits.

All three are also snapshotted: the page body (nyc.gov chrome excluded; its PDF links tracked) and the
PDFs' text, so a new revision is a page change. The weekly "last number approved" PDF is only tracked as
a link (it changes every week and concerns existing applicants). HCR (state) Mitchell-Lama waitlists are
covered by the nys_housing_search adapter.
"""

from __future__ import annotations

import re

from .. import fetch, parse
from ..models import Band, Listing, Result, Snapshot, Unit
from ._page import PageAdapter
from ._pagekit import snapshot

PAGE = "https://www.nyc.gov/site/hpd/services-and-information/mitchell-lama-program.page"
PDFS = "https://www.nyc.gov/assets/hpd/downloads/pdfs/services/"
OPEN_LISTS = PDFS + "ML-waiting-Lists-Status.pdf"
SHORT_LISTS = PDFS + "Short-waiting-Lists.pdf"
SIZES = ["studio", "1br", "2br", "3br", "4br"]  # PDF columns: Studio, One, Two, Three, Four Plus
ST = r"(Open|Closed|None)"
TYPE_RE = re.compile(rf"\b(Rental|Cooperative)\s+{ST}\s+{ST}\s+{ST}\s+{ST}\s+{ST}\b")
PHONE_RE = re.compile(r"^\(?\d{3}\s?\)?[\s-]?\d{3}[\s-]?\d{4}$")
NAME_RE = re.compile(r"^[A-Z0-9][A-Z0-9 .,'&/()-]*[A-Z]{2}[A-Z0-9 .,'&/()-]*$")
SEG_RE = re.compile(r"\S+(?: \S+)*")  # runs of text separated by 2+ spaces


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def income_limits(html: str) -> dict[str, dict[int, int]]:
    """Program page's limit table -> {"rental_fed": {1: 95040, ...}, "coop_fed": {...}, "non_fed": {...}}."""
    s = parse.soup(html)
    for table in s.find_all("table"):
        rows = parse.table_rows(str(table))
        if not rows or not re.search(r"household size", " ".join(rows[0]), re.I):
            continue
        head = [h.lower() for h in rows[0]]
        cols = {}
        for i, h in enumerate(head):
            if "non" in h:
                cols["non_fed"] = i
            elif "rental" in h:
                cols["rental_fed"] = i
            elif "cooperative" in h or "co-op" in h:
                cols["coop_fed"] = i
        out: dict[str, dict[int, int]] = {k: {} for k in cols}
        for r in rows[1:]:
            if not r or not r[0].strip().isdigit():
                continue
            for k, i in cols.items():
                if i < len(r) and parse.whole(r[i]):
                    out[k][int(r[0])] = parse.whole(r[i])
        if len(cols) == 3 and all(out.values()):
            return out
    raise RuntimeError(f"{PAGE}: income-limit table not found (layout changed?)")


def _segments(line: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(0)) for m in SEG_RE.finditer(line)]


def parse_open_lists(text: str, limits: dict[str, dict[int, int]] | None = None) -> list[Listing]:
    """pdftotext -layout of the open-lists PDF -> one waitlist Listing per development."""
    rev = re.search(r"\(Rev ([\d-]+)\)", text)
    out = []
    for page in re.split(r"(?=^.*OPEN EXTERNAL WAIT LISTS)", text, flags=re.M):
        lines = page.splitlines()
        req = next((ln.index("Request an Application") for ln in lines if "Request an Application" in ln), None)
        if req is None:
            continue
        right_col = req - 12  # agent column; its lines start a few chars left of the header
        # development blocks: [left-column lines], [agent lines], federally?, type line match
        blocks: list[dict] = []
        agent_lines: list[str] = []
        for ln in lines:
            if not ln.strip() or "Request an Application" in ln or "OPEN EXTERNAL" in ln:
                continue
            segs = _segments(ln)
            fed = None
            if segs and segs[0][0] < 6 and segs[0][1] in ("Yes", "No"):
                fed = segs[0][1] == "Yes"
                segs = segs[1:]
            left = [t for p, t in segs if p < 50]
            right = [t for p, t in segs if p >= right_col]
            m = TYPE_RE.search(ln)
            if m:
                pre = ln[: m.start()].strip()
                pre = re.sub(r"^(Yes|No)\b\s*", "", pre)
                left = [pre] if pre else []
            if left and NAME_RE.match(left[0].split("*")[0].strip()) and not m and "Subsided" not in left[0]:
                blocks.append({"name": left[0], "left": [], "fed": fed, "type": None, "agent": []})
                left = left[1:]
            if not blocks:
                continue
            b = blocks[-1]
            if fed is not None:
                b["fed"] = fed
            if m:
                b["type"] = m
            b["left"] += [t for t in left if t not in ("Housing", "Type")]
            agent_lines += [" ".join(right)] if right else []
        # agent entries end at a phone line; they appear in block order
        entries, cur = [], []
        for a in agent_lines:
            cur.append(a)
            if PHONE_RE.match(a):
                entries.append(cur)
                cur = []
        blocks = [b for b in blocks if b["type"] is not None]
        if len(entries) != len(blocks):
            entries = []  # columns did not line up: name no agent rather than the wrong one
        for i, b in enumerate(blocks):
            m = b["type"]
            name = b["name"]
            senior = "*" in name and bool(re.search(r"62|senior", name, re.I))
            clean = re.sub(r"\s*\*.*$", "", name).strip().title()
            phones = [x for x in b["left"] if PHONE_RE.match(x)]
            addr = ", ".join(x for x in b["left"] if not PHONE_RE.match(x))
            agent = entries[i] if i < len(entries) else []
            housing = m.group(1)
            cat = "non_fed" if b["fed"] is False else ("rental_fed" if housing == "Rental" else "coop_fed")
            caps = (limits or {}).get(cat, {})
            bands = [Band(h, None, cap) for h, cap in sorted(caps.items())]
            units = [
                Unit(size=sz, available=st == "Open", bands=list(bands))
                for sz, st in zip(SIZES, m.groups()[1:], strict=True)
                if st != "None"
            ]
            opens = [sz for sz, st in zip(SIZES, m.groups()[1:], strict=True) if st == "Open"]
            notes = (
                f"{'Co-op' if housing == 'Cooperative' else 'Rental'}"
                f"{'' if b['fed'] is None else (', federally assisted' if b['fed'] else ', not federally assisted')}."
                f" Open lists: {', '.join(opens) or 'none'}."
                f" Request an application ($75 non-refundable) from {'; '.join(agent) or 'the agent (see PDF)'}."
                + (f" Development phone {phones[0]}." if phones else "")
                + (f" Source: open-lists PDF Rev {rev.group(1)}." if rev else "")
            )
            out.append(
                Listing(
                    source="mitchell_lama",
                    ext_id=slug(clean),
                    title=f"{clean} (Mitchell-Lama)",
                    url=OPEN_LISTS,
                    address=addr or None,
                    borough=parse.borough(addr),
                    status="waitlist" if opens else "closed",
                    kind="waitlist",
                    units=units,
                    senior_only=senior,
                    notes=notes,
                )
            )
    return out


def pdf_snapshot(name: str, url: str, text: str) -> Snapshot:
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    return Snapshot(source=name, ext_id=url, url=url, text="\n".join(ln for ln in lines if ln))


class MitchellLama(PageAdapter):
    name = "mitchell_lama"
    title = "HPD Mitchell-Lama waitlists"
    url = PAGE
    pages = [PAGE]
    every_hours = 24
    min_text = 3000
    min_listings = 5  # 16 developments on the Rev 05-21-2026 list

    def fetch(self) -> Result:
        body = fetch.html(PAGE)
        snaps = [snapshot(self, PAGE, body, root="div.about-description")]
        open_txt = fetch.pdf_text(OPEN_LISTS)
        listings = parse_open_lists(open_txt, income_limits(body))
        if len(listings) < self.min_listings:
            raise RuntimeError(f"{OPEN_LISTS}: only {len(listings)} developments parsed")
        snaps.append(pdf_snapshot(self.name, OPEN_LISTS, open_txt))
        snaps.append(pdf_snapshot(self.name, SHORT_LISTS, fetch.pdf_text(SHORT_LISTS)))
        return Result(listings=listings, snapshots=snaps)
