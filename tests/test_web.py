import pytest
from fastapi.testclient import TestClient

from housing_watch.web.app import create_app
from seed import procrastinate_engine, seed

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
        ("/listings?max_rent=junk&page=99", "a 1"),
        ("/listings?open=a%3A1", "Apply by mail"),
        ("/listings?open=missing", "Listing not found"),
        ("/sources", "TimeoutError: boom"),
        ("/fleet", "set up yet"),
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


def test_fleet_page(pg, pg_url):
    seed(pg)
    eng = procrastinate_engine(pg_url)
    try:
        r = TestClient(create_app(eng)).get("/fleet")
    finally:
        eng.dispose()
    assert r.status_code == 200 and "source_run a" in r.text and "hetzner" in r.text
