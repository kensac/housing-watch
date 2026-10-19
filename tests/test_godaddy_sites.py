"""Offline tests for the GoDaddy Website Builder watchers (cgmr, larris, your_neighborhood_housing,
infinite_horizons, riverton) and the shared helpers in adapters/_pagekit.py."""

from pathlib import Path

from housing_watch import db
from housing_watch.adapters._pagekit import snapshot, wsimg_images
from housing_watch.adapters.cgmr import Cgmr
from housing_watch.adapters.infinite_horizons import GALLERY, InfiniteHorizons, detail_pages
from housing_watch.adapters.larris import Larris
from housing_watch.adapters.riverton import Riverton
from housing_watch.adapters.your_neighborhood_housing import YourNeighborhoodHousing

F = Path(__file__).parent / "fixtures"


def snap(adapter, url, path):
    body = (F / path).read_text()
    return snapshot(adapter, url, body, extra_links=wsimg_images(body))


def test_cgmr_empty_page_keeps_content_and_drops_footer():
    s = snap(Cgmr(), Cgmr.url, "cgmr/page.html")
    assert "Housing Opportunities" in s.text
    assert not any(x in s.text for x in ("Copyright", "Powered by", "All Rights Reserved"))
    assert len(s.text) >= Cgmr.min_text
    assert all("/:/" not in u and "godaddy.com" not in u for u in s.links)  # resize suffix + footer link gone


def test_cgmr_new_listing_is_a_change():
    a = Cgmr()
    body = (F / "cgmr/page.html").read_text()
    s1 = snapshot(a, a.url, body, extra_links=wsimg_images(body))
    body2 = body.replace(
        "</body>",
        '<div><p>123 Example Ave, Bronx NY - Studio $1,900 - 80% AMI</p><a href="/flyer.pdf">Flyer</a></div></body>',
    )
    s2 = snapshot(a, a.url, body2, extra_links=wsimg_images(body2))
    c = db.connect(":memory:")
    assert db.record_snapshots(c, "cgmr", [s1], now=1) == 1
    assert db.record_snapshots(c, "cgmr", [s1], now=2) == 0
    assert db.record_snapshots(c, "cgmr", [s2], now=3) == 1
    detail = c.execute("SELECT detail FROM events WHERE type='page_changed'").fetchone()["detail"]
    assert "123 Example Ave" in detail and "flyer.pdf" in detail


def test_rendered_and_plain_godaddy_pages_snapshot_identically():
    # byparr adds GoDaddy's rotating "Airo" promo; it must not read as a page change
    a = InfiniteHorizons()
    plain = snap(a, GALLERY, "infinite_horizons/photo-gallery.html")
    rendered = snap(a, GALLERY, "infinite_horizons/photo-gallery-rendered.html")
    assert "Airo" in (F / "infinite_horizons/photo-gallery-rendered.html").read_text()
    assert plain.text == rendered.text and plain.links == rendered.links


def test_infinite_horizons_gallery_detail_pages_and_flyers():
    body = (F / "infinite_horizons/photo-gallery.html").read_text()
    assert detail_pages(body) == [f"https://ihrerentals.com/house-{x}" for x in ("1", "1a", "2a")]
    s = snap(InfiniteHorizons(), GALLERY, "infinite_horizons/photo-gallery.html")
    assert "545e 166th st Bronx, NY 10456" in s.text and "Virginia House Senior Residences" in s.text
    flyers = [u for u in s.links if "img1.wsimg.com" in u]
    assert flyers and all(u.startswith("https://img1.wsimg.com/isteam/ip/") and "/:/" not in u for u in flyers)
    assert not any("transparent_placeholder" in u for u in flyers)


def test_ynh_business_hours_widget_is_ignored():
    a = YourNeighborhoodHousing()
    body = (F / "your_neighborhood_housing/home.html").read_text()
    weekday = snapshot(a, a.url, body)
    weekend = snapshot(
        a, a.url, body.replace(">Open today<", ">Closed today<").replace("09:00 am – 05:00 pm", "Closed")
    )
    assert weekday.text == weekend.text
    assert "3531 Bruckner Boulevard" in weekday.text
    assert "Open today" not in weekday.text and "We use cookies" not in weekday.text


def test_larris_boilerplate_page():
    s = snap(Larris(), Larris.url, "larris/re-rentals.html")
    assert "Available Re-Rentals" in s.text and len(s.text) >= Larris.min_text
    assert "This website uses cookies." not in s.text and "Accept" not in s.text.splitlines()


def test_riverton_watches_lottery_site_and_placeholder():
    r = Riverton()
    lot = snap(r, "https://rivertonlottery.com/", "riverton/rivertonlottery.html")
    assert "Riverton Square Lottery" in lot.text
    assert any(u.endswith("Riverton-Apartments-Waitlist-Advertisement-FINAL-8.9.1855-1.pdf") for u in lot.links)
    ph = snap(r, "https://rivertonsquare.com/", "riverton/rivertonsquare.html")
    assert "Launching Soon" in ph.text and "Copyright" not in ph.text
