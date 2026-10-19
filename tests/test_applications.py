import base64
import io
import json
import re
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

from housing_watch import applications as A

FX = Path(__file__).parent / "fixtures" / "applications"
BLANK = (FX / "hc2.pdf").read_bytes()
ME = json.loads((FX / "applicant.example.json").read_text())


def values(pdf: bytes) -> dict:
    return {k: v.get("/V") for k, v in PdfReader(io.BytesIO(pdf)).get_fields().items()}


def test_pdf_urls():
    notes = (
        "Apply: mail to X; application PDF https://iafford.s3.amazonaws.com/p/Attachment_G-1.pdf; "
        "Application: https://drive.google.com/uc?export=download&id=1wyDfT-x_9 and again "
        "https://iafford.s3.amazonaws.com/p/Attachment_G-1.pdf"
    )
    assert A.pdf_urls(notes) == [
        "https://iafford.s3.amazonaws.com/p/Attachment_G-1.pdf",
        "https://drive.google.com/uc?export=download&id=1wyDfT-x_9",
    ]
    assert A.pdf_urls(None) == [] and A.pdf_urls("see https://example.com/apply") == []


def test_detect():
    assert A.detect(BLANK).name.startswith("HC2.0")
    assert A.detect(b"not a pdf") is None and A.fill(b"not a pdf", ME) is None


def test_fill_hc2():
    v = values(A.fill(BLANK, ME))
    assert v["First Name"] == "Alex" and v["Last Name"] == "Example" and v["Zip"] == "10001"
    assert v["First Middle Initial  Last Name SuffixRow1"] == "Alex Q Example"
    assert v["Birth Date MMDDYYSelf"] == "01/02/95" and v["English"] == "/On"
    assert v["Employer Name  AddressSelf"].startswith("Example Corp") and v["Text13"] == "3"
    assert v["Annual IncomeSelf"] == "150000" and v["Amount Paid Self"] == "2885"
    assert v["Add together the total annual income amounts from 1A and 2A above"] == "150,000"
    assert v["Check Box2"] == "/Yes" and v["Check Box1"] is None  # NYCHA: No
    assert v["undefined_7"] == "/No" and v["undefined_15"] == "/Yes_3" and v["No_4"] == "/On"
    assert not v.get("SSNTIN OptionalRow1") and re.fullmatch(r"\d\d/\d\d/\d{4}", v["Date"]) and not v.get("Date_2")


def test_radio_widgets_show_the_choice():
    r = PdfReader(io.BytesIO(A.fill(BLANK, ME)))
    states = {}
    for page in r.pages:
        for a in page.get("/Annots") or []:
            a = a.get_object()
            if a.get("/Parent") is not None and a["/Parent"].get("/T") == "undefined_7":
                states[a["/AS"]] = 1
    assert set(states) == {"/No", "/Off"}


def test_overrides_and_missing_file(tmp_path):
    v = values(A.fill(BLANK, ME | {"fields": {"Work Phone": "212-555-0199"}}))
    assert v["Work Phone"] == "212-555-0199"
    assert A.applicant(str(tmp_path / "nope.json")) is None
    (tmp_path / "a.json").write_text(json.dumps(ME))
    assert A.applicant(str(tmp_path / "a.json"))["first"] == "Alex"


def _page10_text(pdf: bytes) -> str:
    return PdfReader(io.BytesIO(pdf)).pages[9].extract_text()


def test_signing():
    signed = A.fill(BLANK, ME)
    assert "Alex Example" in _page10_text(signed)  # typed in the handwriting font
    unsigned = A.fill(BLANK, ME | {"sign": False})
    assert "Alex Example" not in _page10_text(unsigned) and not values(unsigned).get("Date")
    png = io.BytesIO()
    Image.new("RGBA", (300, 60), (0, 0, 0, 0)).save(png, "PNG")
    img = A.fill(BLANK, ME | {"signature_image": "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()})
    page = PdfReader(io.BytesIO(img)).pages[9]
    assert "Alex Example" not in page.extract_text() and page.images  # the image, not the typed name
