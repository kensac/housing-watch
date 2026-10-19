import shutil
from pathlib import Path

import pytest

from housing_watch.adapters import rockrose
from housing_watch.adapters.rockrose import Rockrose, units_from_chart
from housing_watch.criteria import listing_verdict, unit_verdict
from housing_watch.models import Band

F = Path(__file__).parent / "fixtures" / "rockrose"
U = "https://rockrose.com/wp-content/uploads/"
PAGES = {
    rockrose.INDEX: "index.html",
    "https://rockrose.com/affordable-availabilities/the-cove-waitlist/": "the-cove-waitlist.html",
    "https://rockrose.com/affordable-availabilities/lyra-waiting-list/": "lyra-waiting-list.html",
}
FILES = {  # file url -> fixture holding what tesseract / pdftotext made of it
    U + "2026/09/Cove-Max-rents-1.png": "Cove-Max-rents-1.ocr.txt",
    U + "2026/09/130ami.png": "130ami.ocr.txt",
    U + "2026/09/70-ami-4.png": "70-ami-4.ocr.txt",
    U + "2026/08/BuildingLink-NEW.png": "BuildingLink-NEW.ocr.txt",  # a logo in Lyra's slider: no caps
    U + "2026/01/43-25-Hunter-Affordable-Application-and-Privacy-Policy-Updated-with-4.1.2025-Income-Limits_FORM.pdf": (
        "hayden.pdf.txt"
    ),
    U + "2026/01/Tribeca-Pointe-Application-and-Privacy-Policy-Updated-with-04.01.2025-Income-Limits-FORM.pdf": (
        "tribeca-pointe.pdf.txt"
    ),
}


class _Resp:
    def __init__(self, content):
        self.content = content


@pytest.fixture
def offline(monkeypatch):
    calls = {"make": 0}

    def make(data):
        calls["make"] += 1
        return data.decode()

    monkeypatch.setattr(rockrose.fetch, "html", lambda url, **kw: (F / PAGES[url]).read_text())
    monkeypatch.setattr(rockrose.fetch, "get", lambda url, **kw: _Resp((F / FILES[url]).read_bytes()))
    monkeypatch.setattr(rockrose, "tesseract", make)
    monkeypatch.setattr(rockrose, "pdftotext", make)
    return calls


def test_fetch(offline):
    res = Rockrose().fetch()
    by = {lst.ext_id: lst for lst in res.listings}
    assert set(by) == {"the-cove-waitlist", "lyra-waiting-list", "hayden", "tribeca-pointe"}
    assert {k: (v.borough, v.status, v.kind) for k, v in by.items()} == {
        "the-cove-waitlist": ("Queens", "waitlist", "waitlist"),
        "lyra-waiting-list": ("Manhattan", "waitlist", "waitlist"),
        "hayden": ("Queens", "waitlist", "waitlist"),
        "tribeca-pointe": ("Manhattan", "waitlist", "waitlist"),
    }

    cove = by["the-cove-waitlist"]
    assert cove.address == "43-12 Hunter Street, Long Island City"
    assert [(u.size, u.ami, u.rent) for u in cove.units] == [
        ("studio", 130, None),
        ("1br", 130, None),
        ("2br", 130, None),
    ]
    assert cove.units[1].bands == [Band(1, None, 154440), Band(2, None, 176410), Band(3, None, 198510)]
    assert [b.income_max for b in cove.units[2].bands] == [176410, 198510, 220480, 238160]
    assert "covererental@housingpartnership.com" in cove.notes and "Attachment-G-1.pdf" in cove.notes
    # minimum income is "TBD": possible, not eligible, and the notes say why
    assert listing_verdict(cove).verdict == "possible"
    assert "minimum incomes are not published (charts say TBD)" in cove.notes

    lyra = by["lyra-waiting-list"]
    assert [(u.size, u.ami) for u in lyra.units] == [
        ("studio", 70),
        ("1br", 70),
        ("2br", 70),
        ("studio", 130),
        ("1br", 130),
        ("2br", 130),
    ]
    assert unit_verdict(lyra.units[0]) == ("excluded", "over income (max $83,160)")
    assert unit_verdict(lyra.units[3])[0] == "possible"
    assert "lyrarerental@housingpartnership.com" in lyra.notes
    assert listing_verdict(lyra).verdict == "possible"

    hayden, tribeca = by["hayden"], by["tribeca-pointe"]
    assert hayden.units[0].ami == 60 and tribeca.units[0].ami == 40
    assert "15 EAST 26TH STREET" in hayden.notes  # mailing address, not the building's borough
    assert listing_verdict(hayden).verdict == listing_verdict(tribeca).verdict == "excluded"


def test_cache_skips_unchanged_files(offline):
    cache: dict = {}
    Rockrose(cache).fetch()
    first = offline["make"]
    assert first == 6 and len(cache["ocr"]) == 4 and len(cache["pdf"]) == 2
    Rockrose(cache).fetch()
    assert offline["make"] == first  # same content hash: no OCR / pdftotext rerun


def test_chart_tolerates_ocr_misread():
    ocr = (F / "130ami.ocr.txt").read_text().replace("$198,510", "$I98,5I0", 1)
    units = units_from_chart(ocr)
    # the 1BR's unreadable 3-person cap makes it, and the 2BR after it, unknown rather than a guessed studio
    assert [u.size for u in units] == ["studio", "unknown", "unknown"]
    assert [b.household for b in units[1].bands] == [1, 2]
    assert unit_verdict(units[1]) == ("possible", "income fits, size unknown")


@pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")
def test_real_ocr_of_charts():
    for png, ami in (("Cove-Max-rents-1.png", 130), ("130ami.png", 130), ("70-ami-4.png", 70)):
        units = units_from_chart(rockrose.tesseract((F / png).read_bytes()))
        assert [u.size for u in units] == ["studio", "1br", "2br"]
        assert {u.ami for u in units} == {ami}
        assert sum(len(u.bands) for u in units) == 9
