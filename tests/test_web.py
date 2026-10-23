from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from housing_watch import applications, queries
from housing_watch.orm import SavedViewRow
from housing_watch.web.app import create_app
from seed import L, add_listing, event, gql, gql_error, seed, seed_jobs

SAME = {"origin": "http://testserver"}
FX = Path(__file__).parent / "fixtures" / "applications"
LISTINGS = """query($f: [String!], $q: String! = "", $sort: String! = "", $offset: Int! = 0) {
  listings(f: $f, q: $q, sort: $sort, offset: $offset) {
    items { key title verdict sizes tracker_match application_since } total next_offset
    filters { i label } problems hidden_gone verdict_counts { eligible }
  }
}"""
EVENTS = """query($f: [String!], $before: Int) {
  events(f: $f, before: $before) { items { key type } total next_before problems }
}"""
VIEW = "id page name position is_default warnings count state { f q sort columns }"
VIEWS = f"{{ views {{ {VIEW} }} }}"
CREATE = f"""mutation($page: String!, $name: String!, $is_default: Boolean! = false, $state: ViewStateInput) {{
  createView(page: $page, name: $name, is_default: $is_default, state: $state) {{ {VIEW} }}
}}"""


@pytest.fixture
def client(pg):
    seed(pg)
    return TestClient(create_app(pg))


def listings(c, **v) -> dict:
    return gql(c, LISTINGS, **v)["data"]["listings"]


def titles(r: dict) -> list[str]:
    return [x["title"] for x in r["items"]]


def test_meta(client):
    q = "{ meta { op_labels pages { listings { fields { name type money choices ops } } units { sorts }"
    m = gql(client, q + " events { fields { name } } } } }")["data"]["meta"]
    fields = {f["name"]: f for f in m["pages"]["listings"]["fields"]}
    assert fields["units.rent"]["type"] == "number" and fields["units.rent"]["money"]
    assert (
        fields["verdict"]["choices"] == ["eligible", "possible", "excluded"] and "between" in fields["units.ami"]["ops"]
    )
    assert "listing.borough" in {f["name"] for f in m["pages"]["events"]["fields"]}
    assert m["pages"]["units"]["sorts"]["rent"] == "Rent" and m["op_labels"]["le"] == "≤"


def test_listings(client):
    r = listings(client)
    assert titles(r)[0] == "a 1" and "b 3" not in titles(r)  # gone hidden
    x = r["items"][0]
    assert x["verdict"] == "eligible" and x["sizes"] == ["studio"]
    f = ["verdict.in.excluded", "borough.in.Bronx", "units.size.in.studio", "units.rent.le.5000"]
    r = listings(client, f=[*f, "last_changed.last.7d"], sort="-rent", q="a")
    assert titles(r) == ["a 2"] and r["filters"][1]["label"] == "Borough is Bronx"


def test_bad_filter_is_a_problem_not_an_error(client):
    res = gql(client, LISTINGS, f=["units.rent.le.junk"])
    assert "errors" not in res and res["data"]["listings"]["problems"] == ["Rent: “junk” is not a number"]
    assert "Unknown filter field" in listings(client, f=["nope.in.x"])["problems"][0]
    assert gql(client, EVENTS, f=["nope.in.x"])["data"]["events"]["problems"]


def test_listing_detail(client):
    q = """query($key: String!) { listing(key: $key) {
      summary { title } notes units { bands } events { type } pdfs } }"""
    d = gql(client, q, key="a:1")["data"]["listing"]
    assert d["summary"]["title"] == "a 1" and d["notes"] == "Apply by mail" and d["units"][0]["bands"][0][1] == 82557
    assert d["events"][0]["type"] == "new" and d["pdfs"] == []
    assert gql_error(gql(client, q, key="missing")) == "This listing is not in the store."


def test_values(client):
    q = """query($page: String!, $field: String!, $f: [String!]) {
      values(page: $page, field: $field, f: $f) { enum counts { value count } } }"""
    v = gql(client, q, page="listings", field="borough", f=["verdict.in.eligible"])["data"]["values"]
    assert v["enum"] and {c["value"] for c in v["counts"] if c["count"]} == {"Queens"}
    assert not gql(client, q, page="listings", field="units.rent")["data"]["values"]["enum"]
    ev = gql(client, q, page="events", field="type")["data"]["values"]
    assert "source_failed" in {c["value"] for c in ev["counts"]}
    assert gql_error(gql(client, q, page="nope", field="x")) == "Unknown field"


def test_events_overview_sources(client):
    e = gql(client, EVENTS)["data"]["events"]
    assert e["total"] == 6 and e["items"][0]["type"] == "source_failed" and e["next_before"] is None
    e = gql(client, EVENTS, f=["listing.borough.in.Queens", "verdict.in.eligible"])["data"]["events"]
    assert e["problems"] == [] and [x["key"] for x in e["items"]] == ["b:3", "a:1"]  # removed listings included
    o = gql(client, "{ overview { eligible_open failing eligible_now { title } digest { events } } }")["data"]
    o = o["overview"]
    assert o["eligible_open"] == 1 and o["eligible_now"][0]["title"] == "a 1" and o["failing"] == ["b"]
    assert o["digest"]["events"] >= 1
    s = {x["source"]: x for x in gql(client, "{ sources { source health last_error } }")["data"]["sources"]}
    assert s["b"]["health"] == "failing" and s["b"]["last_error"] == "TimeoutError: boom"


def test_get_queries_work(client):
    r = client.get("/graphql", params={"query": "{ overview { total } }"}).json()
    assert r["data"]["overview"]["total"] >= 1
    assert client.get("/graphql", params={"query": "mutation { deleteView(id: 1) }"}).status_code >= 400


def test_lists_scroll_in_batches(pg):
    with Session(pg) as s, s.begin():
        for i in range(250):
            add_listing(s, L("bulk", f"{i:03}", "Queens"))
            event(s, f"bulk:{i:03}", "new")
    c = TestClient(create_app(pg))
    first = listings(c, sort="title")
    assert first["total"] == 250 and len(first["items"]) == 200 and first["next_offset"] == 200
    rest = listings(c, sort="title", offset=200)
    assert len(rest["items"]) == 50 and rest["next_offset"] is None
    ev = gql(c, EVENTS)["data"]["events"]
    assert ev["total"] == 250 and len(ev["items"]) == 200
    older = gql(c, EVENTS, before=ev["next_before"])["data"]["events"]
    assert len(older["items"]) == 50 and older["next_before"] is None


def test_fleet(pg, monkeypatch):
    seed(pg)
    seed_jobs(pg)
    q = "{ fleet { available queues hosts { name } workers { name age } failures { args } } }"
    f = gql(TestClient(create_app(pg)), q)["data"]["fleet"]
    assert f["available"] and f["queues"]["sources"]["failed"] == 1 and f["hosts"]
    monkeypatch.setattr(queries, "JOBS_TABLE", "procrastinate_missing")
    assert gql(TestClient(create_app(pg)), q)["data"]["fleet"]["available"] is False


def test_saved_views(client):
    assert gql(client, VIEWS)["data"]["views"] == []  # nothing seeded
    state = {"f": ["borough.in.Queens"], "sort": "-rent"}
    v = gql(client, CREATE, SAME, page="listings", name="Queens", is_default=True, state=state)["data"]["createView"]
    assert v["state"] == {"f": ["borough.in.Queens"], "q": "", "sort": "-rent", "columns": v["state"]["columns"]}
    assert v["is_default"] and gql(client, VIEWS)["data"]["views"][0]["count"] == 1
    vid = v["id"]
    change = """mutation($id: Int!, $name: String, $state: ViewStateInput, $move: Int) {
      changeView(id: $id, name: $name, state: $state, move: $move) { name position state { f } } }"""
    p = gql(client, change, SAME, id=vid, state={"f": ["verdict.in.eligible"]}, name="Renamed")["data"]["changeView"]
    assert p["name"] == "Renamed" and p["state"]["f"] == ["verdict.in.eligible"]
    dup = gql(client, "mutation($id: Int!) { duplicateView(id: $id) { name } }", SAME, id=vid)["data"]
    assert dup["duplicateView"]["name"] == "Renamed (copy)"
    assert gql(client, change, SAME, id=vid, move=1)["data"]["changeView"]["position"] == 1
    bad = gql(client, CREATE, SAME, page="listings", name="x", state={"f": ["nope.in.x"]})
    assert "Unknown filter field" in gql_error(bad) and bad["data"] is None
    assert gql_error(gql(client, CREATE, SAME, page="listings", name=""))
    assert gql_error(gql(client, CREATE, SAME, page="nope", name="x")) == "Unknown page “nope”"
    delete = "mutation($id: Int!) { deleteView(id: $id) }"
    assert gql(client, delete, SAME, id=vid)["data"]["deleteView"] is True
    assert gql_error(gql(client, delete, SAME, id=vid)) == "No such view"
    assert gql_error(gql(client, change, SAME, id=vid, name="x")) == "No such view"


def test_view_writes_need_same_origin(client):
    refused = "cross-origin request refused"
    assert gql_error(gql(client, CREATE, page="listings", name="X")) == refused
    assert gql_error(gql(client, CREATE, {"origin": "http://evil.example"}, page="listings", name="X")) == refused
    cross = {**SAME, "sec-fetch-site": "cross-site"}
    assert gql_error(gql(client, CREATE, cross, page="listings", name="X")) == refused
    assert gql(client, VIEWS)["data"]["views"] == []  # nothing was written
    ok = gql(client, CREATE, {"referer": "http://testserver/listings"}, page="listings", name="X")
    assert ok["data"]["createView"]["name"] == "X"
    assert gql_error(gql(client, "mutation { deleteView(id: 1) }")) == refused


def test_stale_view_warns(client, pg):
    vid = gql(client, CREATE, SAME, page="listings", name="Old")["data"]["createView"]["id"]
    with Session(pg) as s, s.begin():
        s.execute(update(SavedViewRow).where(SavedViewRow.id == vid).values(
            query={"filters": [{"field": "pets_ok", "op": "is", "values": ["yes"]}]}))  # fmt: skip
    (v,) = gql(client, VIEWS)["data"]["views"]
    assert "Unknown filter field “pets_ok”" in v["warnings"][0] and v["state"]["f"] == []


def test_application_prefill(pg, monkeypatch, tmp_path):
    blank = (FX / "hc2.pdf").read_bytes()
    monkeypatch.setattr(applications, "blank", lambda url: blank if url.endswith("g1.pdf") else b"%PDF flat")
    with Session(pg) as s, s.begin():
        add_listing(
            s, L("m", "1", "Queens", notes="Mail only. Application: https://x.example/g1.pdf; https://x.example/f.pdf")
        )
    c = TestClient(create_app(pg))
    pdfs = gql(c, 'query { listing(key: "m:1") { pdfs } }')["data"]["listing"]["pdfs"]
    assert pdfs == ["https://x.example/g1.pdf", "https://x.example/f.pdf"]
    apps = 'query { listingApplications(key: "m:1") { ready pdfs { n url form error } } }'
    p = gql(c, apps)["data"]["listingApplications"]
    assert not p["ready"] and [x["form"] for x in p["pdfs"]] == ["HC2.0 Affordable Housing Application", None]
    r = c.get("/api/listing/application?key=m%3A1")
    assert r.status_code == 404 and "HW_APPLICANT_FILE" in r.json()["error"]  # no applicant file yet
    f = tmp_path / "applicant.json"
    f.write_text((FX / "applicant.example.json").read_text())
    monkeypatch.setattr(applications, "APPLICANT_FILE", str(f))
    assert gql(c, apps)["data"]["listingApplications"]["ready"]
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
    assert c.get("/api/nope").status_code == 404 and c.get("/api/listings").json()["error"]
    assert c.get("/../etc/passwd").status_code in (200, 404)


def test_units_api(client):
    q = """query($f: [String!], $sort: String!) { units(f: $f, sort: $sort) {
      items { unit_no income_min } total verdict_counts { eligible } } }"""
    r = gql(client, q, f=["borough.in.Queens"], sort="rent")["data"]["units"]
    assert r["total"] == 1 and r["items"][0]["unit_no"] == "4B" and r["items"][0]["income_min"] == 82557
    assert r["verdict_counts"]["eligible"] == 1
    v = gql(client, CREATE, SAME, page="units", name="Cheap", state={"f": ["units.rent.le.2500"]})["data"]
    assert v["createView"]["page"] == "units"
    assert gql(client, VIEWS)["data"]["views"][0]["count"] == 3  # a:1, a:2 and closed b:2
