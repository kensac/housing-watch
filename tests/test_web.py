from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from housing_watch import applications, queries
from housing_watch.orm import SavedViewRow
from housing_watch.web.app import create_app
from seed import L, add_listing, event, seed, seed_jobs

SAME = {"origin": "http://testserver"}
FX = Path(__file__).parent / "fixtures" / "applications"


@pytest.fixture
def client(pg):
    seed(pg)
    return TestClient(create_app(pg))


def titles(r) -> list[str]:
    return [x["title"] for x in r.json()["items"]]


def test_meta(client):
    m = client.get("/api/meta").json()
    fields = {f["name"]: f for f in m["pages"]["listings"]["fields"]}
    assert fields["units.rent"]["type"] == "number" and fields["units.rent"]["money"]
    assert (
        fields["verdict"]["choices"] == ["eligible", "possible", "excluded"] and "between" in fields["units.ami"]["ops"]
    )
    assert "listing.borough" in {f["name"] for f in m["pages"]["events"]["fields"]}
    assert m["pages"]["units"]["sorts"]["rent"] == "Rent" and m["op_labels"]["le"] == "≤"


def test_listings(client):
    r = client.get("/api/listings")
    assert r.status_code == 200 and titles(r)[0] == "a 1" and "b 3" not in titles(r)  # gone hidden
    x = r.json()["items"][0]
    assert x["verdict"] == "eligible" and x["sizes"] == ["studio"] and "haystack" not in x
    q = "/api/listings?f=verdict.in.excluded&f=borough.in.Bronx&sort=-rent&q=a&f=units.size.in.studio"
    r = client.get(q + "&f=units.rent.le.5000&f=last_changed.last.7d").json()
    assert [x["title"] for x in r["items"]] == ["a 2"] and r["filters"][1]["label"] == "Borough is Bronx"
    assert client.get("/api/listings?f=units.rent.le.junk").json()["problems"] == ["Rent: “junk” is not a number"]
    assert "Unknown filter field" in client.get("/api/listings?f=nope.in.x").json()["problems"][0]


def test_listing_detail(client):
    d = client.get("/api/listing?key=a%3A1").json()
    assert d["summary"]["title"] == "a 1" and d["notes"] == "Apply by mail" and d["units"][0]["bands"][0][1] == 82557
    assert d["events"][0]["type"] == "new" and d["pdfs"] == []
    assert client.get("/api/listing?key=missing").status_code == 404


def test_values(client):
    v = client.get("/api/values?page=listings&field=borough&f=verdict.in.eligible").json()
    assert v["enum"] and {c["value"] for c in v["counts"] if c["count"]} == {"Queens"}
    assert not client.get("/api/values?page=listings&field=units.rent").json()["enum"]
    ev = client.get("/api/values?page=events&field=type").json()
    assert "source_failed" in {c["value"] for c in ev["counts"]}
    assert client.get("/api/values?page=nope&field=x").status_code == 404


def test_events_overview_sources(client):
    e = client.get("/api/events").json()
    assert e["total"] == 6 and e["items"][0]["type"] == "source_failed" and e["next_before"] is None
    e = client.get("/api/events?f=listing.borough.in.Queens&f=verdict.in.eligible").json()
    assert e["problems"] == [] and [x["key"] for x in e["items"]] == ["b:3", "a:1"]  # removed listings included
    o = client.get("/api/overview").json()
    assert o["eligible_open"] == 1 and o["eligible_now"][0]["title"] == "a 1" and o["failing"] == ["b"]
    assert o["digest"]["events"] >= 1
    s = {x["source"]: x for x in client.get("/api/sources").json()}
    assert s["b"]["health"] == "failing" and s["b"]["last_error"] == "TimeoutError: boom"


def test_lists_scroll_in_batches(pg):
    with Session(pg) as s, s.begin():
        for i in range(250):
            add_listing(s, L("bulk", f"{i:03}", "Queens"))
            event(s, f"bulk:{i:03}", "new")
    c = TestClient(create_app(pg))
    first = c.get("/api/listings?sort=title").json()
    assert first["total"] == 250 and len(first["items"]) == 200 and first["next_offset"] == 200
    rest = c.get("/api/listings?sort=title&offset=200").json()
    assert len(rest["items"]) == 50 and rest["next_offset"] is None
    ev = c.get("/api/events").json()
    assert ev["total"] == 250 and len(ev["items"]) == 200
    older = c.get(f"/api/events?before={ev['next_before']}").json()
    assert len(older["items"]) == 50 and older["next_before"] is None


def test_fleet(pg, monkeypatch):
    seed(pg)
    seed_jobs(pg)
    f = TestClient(create_app(pg)).get("/api/fleet").json()
    assert f["available"] and f["queues"]["sources"]["failed"] == 1 and f["hosts"]
    monkeypatch.setattr(queries, "JOBS_TABLE", "procrastinate_missing")
    assert TestClient(create_app(pg)).get("/api/fleet").json()["available"] is False


def test_saved_views(client):
    assert client.get("/api/views").json() == []  # nothing seeded
    body = {"page": "listings", "name": "Queens", "is_default": True,
            "state": {"f": ["borough.in.Queens"], "sort": "-rent"}}  # fmt: skip
    v = client.post("/api/views", json=body, headers=SAME).json()
    assert v["state"] == {"f": ["borough.in.Queens"], "q": "", "sort": "-rent", "columns": v["state"]["columns"]}
    assert v["is_default"] and client.get("/api/views").json()[0]["count"] == 1
    vid = v["id"]
    p = client.patch(
        f"/api/views/{vid}", json={"state": {"f": ["verdict.in.eligible"]}, "name": "Renamed"}, headers=SAME
    )
    assert p.json()["name"] == "Renamed" and p.json()["state"]["f"] == ["verdict.in.eligible"]
    assert client.post(f"/api/views/{vid}/duplicate", headers=SAME).json()["name"] == "Renamed (copy)"
    assert client.patch(f"/api/views/{vid}", json={"move": 1}, headers=SAME).json()["position"] == 1
    bad = {"page": "listings", "name": "x", "state": {"f": ["nope.in.x"]}}
    assert client.post("/api/views", json=bad, headers=SAME).status_code == 400
    assert client.post("/api/views", json={"page": "listings", "name": ""}, headers=SAME).status_code == 400
    assert client.delete(f"/api/views/{vid}", headers=SAME).status_code == 204
    assert client.delete(f"/api/views/{vid}", headers=SAME).status_code == 404


def test_view_writes_need_same_origin(client):
    body = {"page": "listings", "name": "X"}
    assert client.post("/api/views", json=body).status_code == 403
    assert client.post("/api/views", json=body, headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/views", json=body, headers={**SAME, "sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/api/views", json=body, headers={"referer": "http://testserver/listings"}).status_code == 200


def test_stale_view_warns(client, pg):
    vid = client.post("/api/views", json={"page": "listings", "name": "Old"}, headers=SAME).json()["id"]
    with Session(pg) as s, s.begin():
        s.execute(update(SavedViewRow).where(SavedViewRow.id == vid).values(
            query={"filters": [{"field": "pets_ok", "op": "is", "values": ["yes"]}]}))  # fmt: skip
    (v,) = client.get("/api/views").json()
    assert "Unknown filter field “pets_ok”" in v["warnings"][0] and v["state"]["f"] == []


def test_application_prefill(pg, monkeypatch, tmp_path):
    blank = (FX / "hc2.pdf").read_bytes()
    monkeypatch.setattr(applications, "blank", lambda url: blank if url.endswith("g1.pdf") else b"%PDF flat")
    with Session(pg) as s, s.begin():
        add_listing(
            s, L("m", "1", "Queens", notes="Mail only. Application: https://x.example/g1.pdf; https://x.example/f.pdf")
        )
    c = TestClient(create_app(pg))
    assert c.get("/api/listing?key=m%3A1").json()["pdfs"] == ["https://x.example/g1.pdf", "https://x.example/f.pdf"]
    p = c.get("/api/listing/applications?key=m%3A1").json()
    assert not p["ready"] and [x["form"] for x in p["pdfs"]] == ["HC2.0 Affordable Housing Application", None]
    assert c.get("/api/listing/application?key=m%3A1").status_code == 404  # no applicant file yet
    f = tmp_path / "applicant.json"
    f.write_text((FX / "applicant.example.json").read_text())
    monkeypatch.setattr(applications, "APPLICANT_FILE", str(f))
    assert c.get("/api/listing/applications?key=m%3A1").json()["ready"]
    r = c.get("/api/listing/application?key=m%3A1&n=0")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"
    assert "no-store" in r.headers["cache-control"] and "application-1.pdf" in r.headers["content-disposition"]
    assert c.get("/api/listing/application?key=m%3A1&n=1").status_code == 422
    assert c.get("/api/listing/application?key=m%3A1&n=5").status_code == 404


def test_spa(pg, tmp_path):
    assert TestClient(create_app(pg, dist=tmp_path)).get("/listings").status_code == 503
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id=root></div>")
    (tmp_path / "assets" / "app.js").write_text("x")
    c = TestClient(create_app(pg, dist=tmp_path))
    assert c.get("/listings?open=a%3A1").text == "<div id=root></div>" and c.get("/assets/app.js").text == "x"
    assert c.get("/api/nope").status_code == 404 and c.get("/../etc/passwd").status_code in (200, 404)


def test_units_api(client):
    r = client.get("/api/units?sort=rent&f=borough.in.Queens").json()
    assert r["total"] == 1 and r["items"][0]["unit_no"] == "4B" and r["items"][0]["income_min"] == 82557
    assert "haystack" not in r["items"][0] and r["verdict_counts"]["eligible"] == 1
    v = client.post("/api/views", json={"page": "units", "name": "Cheap", "state": {"f": ["units.rent.le.2500"]}},
                    headers=SAME).json()  # fmt: skip
    assert v["page"] == "units" and client.get("/api/views").json()[0]["count"] == 3  # a:1, a:2 and closed b:2
