import pytest
from fastapi.testclient import TestClient

from housing_watch.web.app import create_app
from seed import seed, seed_jobs

HX = {"HX-Request": "true"}


@pytest.fixture
def client(pg):
    seed(pg)
    return TestClient(create_app(pg))


def test_pages_render(client):
    for path, text in [
        ("/", "Eligible &amp; open now"),
        ("/listings", "a 1"),
        ("/listings?verdict=excluded&borough=Bronx&sort=-rent&q=a&size=studio&max_rent=5000&since=7d", "a 2"),
        ("/listings?max_rent=junk", "a 1"),
        ("/listings?open=a%3A1", "Apply by mail"),
        ("/listings?open=missing", "Listing not found"),
        ("/sources", "TimeoutError: boom"),
        ("/events", "source failed"),
        ("/events?type=new&source=a&baseline=0", "a 1"),
    ]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert text in r.text, path
    assert "/static/htmx.min.js" in r.text
    assert client.get("/static/htmx.min.js").status_code == 200


def test_htmx_fragments(client):
    r = client.get("/listings?verdict=eligible", headers=HX)
    assert r.status_code == 200 and "<html" not in r.text and 'id="vc-eligible"' in r.text and "a 1" in r.text
    d = client.get("/listings/drawer?key=a%3A1", headers=HX)
    assert "<html" not in d.text and "82,557" in d.text and "https://a.example/1" in d.text
    assert (
        client.get("/listings/drawer?key=a%3A1", follow_redirects=False).headers["location"] == "/listings?open=a%3A1"
    )
    e = client.get("/events?before=999999", headers=HX)
    assert e.text.lstrip().startswith("<tr")


def test_empty_store(pg):
    r = TestClient(create_app(pg)).get("/listings")
    assert r.status_code == 200 and "No listings yet" in r.text
    assert "Nothing pending" in TestClient(create_app(pg)).get("/").text


def test_fleet_page(pg, monkeypatch):
    seed(pg)
    seed_jobs(pg)
    r = TestClient(create_app(pg)).get("/fleet")
    assert r.status_code == 200 and "source_run a" in r.text and "hetzner" in r.text


def test_lists_scroll_in_batches(pg):
    from sqlalchemy.orm import Session

    from seed import L, add_listing, event

    with Session(pg) as s, s.begin():
        for i in range(250):
            add_listing(s, L("bulk", f"{i:03}", "Queens"))
            event(s, f"bulk:{i:03}", "new")
    c = TestClient(create_app(pg))
    first = c.get("/listings?sort=title")
    assert "<strong>250</strong> listings" in first.text and first.text.count('class="click') == 200
    assert 'hx-get="/listings?sort=title&amp;offset=200"' in first.text and 'hx-trigger="intersect once"' in first.text
    assert "page=" not in first.text
    rest = c.get("/listings?sort=title&offset=200", headers=HX)
    assert "<table" not in rest.text and rest.text.count('class="click') == 50
    assert "All 250 listings shown" in rest.text and "intersect" not in rest.text
    ev = c.get("/events")
    assert "<strong>250</strong> events" in ev.text and 'hx-trigger="intersect once"' in ev.text
    older = c.get(ev.text.split('class="more-row" hx-get="')[1].split('"')[0].replace("&amp;", "&"), headers=HX)
    assert older.text.count("<tr>") == 50 and "No older events" in older.text


def test_fleet_page_without_queue(pg, monkeypatch):
    from housing_watch import queries

    monkeypatch.setattr(queries, "JOBS_TABLE", "procrastinate_missing")
    assert "set up yet" in TestClient(create_app(pg)).get("/fleet").text
