"""Pre-fill paper application PDFs that are a known form, from the applicant file (HW_APPLICANT_FILE).

The applicant file is private JSON that lives only on the host; nothing here stores it or the filled PDF.
The applicant's own signature line is signed (their `signature_image`, else their name in a handwriting
font) and dated today, unless `"sign": false`. The SSN is never filled (optional on these forms). A PDF
that is not a known form is not filled at all.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from base64 import b64decode
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from . import fetch

APPLICANT_FILE = os.environ.get("HW_APPLICANT_FILE", "")
HAND = Path(__file__).parent / "fonts" / "HomemadeApple-Regular.ttf"  # Apache 2.0, fonts/LICENSE.txt
NY = ZoneInfo("America/New_York")
_URL = re.compile(r"https?://[^\s;,)'\"]+?(?:\.pdf|[?&]id=[\w-]+)", re.I)


def pdf_urls(notes: str | None) -> list[str]:
    """Application PDF links in a listing's notes (adapters write them there), in order, deduped."""
    return list(dict.fromkeys(_URL.findall(notes or "")))


def applicant(path: str | None = None) -> dict | None:
    """The applicant JSON, or None when the file is not configured or missing (the feature is then off).
    Read on every call, so editing the file needs no restart."""
    p = Path(path or APPLICANT_FILE) if (path or APPLICANT_FILE) else None
    return json.loads(p.read_text()) if p and p.is_file() else None


_blanks: dict[str, tuple[float, bytes]] = {}


def blank(url: str) -> bytes:
    """The unfilled PDF, fetched politely and kept for an hour (the panel checks it, then the download fills it)."""
    hit = _blanks.get(url)
    if hit and time.monotonic() - hit[0] < 3600:
        return hit[1]
    data = fetch.get(url).content
    if len(_blanks) >= 32:
        _blanks.clear()
    _blanks[url] = (time.monotonic(), data)
    return data


def _money(v) -> str:
    return "" if v in (None, "") else f"{float(v):,.0f}"


def _digits(v) -> str:  # for narrow table columns, where a comma makes six digits wrap
    return "" if v in (None, "") else f"{float(v):.0f}"


def _yes_no(group: str, on: str, off: str):
    """A Yes/No radio group: True → on-state, False → off-state, missing → untouched."""
    return lambda v: {group: on if v else off} if v is not None else {}


def _boxes(yes: str, no: str):
    """Two separate checkboxes used as a Yes/No pair."""
    return lambda v: {yes if v else no: "/Yes"} if v is not None else {}


# --- HPD's HC2.0 Affordable Housing Application (the Marketing Handbook's "Attachment G-1") -------------

_HC2_LANG = {
    "english": "English", "spanish": "Español Spanish", "french": "Français French",
    "haitian creole": "Kreyòl Ayisyen Haitian Creole", "chinese": "toggle_4", "arabic": "toggle_5",
    "russian": "toggle_7", "korean": "toggle_8", "urdu": "toggle_9", "bangla": "toggle_10",
}  # fmt: skip
_HC2_SUBSIDY = {"hpd": "Yes  HPD Section 8", "nycha": "Yes  NYCHA Section 8", "other": "Yes  Other Rental"}
_HC2_TOTAL_1A = (
    "1A TOTAL ANNUAL INCOME FROM EMPLOYMENT AND SELFEMPLOYMENT add all amounts from Annual Income column in this table"
)
_HC2_MONTHS = ["Text13", "Text14", "Text15", "Text16", "Text17", "Text18"]
_HC2_JOB_YRS = ["Yrs", "undefined_9", "undefined_10", "undefined_11", "undefined_12", "undefined_13"]


def _hc2_row(prefix: str, i: int) -> str:
    """Row i (0 = Self) of a table whose rows are named `<prefix>Self`, `<prefix>Row2`, ..."""
    return f"{prefix}Self" if i == 0 else f"{prefix}Row{i + 1}"


def _hc2(a: dict) -> dict[str, str]:
    addr, mail, ph = a.get("address") or {}, a.get("mailing") or {}, a.get("phones") or {}
    v: dict[str, str] = {
        "First Name": a.get("first", ""), "Middle Initial": a.get("middle", ""), "Last Name": a.get("last", ""),
        "Street Address": addr.get("street", ""), "Apartment": addr.get("apt", ""), "City": addr.get("city", ""),
        "State": addr.get("state", ""), "Zip": addr.get("zip", ""),
        "Your Pronouns heshethey optional": a.get("pronouns", ""),
        "Cell Phone": ph.get("cell", ""), "Home Phone": ph.get("home", ""), "Work Phone": ph.get("work", ""),
        "Email Address": a.get("email", ""),
    }  # fmt: skip
    if mail:
        v |= {
            "Check if mailing address is different than Current Living Address above": "/On",
            "Building House_2": mail.get("number", ""), "Street_2": mail.get("street", ""),
            "Apartment_3": mail.get("apt", ""), "PO Box": mail.get("po_box", ""), "City_3": mail.get("city", ""),
            "State_3": mail.get("state", ""), "Zip_3": mail.get("zip", ""),
        }  # fmt: skip
    if lang := _HC2_LANG.get(str(a.get("language", "")).lower()):
        v[lang] = "/On"
    v |= _boxes("Check Box1", "Check Box2")(a.get("nycha"))
    v |= _boxes("Check Box3", "Check Box4")(a.get("nycha_household"))
    v |= _boxes("Check Box5", "Check Box6")(a.get("shelter"))
    # household: Self first, then the other members (relationship column is SelfRow1..6)
    me = " ".join(x for x in (a.get("first"), a.get("middle"), a.get("last")) if x)
    people = [{"name": me, "birth_date": a.get("birth_date", ""), "gender": a.get("gender", "")}]
    people += list(a.get("members") or [])
    for i, m in enumerate(people[:7]):
        v[f"First Middle Initial  Last Name SuffixRow{i + 1}"] = m.get("name", "")
        v[_hc2_row("Birth Date MMDDYY", i)] = m.get("birth_date", "")
        v[_hc2_row("Gender Identification Optional", i)] = m.get("gender", "")
        if i:
            v[f"SelfRow{i}"] = m.get("relationship", "")
    acc = a.get("accommodation")
    if acc is not None:
        v |= (
            {"Check Box7": "/Yes", "Yes  please specify the accommodation required": acc}
            if acc
            else {"Check Box8": "/Yes"}
        )
    v |= _yes_no("undefined_7", "/Yes", "/No")(a.get("city_employee"))
    v |= _boxes("Check Box9", "Check Box10")(a.get("students"))
    # income from employment: Self's row is unprefixed, later rows name the member in SelfRowN_2
    jobs = list(a.get("jobs") or [])[:6]
    for i, j in enumerate(jobs):
        if i:
            v[f"SelfRow{i}_2"] = j.get("member", "")
        v[_hc2_row("Employer Name  Address", i)] = j.get("employer", "")
        v[_HC2_JOB_YRS[i]] = str(j.get("years", ""))
        v[_HC2_MONTHS[i]] = str(j.get("months", ""))
        v[_hc2_row("Amount Paid ", i)] = _digits(j.get("amount"))
        v[_hc2_row("How Often Ex weekly biweekly monthly annually", i)] = j.get("how_often", "")
        v[_hc2_row("Annual Income", i)] = _digits(j.get("annual"))
    other = list(a.get("other_income") or [])[:7]
    for i, o in enumerate(other):
        sfx = "_2" if i < 6 else ""  # the 7th row's fields have no suffix
        if i:
            v[f"SelfRow{i}_3" if i < 6 else "SelfRow6_2"] = o.get("member", "")
        v[_hc2_row("Type of Income", i)] = o.get("type", "")
        v[_hc2_row("Amount Paid ", i) + sfx] = _digits(o.get("amount"))
        v[_hc2_row("How Often Ex weekly biweekly monthly annually", i) + sfx] = o.get("how_often", "")
        v[_hc2_row("Annual Income", i) + sfx] = _digits(o.get("annual"))
    t1 = sum(float(j.get("annual") or 0) for j in jobs)
    t2 = sum(float(o.get("annual") or 0) for o in other)
    v[_HC2_TOTAL_1A] = _money(t1) if jobs else ""
    v["Text12"] = _money(t2) if other else ""
    v["Add together the total annual income amounts from 1A and 2A above"] = _money(t1 + t2) if jobs or other else ""
    assets = list(a.get("assets") or [])[:6]
    if "assets" in a:
        v["undefined_15"] = "/Yes_3" if assets else "/No_3"
    for i, s in enumerate(assets):
        if i:
            v[f"SelfRow{i}_4"] = s.get("member", "")
        v[_hc2_row("Type of Asset or Account", i)] = s.get("type", "")
        v[_hc2_row("BankInstitution", i)] = s.get("bank", "")
    sub = a.get("subsidy")
    if sub is not None:
        kind, _, name = str(sub or "no").partition(":")
        v[_HC2_SUBSIDY.get(kind.lower(), "No_4")] = "/On"
        v["SubsidyCertificate"] = name
    return {k: val for k, val in v.items() if val not in (None, "")}


@dataclass(frozen=True)
class Form:
    name: str
    signature: frozenset[str]  # field names that identify the form (all must be present)
    values: Callable[[dict], dict[str, str]]
    sign: tuple[str, str] | None = None  # the applicant's (signature field, date field)


FORMS = [
    Form(
        "HC2.0 Affordable Housing Application",
        frozenset({"First Name", "Last Name", "Birth Date MMDDYYSelf", "Employer Name  AddressSelf", "Text12",
                   "Add together the total annual income amounts from 1A and 2A above", "Yes  HPD Section 8"}),
        _hc2,
        sign=("Signature", "Date"),
    ),
]  # fmt: skip


def detect(pdf: bytes) -> Form | None:
    """The known form this PDF is, by its fillable fields; None for anything else (flat scans, other forms)."""
    try:
        fields = set(PdfReader(io.BytesIO(pdf)).get_fields() or {})
    except Exception:
        return None
    return next((f for f in FORMS if f.signature <= fields), None)


def _press(page, buttons: dict[str, str]) -> None:
    """Checkboxes and radio groups: the widget whose on-state is the value shows it, its siblings show /Off.

    pypdf's form update sets a radio group's value but not its widgets' appearance states."""
    for ref in page.get("/Annots") or []:
        a = ref.get_object()
        parent = a.get("/Parent")
        owner = parent.get_object() if parent is not None and "/T" not in a else a
        name = owner.get("/T")
        if name not in buttons:
            continue
        want = NameObject(buttons[name])
        states = list((a.get("/AP") or {}).get("/N", {}).keys())
        a[NameObject("/AS")] = want if want in states else NameObject("/Off")
        owner[NameObject("/V")] = want


def _sign(page, field: str, a: dict) -> None:
    """Draw the signature into the signature field's box on this page (if the field is on it)."""
    box = next((r.get_object()["/Rect"] for r in page.get("/Annots") or [] if r.get_object().get("/T") == field), None)
    if box is None:
        return
    x0, y0, x1, y1 = (float(v) for v in box)
    buf = io.BytesIO()
    mb = page.mediabox
    c = canvas.Canvas(buf, pagesize=(float(mb.width), float(mb.height)))
    if img := a.get("signature_image"):
        data = b64decode(img.split(",", 1)[-1])  # base64 PNG, optionally a data: URI
        c.drawImage(
            ImageReader(io.BytesIO(data)), x0, y0, x1 - x0, y1 - y0, mask="auto", preserveAspectRatio=True, anchor="sw"
        )
    else:
        if "Hand" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("Hand", str(HAND)))
        name = " ".join(x for x in (a.get("first"), a.get("last")) if x)
        size = min((y1 - y0) * 0.6, 16.0)
        while size > 6 and pdfmetrics.stringWidth(name, "Hand", size) > x1 - x0 - 4:
            size -= 0.5
        c.setFont("Hand", size)
        c.drawString(x0 + 2, y0 + (y1 - y0) * 0.25, name)
    c.save()
    page.merge_page(PdfReader(buf).pages[0])


def fill(pdf: bytes, a: dict) -> bytes | None:
    """The PDF with the applicant's answers filled in (still editable), or None if it is not a known form."""
    form = detect(pdf)
    if form is None:
        return None
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf)))
    values = form.values(a)
    signing = form.sign and a.get("sign", True)
    if signing:
        values[form.sign[1]] = datetime.now(NY).strftime("%m/%d/%Y")
    values |= {k: str(v) for k, v in (a.get("fields") or {}).items()}  # raw overrides last
    fields = w.get_fields() or {}
    text = {k: v for k, v in values.items() if k in fields and fields[k].get("/FT") != "/Btn"}
    for page in w.pages:
        w.update_page_form_field_values(page, text, auto_regenerate=False)
        _press(page, {k: v for k, v in values.items() if k in fields and fields[k].get("/FT") == "/Btn"})
        if signing:
            _sign(page, form.sign[0], a)
    w.set_need_appearances_writer(True)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()
