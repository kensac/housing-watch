import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from housing_watch import applications, queries
from housing_watch.orm import SavedViewRow
from housing_watch.web.app import create_app
from housing_watch.web.schema import schema
from seed import L, add_listing, event, gql, gql_code, gql_error, nodes, seed, seed_jobs

SAME = {"origin": "http://testserver"}
FX = Path(__file__).parent / "fixtures" / "applications"
SDL = Path(__file__).parent.parent / "schema.graphql"
LISTINGS = """query($filter: ListingFilterInput, $q: String! = "", $sort: [ListingSortInput!], $after: String,
                   $first: Int! = 200) {
  listings(filter: $filter, q: $q, sort: $sort, after: $after, first: $first) {
    edges { cursor node { key title verdict status sizes tracker_match application_since deadline } }
    page_info { has_next_page has_previous_page start_cursor end_cursor } total
    filters { field op label } hidden_gone verdict_counts { eligible }
  }
}"""
EVENTS = """query($filter: EventFilterInput, $after: String) {
  events(filter: $filter, after: $after) { edges { node { key type } } page_info { has_next_page end_cursor } total }
}"""
# the pages' sort and column enums differ, so a selection of both needs aliases (GraphQL's field merging rule)
STATE = """state { __typename
  ... on ListingsViewState { q filter { borough { in } verdict { in } units { rent { le } } } sort { field direction }
                             columns }
  ... on UnitsViewState { q filter { units { rent { le } } } unit_sort: sort { field direction } unit_columns: columns }
  ... on EventsViewState { filter { type { in } listing { verdict { in } } } event_columns: columns } }"""
VIEW = f"id page name position is_default warnings count {STATE}"
VIEWS = f"{{ views {{ {VIEW} }} }}"
CREATE = f"""mutation($name: String!, $is_default: Boolean! = false, $state: ViewStateInput! = {{ listings: {{}} }}) {{
  createView(input: {{ name: $name, is_default: $is_default, state: $state }}) {{ view {{ {VIEW} }} }}
}}"""
CHANGE = f"""mutation($id: ID!, $name: String, $state: ViewStateInput, $move: ViewMove) {{
  changeView(input: {{ id: $id, name: $name, state: $state, move: $move }}) {{ view {{ name position {STATE} }} }} }}"""
DELETE = "mutation($id: ID!) { deleteView(input: { id: $id }) { id } }"


@pytest.fixture
def client(pg):
    seed(pg)
    return TestClient(create_app(pg))


def listings(c, **v) -> dict:
    return gql(c, LISTINGS, **v)["data"]["listings"]


def titles(r: dict) -> list[str]:
    return [x["title"] for x in nodes(r)]


def test_committed_sdl_matches_the_code():
    assert SDL.read_text() == schema.as_str() + "\n", "run `python -m housing_watch schema` and commit schema.graphql"


def test_meta(client):
    q = """{ meta { ops { op label } verdicts statuses profile { household sizes excluded_boroughs }
      pages { listings { fields { name path type money choices { name label } ops } default_sort { field direction }
                         columns { column label } default_columns }
              units { sorts { field label natural } } events { fields { name path } default_columns } } } }"""
    m = gql(client, q)["data"]["meta"]
    fields = {f["name"]: f for f in m["pages"]["listings"]["fields"]}
    assert fields["units_rent"]["type"] == "number" and fields["units_rent"]["money"]
    assert fields["units_rent"]["path"] == ["units", "rent"] and "between" in fields["units_ami"]["ops"]
    assert [c["name"] for c in fields["verdict"]["choices"]] == ["eligible", "possible", "excluded"]
    assert {"name": "Staten_Island", "label": "Staten Island"} in fields["borough"]["choices"]
    assert fields["borough"]["type"] == "enum" and fields["borough"]["ops"] == ["in", "not_in", "empty"]
    assert fields["units_bands_household"]["path"] == ["units", "bands", "household"]
    events = {f["name"]: f["path"] for f in m["pages"]["events"]["fields"]}
    assert events["listing_borough"] == ["listing", "borough"] and events["listing_verdict"] == ["listing", "verdict"]
    assert {"field": "rent", "label": "Rent", "natural": "asc"} in m["pages"]["units"]["sorts"]
    assert {"field": "changed", "label": "Last changed", "natural": "desc"} in m["pages"]["units"]["sorts"]
    assert m["pages"]["listings"]["default_sort"] == [{"field": "verdict", "direction": "asc"}]
    assert {"column": "rent", "label": "Rent"} in m["pages"]["listings"]["columns"]
    assert m["pages"]["events"]["default_columns"] == ["verdict", "source", "mailed"]
    assert {"op": "le", "label": "≤"} in m["ops"] and m["verdicts"] == ["eligible", "possible", "excluded"]
    assert m["profile"] == {"household": 1, "sizes": ["_1br", "studio"], "excluded_boroughs": ["Bronx"]}
    assert "gone" in m["statuses"]


def test_listings(client):
    r = listings(client)
    assert titles(r)[0] == "a 1" and "b 3" not in titles(r)  # gone hidden
    x = nodes(r)[0]
    assert x["verdict"] == "eligible" and x["status"] == "open" and x["sizes"] == ["studio"]
    f = {"verdict": {"in": ["excluded"]}, "borough": {"in": ["Bronx"]},
         "units": {"size": {"in": ["studio"]}, "rent": {"le": 5000}},
         "last_changed": {"last": {"amount": 7, "unit": "days"}}}  # fmt: skip
    r = listings(client, filter=f, sort=[{"field": "rent", "direction": "desc"}], q="a")
    assert titles(r) == ["a 2"] and {"field": "borough", "op": "in", "label": "Borough is Bronx"} in r["filters"]
    assert {"field": "units_rent", "op": "le", "label": "Rent ≤ $5,000"} in r["filters"] and len(r["filters"]) == 5
    sizes = listings(client, filter={"units": {"size": {"in": ["_1br", "studio"]}}})
    assert set(titles(sizes)) == {"a 1", "a 2", "b 2"}  # enum names stand for the data's values ("1br")
    by_title = listings(client, sort=[{"field": "source", "direction": "desc"}, {"field": "title"}])
    assert titles(by_title)[0].startswith("b ")


def test_bad_filters_are_refused(client):
    for f, msg in [({"units": {"rent": {"le": "junk"}}}, "Float cannot represent"),
                   ({"nope": {"in": ["x"]}}, "nope"), ({"borough": {"in": ["Jersey City"]}}, "Borough"),
                   ({"borough": {"le": 3}}, "le")]:  # fmt: skip
        res = gql(client, LISTINGS, filter=f)
        assert msg in gql_error(res) and gql_code(res) == "BAD_USER_INPUT" and res.get("data") is None
    for f, msg in [({"borough": {"in": []}}, "expected one or more"), ({"notes": {"contains": " "}}, "some text"),
                   ({"first_seen": {"last": {"amount": 0, "unit": "days"}}}, "whole number")]:  # fmt: skip
        res = gql(client, LISTINGS, filter=f)  # well-typed, but meaningless: the resolver refuses it
        assert msg in gql_error(res) and gql_code(res) == "BAD_USER_INPUT"
    assert gql_code(gql(client, EVENTS, filter={"listing": {"nope": {}}})) == "BAD_USER_INPUT"
    bad_sort = gql(client, LISTINGS, sort=[{"field": "ami"}])  # a Units sort
    assert gql_code(bad_sort) == "BAD_USER_INPUT"


def test_listing_detail(client):
    q = """query($key: ID!) { listing(key: $key) {
      summary { title } notes units { bands { household income_min income_max } verdict } events { type } pdfs } }"""
    d = gql(client, q, key="a:1")["data"]["listing"]
    assert d["summary"]["title"] == "a 1" and d["notes"] == "Apply by mail"
    assert d["units"][0]["bands"][0] == {"household": 1, "income_min": 82557, "income_max": 154440}
    assert d["events"][0]["type"] == "new" and d["pdfs"] == []
    res = gql(client, q, key="missing")
    assert gql_error(res) == "This listing is not in the store." and gql_code(res) == "NOT_FOUND"


def test_values(client):
    q = """query($field: FilterFieldName!, $filter: PageFilterInput!) {
      values(field: $field, filter: $filter) { enum counts { value label count } } }"""
    v = gql(client, q, field="borough", filter={"listings": {"verdict": {"in": ["eligible"]}}})["data"]["values"]
    assert v["enum"] and {c["value"] for c in v["counts"] if c["count"]} == {"Queens"}
    assert {"value": "Staten_Island", "label": "Staten Island", "count": 0} in v["counts"]  # enum names to send back
    sizes = gql(client, q, field="units_size", filter={"units": {}})["data"]["values"]["counts"]
    assert {"value": "studio", "label": "studio", "count": 3} in sizes
    assert not gql(client, q, field="units_rent", filter={"listings": {}})["data"]["values"]["enum"]
    ev = gql(client, q, field="type", filter={"events": {}})["data"]["values"]
    assert "source_failed" in {c["value"] for c in ev["counts"]}
    assert gql(client, q, field="notes", filter={"listings": {}})["data"]["values"]["counts"][0]["value"] is None
    res = gql(client, q, field="type", filter={"listings": {}})  # an events field
    assert "listings has no field type" in gql_error(res) and gql_code(res) == "BAD_USER_INPUT"
    assert gql_code(gql(client, q, field="x", filter={"listings": {}})) == "BAD_USER_INPUT"  # not a field
    both = gql(client, q, field="borough", filter={"listings": {}, "units": {}})  # @oneOf: exactly one page
    assert gql_code(both) == "BAD_USER_INPUT"


def test_events_overview_sources(client):
    e = gql(client, EVENTS)["data"]["events"]
    assert e["total"] == 6 and nodes(e)[0]["type"] == "source_failed" and not e["page_info"]["has_next_page"]
    f = {"listing": {"borough": {"in": ["Queens"]}, "verdict": {"in": ["eligible"]}}}
    e = gql(client, EVENTS, filter=f)["data"]["events"]
    assert [x["key"] for x in nodes(e)] == ["b:3", "a:1"]  # removed listings included
    q = "{ overview { eligible_open failing eligible_now { title } matrix { verdict status count }"
    q += " digest { events } } }"
    o = gql(client, q)["data"]["overview"]
    assert o["eligible_open"] == 1 and o["eligible_now"][0]["title"] == "a 1" and o["failing"] == ["b"]
    assert {"verdict": "excluded", "status": "closed", "count": 1} in o["matrix"] and o["digest"]["events"] >= 1
    s = {x["source"]: x for x in gql(client, "{ sources { source health last_error } }")["data"]["sources"]}
    assert s["b"]["health"] == "failing" and s["b"]["last_error"] == "TimeoutError: boom"


def test_kener_monitor_query(client):
    res = client.post("/graphql", json={"query": "{ sources { source } }"}).json()
    assert "errors" not in res and isinstance(res["data"]["sources"], list) and res["data"]["sources"]


def test_event_detail_and_listing(pg):
    seed(pg)
    before = {"status": "open", "deadline": None, "units": [["studio", "4B", 2400.0, 130, None, None]]}
    after = {**before, "status": "waitlist", "units": [["studio", "4B", 2500.0, 130, None, None, [[1, 1, 2]]]]}
    with Session(pg) as s, s.begin():
        event(s, "a:1", "changed", detail={"before": before, "after": after, "changes": ["Status: open → waitlist"]})
        event(s, "a:2", "page_changed", detail={"added": ["x"], "removed": [], "new_links": ["https://y"]})
    q = """{ events { edges { node { type listing { key verdict } detail { __typename
      ... on ListingChange { changes noise before { status units { rent bands { household } } }
                             after { units { rent bands { household income_max } } } }
      ... on PageChange { added new_links } ... on SourceFailure { error fails } } } } } }"""
    by = {}
    for n in nodes(gql(TestClient(create_app(pg)), q)["data"]["events"]):
        by.setdefault(n["type"], n)
    ch = by["changed"]
    assert ch["listing"] == {"key": "a:1", "verdict": "eligible"} and ch["detail"]["changes"] == [
        "Status: open → waitlist"
    ]
    assert ch["detail"]["before"]["units"] == [{"rent": 2400.0, "bands": []}] and not ch["detail"]["noise"]
    assert ch["detail"]["after"]["units"][0]["bands"] == [{"household": 1, "income_max": 2}]
    assert by["page_changed"]["detail"] == {"__typename": "PageChange", "added": ["x"], "new_links": ["https://y"]}
    fail = by["source_failed"]
    assert fail["listing"] is None and fail["detail"] == {"__typename": "SourceFailure", "error": "TimeoutError: boom",
                                                          "fails": 2}  # fmt: skip
    assert by["new"]["detail"] is None


def test_event_listings_load_in_one_batch(pg, monkeypatch):
    seed(pg)
    calls = []
    judged = queries.judged
    monkeypatch.setattr(queries, "judged", lambda *a, **kw: calls.append(1) or judged(*a, **kw))
    q = "{ events { edges { node { listing { key } } } } }"
    res = gql(TestClient(create_app(pg)), q)
    assert len([n for n in nodes(res["data"]["events"]) if n["listing"]]) == 5  # b:3 is gone but stored
    assert len(calls) <= 2  # the events' titles, then one batch for every listing


def test_get_queries_work(client):
    r = client.get("/graphql", params={"query": "{ overview { total } }"}).json()
    assert r["data"]["overview"]["total"] >= 1
    assert (
        client.get("/graphql", params={"query": "mutation { deleteView(input: { id: 1 }) { id } }"}).status_code >= 400
    )


def test_connections_page_with_cursors(pg):
    with Session(pg) as s, s.begin():
        for i in range(250):
            add_listing(s, L("bulk", f"{i:03}", "Queens"))
            event(s, f"bulk:{i:03}", "new")
    c = TestClient(create_app(pg))
    first = listings(c, sort=[{"field": "title"}])
    pi = first["page_info"]
    assert first["total"] == 250 and len(first["edges"]) == 200 and pi["has_next_page"] and not pi["has_previous_page"]
    assert pi["end_cursor"] == first["edges"][-1]["cursor"] and pi["start_cursor"] == first["edges"][0]["cursor"]
    rest = listings(c, sort=[{"field": "title"}], after=pi["end_cursor"])
    assert (
        len(rest["edges"]) == 50 and not rest["page_info"]["has_next_page"] and rest["page_info"]["has_previous_page"]
    )
    assert titles(rest)[0] == "bulk 200" and len(set(titles(first)) | set(titles(rest))) == 250
    ev = gql(c, EVENTS)["data"]["events"]
    assert ev["total"] == 250 and len(ev["edges"]) == 200 and ev["page_info"]["has_next_page"]
    older = gql(c, EVENTS, after=ev["page_info"]["end_cursor"])["data"]["events"]
    assert len(older["edges"]) == 50 and not older["page_info"]["has_next_page"]


def test_bad_cursor_and_page_size(client):
    other = gql(client, EVENTS)["data"]["events"]["page_info"]["end_cursor"] or base64.b64encode(b"events:9").decode()
    for after in (other, "junk", base64.b64encode(b"listings:x").decode()):
        res = gql(client, LISTINGS, after=after)
        assert gql_code(res) == "BAD_USER_INPUT" and gql_error(res) == "That cursor is not from this list."
    for first in (0, 501):
        assert gql_error(gql(client, LISTINGS, first=first)) == "first must be 1 to 500"


def test_errors_are_coded(client, monkeypatch):
    assert gql_code(gql(client, "{ nope }")) == "BAD_USER_INPUT"
    assert gql_code(gql(client, "{ listings(")) == "BAD_USER_INPUT"
    monkeypatch.setattr(queries, "sources", lambda *a: 1 / 0)
    res = gql(client, "{ sources { source } }")
    assert gql_code(res) == "INTERNAL" and gql_error(res) == "division by zero"


def test_abuse_limits_keep_introspection(client):
    from graphql import get_introspection_query

    ok = (
        "{ events { edges { node { detail { ... on ListingChange { before { units { bands { household } } } } } } } } }"
    )
    assert "errors" not in gql(client, ok)
    deep = gql(client, "{ " + "a { " * 11 + "b" + " }" * 11 + " }")
    assert any("depth" in e["message"] for e in deep["errors"]) and gql_code(deep) == "BAD_USER_INPUT"
    aliases = gql(client, "{ " + " ".join(f"a{i}: sources {{ source }}" for i in range(16)) + " }")
    assert "aliases" in gql_error(aliases) and gql_code(aliases) == "BAD_USER_INPUT"
    tokens = gql(client, "{ " + "sources { source } " * 600 + "}")
    assert "token" in gql_error(tokens).lower() and gql_code(tokens) == "BAD_USER_INPUT"
    intro = gql(client, get_introspection_query(descriptions=True, input_value_deprecation=True))
    assert "errors" not in intro and intro["data"]["__schema"]["queryType"]["name"] == "Query"


def test_fleet(pg, monkeypatch):
    seed(pg)
    seed_jobs(pg)
    q = """{ fleet { available queues { queue status count } hosts { name } workers { name age }
      failures { id args { source key run_id timestamp } } } }"""
    f = gql(TestClient(create_app(pg)), q)["data"]["fleet"]
    assert f["available"] and {"queue": "sources", "status": "failed", "count": 1} in f["queues"] and f["hosts"]
    assert f["failures"] == [{"id": "2", "args": {"source": "b", "key": None, "run_id": None, "timestamp": None}}]
    monkeypatch.setattr(queries, "JOBS_TABLE", "procrastinate_missing")
    assert gql(TestClient(create_app(pg)), q)["data"]["fleet"]["available"] is False


def test_saved_views(client):
    assert gql(client, VIEWS)["data"]["views"] == []  # nothing seeded
    state = {"listings": {"filter": {"borough": {"in": ["Queens"]}}, "sort": [{"field": "rent", "direction": "desc"}]}}
    res = gql(client, CREATE, SAME, name="Queens", is_default=True, state=state)
    v = res["data"]["createView"]["view"]
    assert v["state"] == {"__typename": "ListingsViewState", "q": "", "columns": v["state"]["columns"],
                          "filter": {"borough": {"in": ["Queens"]}, "verdict": None, "units": None},
                          "sort": [{"field": "rent", "direction": "desc"}]}  # fmt: skip
    assert v["is_default"] and v["page"] == "listings" and v["count"] == 1  # a write returns the count too
    assert gql(client, VIEWS)["data"]["views"][0]["count"] == 1
    vid = v["id"]
    st = {"listings": {"filter": {"verdict": {"in": ["eligible"]}}, "columns": ["rent", "verdict"]}}
    p = gql(client, CHANGE, SAME, id=vid, state=st, name="Renamed")["data"]["changeView"]
    assert p["view"]["name"] == "Renamed" and p["view"]["state"]["filter"]["verdict"] == {"in": ["eligible"]}
    assert p["view"]["state"]["sort"] == [{"field": "verdict", "direction": "asc"}]  # none given: the default
    assert p["view"]["state"]["columns"] == ["rent", "verdict"]
    dup = gql(client, "mutation($id: ID!) { duplicateView(input: { id: $id }) { view { name } } }", SAME, id=vid)
    assert dup["data"]["duplicateView"]["view"]["name"] == "Renamed (copy)"
    assert gql(client, CHANGE, SAME, id=vid, move="down")["data"]["changeView"]["view"]["position"] == 1
    bad = gql(client, CREATE, SAME, name="x", state={"listings": {"filter": {"borough": {"in": []}}}})
    assert "expected one or more" in gql_error(bad) and bad["data"] is None and gql_code(bad) == "BAD_USER_INPUT"
    assert gql_code(gql(client, CREATE, SAME, name="")) == "BAD_USER_INPUT"
    assert gql_code(gql(client, CREATE, SAME, name="x", state={"nope": {}})) == "BAD_USER_INPUT"
    other_page = gql(client, CHANGE, SAME, id=vid, state={"events": {}})
    assert "belongs to one page" in gql_error(other_page)
    ev = gql(
        client, CREATE, SAME, name="E", state={"events": {"filter": {"listing": {"verdict": {"in": ["eligible"]}}}}}
    )
    assert ev["data"]["createView"]["view"]["state"]["filter"]["listing"] == {"verdict": {"in": ["eligible"]}}
    assert gql(client, DELETE, SAME, id=vid)["data"]["deleteView"] == {"id": vid}
    gone = gql(client, DELETE, SAME, id=vid)
    assert gql_error(gone) == "No such view" and gql_code(gone) == "NOT_FOUND"
    assert gql_error(gql(client, CHANGE, SAME, id=vid, name="x")) == "No such view"
    assert gql_code(gql(client, DELETE, SAME, id="abc")) == "NOT_FOUND"


def test_view_writes_need_same_origin(client):
    refused = "cross-origin request refused"
    res = gql(client, CREATE, name="X")
    assert gql_error(res) == refused and gql_code(res) == "FORBIDDEN"
    assert gql_error(gql(client, CREATE, {"origin": "http://evil.example"}, name="X")) == refused
    cross = {**SAME, "sec-fetch-site": "cross-site"}
    assert gql_error(gql(client, CREATE, cross, name="X")) == refused
    assert gql(client, VIEWS)["data"]["views"] == []  # nothing was written
    ok = gql(client, CREATE, {"referer": "http://testserver/listings"}, name="X")
    assert ok["data"]["createView"]["view"]["name"] == "X"
    assert gql_code(gql(client, "mutation { deleteView(input: { id: 1 }) { id } }")) == "FORBIDDEN"


def test_http_never_marks_the_context_trusted(client, pg, monkeypatch):
    from housing_watch.web import app as web_app

    made = []
    real = web_app.context
    monkeypatch.setattr(web_app, "context", lambda *a, **kw: made.append((a, kw)) or real(*a, **kw))
    sneaky = {"origin": "http://evil.example", "trusted": "true", "x-trusted": "1", "authorization": "Bearer x"}
    body = {"query": DELETE, "variables": {"id": 1, "trusted": True}, "extensions": {"trusted": True}}
    res = client.post("/graphql", json=body, headers=sneaky).json()
    assert gql_code(res) == "FORBIDDEN" and gql_error(res) == "cross-origin request refused"
    res = client.post("/graphql?trusted=1", json={"query": CREATE, "variables": {"name": "X"}}).json()
    assert gql_code(res) == "FORBIDDEN"  # no origin at all
    assert len(made) == 2 and all(len(a) == 3 and kw == {} for a, kw in made)  # (engine, profile, request) only
    assert gql(client, VIEWS)["data"]["views"] == []


def test_stale_view_warns(client, pg):
    vid = gql(client, CREATE, SAME, name="Old")["data"]["createView"]["view"]["id"]
    with Session(pg) as s, s.begin():
        s.execute(update(SavedViewRow).where(SavedViewRow.id == int(vid)).values(
            query={"version": 2, "filter": {"pets_ok": {"is": True}}}))  # fmt: skip
    (v,) = gql(client, VIEWS)["data"]["views"]
    assert "Unknown filter field “pets_ok”" in v["warnings"][0]
    assert v["state"]["filter"] == {"borough": None, "verdict": None, "units": None}


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
    bad = c.get("/api/listing/application?key=nope")
    assert bad.status_code == 404 and bad.json() == {"error": "This listing has no such application PDF.",
                                                     "code": "NOT_FOUND"}  # fmt: skip


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
    q = """query($filter: ListingFilterInput, $sort: [UnitSortInput!]) { units(filter: $filter, sort: $sort) {
      edges { node { unit_no income_min verdict listing_verdict size } } total verdict_counts { eligible } } }"""
    r = gql(client, q, filter={"borough": {"in": ["Queens"]}}, sort=[{"field": "rent"}])["data"]["units"]
    assert r["total"] == 1 and nodes(r)[0]["unit_no"] == "4B" and nodes(r)[0]["income_min"] == 82557
    assert r["verdict_counts"]["eligible"] == 1 and nodes(r)[0]["verdict"] == "eligible"
    assert nodes(r)[0]["size"] == "studio" and gql_code(gql(client, q, sort=[{"field": "nope"}])) == "BAD_USER_INPUT"
    st = {"units": {"filter": {"units": {"rent": {"le": 2500}}}, "sort": [{"field": "ami"}]}}
    v = gql(client, CREATE, SAME, name="Cheap", state=st)["data"]
    assert v["createView"]["view"]["page"] == "units"
    assert v["createView"]["view"]["state"]["unit_sort"] == [{"field": "ami", "direction": "asc"}]
    assert gql(client, VIEWS)["data"]["views"][0]["count"] == 3  # a:1, a:2 and closed b:2


def test_types_are_derived_from_the_read_model():
    from dataclasses import dataclass, fields

    from housing_watch.__main__ import main
    from housing_watch.web.derive import derive

    listing = {f.name for f in schema.get_type_by_name("Listing").fields}
    model = {f.name for f in fields(queries.ListingSummary)}
    assert listing == model - {"haystack", "unit_sizes", "unit_rents"}  # internal() fields stay out
    assert {f.name for f in schema.get_type_by_name("Unit").fields} == {f.name for f in fields(queries.UnitSummary)} - {
        "haystack"
    }

    @dataclass
    class Loose:
        d: dict

    with pytest.raises(TypeError, match="give a dict field a type"):
        derive(Loose)
    assert main(["schema", "--check", str(SDL)]) == 0


def test_schema_check_fails_when_stale(tmp_path):
    from housing_watch.__main__ import main

    stale = tmp_path / "schema.graphql"
    stale.write_text("type Query { old: Int }\n")
    assert main(["schema", "--check", str(stale)]) == 1
    assert main(["schema", str(stale)]) == 0 and main(["schema", "--check", str(stale)]) == 0


def test_enum_values_serialize_as_names(pg):
    with Session(pg) as s, s.begin():
        add_listing(s, L("s", "1", "Staten Island", kind="re-rental", units=[], deadline="2026-11-01"))
    c = TestClient(create_app(pg))
    (x,) = nodes(gql(c, '{ listings(filter: { borough: { in: [Staten_Island] } }) { edges { node { borough kind '
                        "deadline } } } }")["data"]["listings"])  # fmt: skip
    assert x == {"borough": "Staten_Island", "kind": "re_rental", "deadline": "2026-11-01"}
