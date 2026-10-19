from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from housing_watch import applications, tasks
from housing_watch.web.app import create_app
from seed import L, add_listing, seed, seed_jobs

HX = {"HX-Request": "true"}
RESULTS = {**HX, "HX-Target": "results"}
SAME = {"origin": "http://testserver"}


@pytest.fixture
def client(pg):
    seed(pg)
    return TestClient(create_app(pg))


def test_pages_render(client):
    for path, text in [
        ("/", "Eligible &amp; open now"),
        ("/listings", "a 1"),
        (
            "/listings?f=verdict.in.excluded&f=borough.in.Bronx&sort=-rent&q=a&f=units.size.in.studio"
            "&f=units.rent.le.5000&f=last_changed.last.7d&cols=rent,source",
            "a 2",
        ),
        ("/listings?f=units.rent.le.junk", "is not a number"),
        ("/listings?f=nope.in.x", "Unknown filter field"),
        ("/listings?open=a%3A1", "Apply by mail"),
        ("/listings?open=missing", "Listing not found"),
        ("/sources", "TimeoutError: boom"),
        ("/events", "source failed"),
        ("/events?f=type.in.new&f=source.in.a&f=first_run.is.no", "a 1"),
        ("/events?f=listing.borough.in.Queens&f=verdict.in.eligible&cols=source", "a 1"),
    ]:
        r = client.get(path)
        assert r.status_code == 200, path
        assert text in r.text, path
    assert "/static/htmx.min.js" in r.text
    assert client.get("/static/htmx.min.js").status_code == 200


def test_htmx_fragments(client):
    r = client.get("/listings?f=verdict.in.eligible", headers=RESULTS)
    assert r.status_code == 200 and "<html" not in r.text and "a 1" in r.text and "a 2" not in r.text
    assert 'id="chips" class="chips" hx-swap-oob="true"' in r.text and "Verdict is eligible" in r.text
    assert 'id="navlist"' in r.text and 'id="viewbar"' in r.text  # chips, view bar and nav refresh with the list
    d = client.get("/listings/drawer?key=a%3A1", headers=HX)
    assert "<html" not in d.text and "82,557" in d.text and "https://a.example/1" in d.text
    assert (
        client.get("/listings/drawer?key=a%3A1", follow_redirects=False).headers["location"] == "/listings?open=a%3A1"
    )
    e = client.get("/events?before=999999", headers={**HX, "HX-Target": ""})
    assert e.text.lstrip().startswith("<tr")


def test_empty_store(pg):
    r = TestClient(create_app(pg)).get("/listings")
    assert r.status_code == 200 and "No listings yet" in r.text
    assert "Nothing pending" in TestClient(create_app(pg)).get("/").text


def test_fleet_page(pg, monkeypatch):
    seed(pg)
    seed_jobs(pg)
    tasks.beat(pg, "gcp-vps", 2, ["sources"], datetime.now(UTC))
    r = TestClient(create_app(pg)).get("/fleet")
    assert r.status_code == 200 and "gcp-vps" in r.text and "2 slots" in r.text and "hetzner" in r.text


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
    assert "?page=" not in first.text and "&amp;page=" not in first.text
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


def test_filter_builder(client):
    pick = client.get("/filters/builder?page=listings")
    assert "Income band" in pick.text and 'data-pick="units.rent"' in pick.text and 'data-pick="verdict"' in pick.text
    b = client.get("/filters/builder?page=listings&field=borough&f=verdict.in.eligible")
    assert 'value="Queens"' in b.text and 'value="Bronx"' not in b.text  # counts under the other filters
    num = client.get("/filters/builder?page=listings&field=units.rent&f=units.rent.le.3000&edit=0")
    assert 'value="3000"' in num.text and "data-remove" in num.text and '<option value="le" selected' in num.text
    t = client.get("/filters/builder?page=listings&field=first_seen")
    assert 'data-ops="last"' in t.text and 'type="date"' in t.text
    ev = client.get("/filters/builder?page=events&field=type")
    assert 'value="source_failed"' in ev.text
    assert client.get("/filters/builder?page=nope").status_code == 404


def test_saved_views(client):
    assert 'class="views"' not in client.get("/listings").text  # nothing seeded
    r = client.post("/views", data={"page": "listings", "name": "Queens", "default": "1",
                                    "back": "/listings?f=borough.in.Queens&sort=-rent"}, headers=SAME)  # fmt: skip
    assert r.status_code == 204 and r.headers["HX-Location"].startswith(
        "/listings?f=borough.in.Queens&sort=-rent&view="
    )
    vid = r.headers["HX-Location"].rsplit("=", 1)[1]
    page = client.get("/listings")  # the default view opens on a bare URL
    assert "Queens</span>" in page.text and "Borough is Queens" in page.text and "a 2" not in page.text
    assert f'data-canonical="/listings?f=borough.in.Queens&amp;sort=-rent&amp;view={vid}"' in page.text
    drift = client.get(f"/listings?f=borough.in.Queens&f=verdict.in.eligible&sort=-rent&view={vid}")
    assert "mod-dot" in drift.text and "Save changes" in drift.text
    assert "mod-dot" not in client.get(f"/listings?view={vid}").text
    patch = client.patch(f"/views/{vid}", data={"save": "1", "back": f"/listings?f=verdict.in.eligible&view={vid}"},
                         headers=SAME)  # fmt: skip
    assert patch.status_code == 204 and "Verdict is eligible" in client.get(f"/listings?view={vid}").text
    assert client.patch(f"/views/{vid}", data={"name": "Renamed"}, headers=SAME).status_code == 204
    assert "Renamed" in client.get("/listings").text
    dup = client.post(f"/views/{vid}/duplicate", headers=SAME)
    assert dup.status_code == 204 and "Renamed (copy)" in client.get("/listings").text
    assert client.post("/views", data={"page": "listings", "name": ""}, headers=SAME).status_code == 400
    assert client.delete(f"/views/{vid}", headers=SAME).status_code == 204
    assert client.delete(f"/views/{vid}", headers=SAME).status_code == 404


def test_view_writes_need_same_origin(client):
    data = {"page": "listings", "name": "X"}
    assert client.post("/views", data=data).status_code == 403
    assert client.post("/views", data=data, headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/views", data=data, headers={**SAME, "sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/views", data=data, headers={"referer": "http://testserver/listings"}).status_code == 204


def test_stale_view_warns(client, pg):
    from sqlalchemy import update
    from sqlalchemy.orm import Session

    from housing_watch.orm import SavedViewRow

    r = client.post("/views", data={"page": "listings", "name": "Old", "back": "/listings?f=borough.in.Queens"},
                    headers=SAME)  # fmt: skip
    vid = int(r.headers["HX-Location"].rsplit("=", 1)[1])
    with Session(pg) as s, s.begin():
        s.execute(update(SavedViewRow).where(SavedViewRow.id == vid).values(
            query={"filters": [{"field": "pets_ok", "op": "is", "values": ["yes"]}]}))  # fmt: skip
    page = client.get(f"/listings?view={vid}")
    assert page.status_code == 200 and "Unknown filter field “pets_ok”" in page.text and "dropped" in page.text


def test_application_prefill(pg, monkeypatch, tmp_path):
    blank = (Path(__file__).parent / "fixtures" / "applications" / "hc2.pdf").read_bytes()
    monkeypatch.setattr(applications, "blank", lambda url: blank if url.endswith("g1.pdf") else b"%PDF flat")
    with Session(pg) as s:
        notes = "Mail only. Application: https://x.example/g1.pdf; flyer https://x.example/flyer.pdf"
        add_listing(s, L("m", "1", "Queens", notes=notes))
        s.commit()
    c = TestClient(create_app(pg))
    assert "/listings/application/panel?key=m%3A1" in c.get("/listings/drawer?key=m%3A1", headers=HX).text
    p = c.get("/listings/application/panel?key=m%3A1")
    assert "HC2.0" in p.text and "not a fillable form" in p.text and "set HW_APPLICANT_FILE" in p.text
    assert c.get("/listings/application?key=m%3A1").status_code == 404  # no applicant file yet
    f = tmp_path / "applicant.json"
    f.write_text((Path(__file__).parent / "fixtures" / "applications" / "applicant.example.json").read_text())
    monkeypatch.setattr(applications, "APPLICANT_FILE", str(f))
    assert "Download filled" in c.get("/listings/application/panel?key=m%3A1").text
    r = c.get("/listings/application?key=m%3A1&n=0")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"
    assert "no-store" in r.headers["cache-control"] and "application-1.pdf" in r.headers["content-disposition"]
    assert c.get("/listings/application?key=m%3A1&n=1").status_code == 422
    assert c.get("/listings/application?key=m%3A1&n=5").status_code == 404
