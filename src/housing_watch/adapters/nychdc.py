"""NYC HDC re-rentals: a public JSON API of cards, each pointing at a flyer PDF that holds the real tiers.

The card's `rent_range` spans every tier, so units come only from the flyer (`pdftotext -layout`). Flyers
are laid out as tables with vertically centred labels and, sometimes, two AMI tiers side by side, so the
parser works on column positions: household rows ("1 Person") pair in order with income ranges, rows split
into tiers where the household count restarts, and each tier takes the size label and rent nearest to it
in its column. The AMI % is derived from the income caps (HUD limits), snapped to a % printed on the flyer.
Flyer text is cached by PDF path (HDC uploads a new file, under a new path, for every new flyer).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import ami, fetch, parse
from ..models import Listing, Result, Unit
from . import Adapter

BASE = "https://www.nychdc.com"
API = BASE + "/api/re-rentals?current-page={page}&offset={offset}"
PAGE = BASE + "/find-re-rentals"
PER_PAGE = 9

_INCOME = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+)(?:\.\d+)?\s*[-–—]\s*\$\s?(\d{1,3}(?:,\d{3})+)(?:\.\d+)?")
_RENT = re.compile(r"\$\s?(\d{1,2},\d{3}|\d{3,4})(?![\d,])(?:\.\d{1,2})?")
_HH = re.compile(r"\b(\d)\s*(?:[-–]\s*(\d)\s*)?(?:persons?|people)\b", re.I)
_SIZE_RANGE = re.compile(r"\b(studio|\d)\s*[-–]\s*(\d)\s*[-–]?\s*bed(?:room)?s?\b", re.I)
_SIZE = re.compile(r"\b(studio|sro|efficiency|[0-6]\s*[-–]?\s*bed(?:room)?s?|one\s+bedroom|two\s+bedroom)\b", re.I)
_LONE_BED = re.compile(r"\bbed(?:room)?s?\b", re.I)
_AMI_TOKEN = re.compile(r"(\d{2,3})\s*%\s*(?=area|ami|\(ami|$)", re.I)
_SET_ASIDE = re.compile(r"\bMI\b|(?i:mobility|hearing|vision|visual|\bH/V\b)")
_MONTHS = "january february march april may june july august september october november december".split()


@dataclass
class _Tok:
    line: int
    x: int
    val: object


@dataclass
class _Group:
    hh: list[_Tok] = field(default_factory=list)
    inc: list[_Tok] = field(default_factory=list)

    def span(self) -> tuple[int, int]:
        lines = [t.line for t in self.hh + self.inc]
        return min(lines), max(lines)


def _dist(line: int, span: tuple[int, int]) -> int:
    lo, hi = span
    return 0 if lo <= line <= hi else min(abs(line - lo), abs(line - hi))


def _nearest(toks: list[_Tok], span: tuple[int, int], limit: int = 6) -> _Tok | None:
    best = min(toks, key=lambda t: (_dist(t.line, span), t.line), default=None)
    return best if best is not None and _dist(best.line, span) <= limit else None


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] + 1 and b[0] <= a[1] + 1


def _sizes(label: str) -> list[str]:
    m = _SIZE_RANGE.search(label)
    if m:
        lo = 0 if m.group(1).lower() == "studio" else int(m.group(1))
        return ["studio" if n == 0 else f"{n}br" for n in range(lo, int(m.group(2)) + 1)]
    return [parse.size(re.sub(r"\s*[-–]\s*", " ", label))]


def _derive_ami(hh: list[int], hi: int | None, printed: list[int]) -> int | None:
    top = hh[-1] if hh else None
    if not hi or top not in ami.AMI100:
        return min(printed) if len(set(printed)) == 1 else None
    m = ami.match(hi, top, sizes=[top])
    pct = m[2] if m else hi / ami.AMI100[top] * 100
    near = min(printed, key=lambda p: abs(p - pct), default=None)
    return near if near is not None and abs(near - pct) <= 7 else round(pct)


def parse_flyer(text: str) -> list[Unit]:
    """Flyer text (pdftotext -layout) -> one Unit per tier (size + AMI + set-aside)."""
    lines = [re.sub(r"(\d),\s+(\d{3})\b", r"\1,\2", ln) for ln in text.splitlines()]
    hh_t, inc_t, rent_t, size_t = [], [], [], []
    printed = sorted({int(m.group(1)) for ln in lines for m in _AMI_TOKEN.finditer(ln) if 20 <= int(m.group(1)) <= 200})
    for i, ln in enumerate(lines):
        for m in _INCOME.finditer(ln):
            lo, hi = (int(g.replace(",", "")) for g in m.groups())
            if hi >= 10_000:
                inc_t.append(_Tok(i, m.start(), (lo, hi)))
                ln = ln[: m.start()] + " " * (m.end() - m.start()) + ln[m.end() :]
        for m in _HH.finditer(ln):
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            if 1 <= a <= b <= 9:
                hh_t.append(_Tok(i, m.start(), list(range(a, b + 1))))
        for m in _RENT.finditer(ln):
            v = float(m.group(0).replace("$", "").replace(",", "").strip())
            if 100 <= v < 15_000:
                rent_t.append(_Tok(i, m.start(), v))
        found = [(m.start(), m.group(0)) for m in _SIZE_RANGE.finditer(ln)]
        if not found:
            found = [(m.start(), m.group(0)) for m in _SIZE.finditer(ln)]
        if not found:  # "1" on one line, "Bedroom" under it
            for m in _LONE_BED.finditer(ln):
                x = m.start()
                for j in range(i - 1, max(i - 9, -1), -1):
                    seg = lines[j][max(0, x - 6) : x + 10]
                    if seg.strip():
                        if d := re.fullmatch(r"\s*(\d)\s*", seg):
                            found.append((x, f"{d.group(1)} bedroom"))
                        break
        size_t += [_Tok(i, x, lbl) for x, lbl in found]
    if not hh_t or not inc_t:
        return []
    first = min(t.line for t in hh_t)
    head = next((i for i, ln in enumerate(lines[:first]) if re.search(r"unit\s*size", ln, re.I)), first - 6)
    last = max(t.line for t in hh_t + inc_t)
    size_t = [t for t in size_t if head - 3 <= t.line <= last + 3]
    rent_t = [t for t in rent_t if head - 3 <= t.line <= last + 3]

    # column panels: tiers printed side by side have their household column at different x
    xs = sorted({t.x for t in hh_t})
    panels: list[list[int]] = []
    for x in xs:
        if panels and x - panels[-1][-1] <= 20:
            panels[-1].append(x)
        else:
            panels.append([x])
    px = [min(p) for p in panels]

    def panel_of(x: int, left_of_hh: bool) -> int:
        if left_of_hh:  # labels/rents sit left of their panel's household column
            right = [k for k, p in enumerate(px) if p > x]
            return right[0] if right else len(px) - 1
        left = [k for k, p in enumerate(px) if p <= x]
        return left[-1] if left else 0

    groups: list[tuple[int, _Group]] = []  # (panel, tier)
    for k in range(len(px)):
        hhs = sorted((t for t in hh_t if panel_of(t.x, False) == k), key=lambda t: (t.line, t.x))
        incs = sorted((t for t in inc_t if panel_of(t.x, False) == k), key=lambda t: (t.line, t.x))
        pairs: list[tuple[_Tok, _Tok | None]] = []
        if len(hhs) == len(incs):  # rows can be offset by a line; order still pairs them
            pairs = list(zip(hhs, incs, strict=True))
        else:
            free = list(incs)
            for h in hhs:
                best = min(free, key=lambda t: abs(t.line - h.line), default=None)
                if best is not None:
                    free.remove(best)
                pairs.append((h, best))
        for h, inc in pairs:
            g = groups[-1][1] if groups and groups[-1][0] == k else None
            if g is None or h.val[0] <= g.hh[-1].val[-1]:  # household count restarts: next tier
                g = _Group()
                groups.append((k, g))
            g.hh.append(h)
            if inc is not None:
                g.inc.append(inc)

    units: list[tuple[Unit, bool]] = []  # (unit, came from one multi-household waitlist row)
    # "*Units in 516 Schroeders Avenue are designated for Senior": tiers under the "*<address>" heading
    senior_from = None
    if re.search(r"designated for senior", text, re.I):
        senior_from = next((i for i in range(head, len(lines)) if re.match(r"\s*\*\d", lines[i])), None)
    for k, g in groups:
        span = g.span()
        # a rent belongs to this tier only if no other tier's household column sits between them
        fence = max([-1] + [px[j] for j, o in groups if px[j] < px[k] and _overlap(o.span(), span)])
        rents = [t for t in rent_t if fence < t.x < px[k]]
        labels = [t for t in size_t if t.x < px[k]]
        top_hh, top_hi = [], None
        for h, inc in zip(g.hh, g.inc, strict=False):
            top_hh, top_hi = h.val, inc.val[1]
        ami = _derive_ami(top_hh, top_hi, printed)
        bands = []
        for h, inc in zip(g.hh, g.inc, strict=False):
            hh_txt = f"{h.val[0]}-{h.val[-1]} people" if len(h.val) > 1 else f"{h.val[0]} person"
            bands += parse.bands_from_row(hh_txt, f"${inc.val[0]:,} - ${inc.val[1]:,}", ami)
        lbl = _nearest(labels, span)
        rent = _nearest(rents, span)
        sa = None
        if m := _SET_ASIDE.search(" ".join(lines[span[0] : span[1] + 1])):
            sa = "mobility" if m.group(0).lower() in ("mi", "mobility") else "hearing/vision"
        if senior_from is not None and span[0] > senior_from:
            sa = "senior"
        for size in _sizes(lbl.val) if lbl else ["unknown"]:
            u = Unit(size=size, rent=rent.val if rent else None, ami=ami, bands=bands, set_aside=sa)
            units.append((u, any(len(h.val) > 1 for h in g.hh)))
    # whole-flyer restrictions ("The units are designated for applicants whose household includes ...")
    flat = re.sub(r"\s+", " ", text)
    m = re.search(r"(?:unit|units) (?:is|are) designated for applicants whose household includes.{0,160}", flat, re.I)
    if m:
        d = m.group(0).lower()
        whole = "senior" if "62 years" in d else ("mobility" if "mobility" in d else "hearing/vision")
        for u, _ in units:
            u.set_aside = u.set_aside if whole != "senior" and u.set_aside else whole
    out: dict[str, tuple[Unit, bool]] = {}
    for u, agg in units:  # one tier in both a waitlist row ("1 - 7 People") and an immediate table: keep the table
        have = out.get(u.key())
        if have is None or (have[1] and not agg):
            out[u.key()] = (u, agg)
    return [u for u, _ in out.values()]


def _deadline(flat: str) -> str | None:
    m = re.search(
        r"(?:no later than|deadline[^.]{0,40}?|by)\s+("
        + "|".join(_MONTHS)
        + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)",
        flat,
        re.I,
    )
    if not m:
        return None
    return f"{m.group(3)}-{_MONTHS.index(m.group(1).lower()) + 1:02d}-{int(m.group(2)):02d}"


def _how_to_apply(text: str) -> str | None:
    flat = re.sub(r"\s+", " ", text)
    bits = []
    m = re.search(
        r"self-addressed envelope(?: with postage)? to:?\s*(.{10,160}?)"
        r"(?=\s*Applications may|\s*When is|\s*What Happens|$)",
        flat,
        re.I,
    )
    if m:
        bits.append("mail SASE to " + m.group(1).strip(" ."))
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", flat)))
    if emails:
        bits.append("email " + ", ".join(e.rstrip(".") for e in emails))
    phone = re.search(r"calling\s+([\d().\s-]{10,16}\d)", flat)
    if phone:
        bits.append("call " + phone.group(1).strip())
    url = re.search(r"visit\s+(https?://\S+)", flat)
    if url:
        bits.append(url.group(1).rstrip("."))
    return ("Request a paper application: " + "; ".join(bits)) if bits else None


def parse_card(card: dict, flyer: str | None) -> Listing:
    """One API card + its flyer text (None when the PDF could not be read) -> Listing."""
    pdf = BASE + card["pdf"] if card.get("pdf") else PAGE
    title = re.sub(r"\s+", " ", card["title"]).strip()
    boro = (card.get("borough") or "").replace("The Bronx", "Bronx").strip() or None
    boro = boro or parse.borough(card.get("address"))
    text = flyer or ""
    head = " ".join(text.strip().splitlines()[:3]) + " " + title
    flat = re.sub(r"\s+", " ", text)
    if re.search(r"immediate", head, re.I) or not re.search(r"wait", head, re.I):
        status, kind = "open", "re-rental"
    else:
        status, kind = "waitlist", "waitlist"
    senior = bool((card.get("age_restriction_copy") or "").strip())
    if senior and re.search(r"units in .{0,80}? (?:are|is) designated for senior", flat, re.I):
        senior = False  # only one building of a multi-building flyer is senior; its tiers say so
    units = parse_flyer(text) if text else []
    return Listing(
        source="nychdc",
        ext_id=str(card["nid"]),
        title=title,
        url=pdf,
        address=re.sub(r"\s+", " ", card.get("address") or "").strip() or None,
        borough=boro,
        status=status,
        kind=kind,
        units=units,
        deadline=_deadline(flat),
        senior_only=senior,
        notes=_how_to_apply(text) or f"See flyer {pdf}",
    )


class NycHdc(Adapter):
    name = "nychdc"
    title = "NYC HDC re-rentals"
    url = PAGE
    every_hours = 4
    min_listings = 8

    def fetch(self) -> Result:
        cards: list[dict] = []
        page, total = 1, None
        while total is None or len(cards) < total:
            d = fetch.json(API.format(page=page, offset=PER_PAGE * (page - 1)))
            batch = d.get("json") or []
            total = int(d["pagination"]["total-results"])
            if not batch:
                break
            cards += batch
            page += 1
            if page > 20:
                raise RuntimeError("HDC pagination did not terminate")
        cache: dict = self.cache.setdefault("flyers", {})
        out = []
        for c in cards:
            pdf = c.get("pdf") or ""
            text = cache.get(pdf)
            if text is None and pdf:
                try:
                    text = fetch.pdf_text(BASE + pdf)
                    cache[pdf] = text
                except fetch.FetchError:
                    text = None  # listing still reported, as "possible"; retried next run
            out.append(parse_card(c, text))
        live = {c.get("pdf") for c in cards}
        for k in [k for k in cache if k not in live]:
            del cache[k]
        return Result(listings=out)
