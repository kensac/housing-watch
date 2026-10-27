import asyncio
import json

import pytest

from housing_watch import mcp_server
from housing_watch import tracker as T
from housing_watch.orm import TrackerRow
from seed import Session, seed


def call(eng, tool: str, **args):
    from fastmcp import Client

    async def go():
        async with Client(mcp_server.build(eng)) as c:
            return (await c.call_tool(tool, args)).structured_content

    return asyncio.run(go())


def tools(eng) -> dict:
    from fastmcp import Client

    async def go():
        async with Client(mcp_server.build(eng)) as c:
            return {t.name: t for t in await c.list_tools()}

    return asyncio.run(go())


def test_tools(pg):
    seed(pg)
    found = call(pg, "find_listings", filter={"verdict": {"in": ["eligible"]}})
    assert [x["key"] for x in found["items"]] == ["a:1"] and "haystack" not in found["items"][0]
    assert found["verdict_counts"] == {"eligible": 1, "possible": 1, "excluded": 2}
    cheap = {"units": {"size": {"in": ["studio", "1br"]}, "rent": {"le": 2500}}, "borough": {"not_in": ["Bronx"]}}
    assert {x["key"] for x in call(pg, "find_listings", filter=cheap)["items"]} == {"a:1", "b:2"}
    by = call(pg, "find_listings", sort=[{"field": "title", "direction": "desc"}])["items"]
    assert by[0]["key"] == "b:2"
    units = call(pg, "find_units", filter={"borough": {"in": ["Queens"]}}, sort=[{"field": "rent"}])
    assert [u["unit_no"] for u in units["items"]] == ["4B"] and units["items"][0]["size"] == "studio"
    ev = call(pg, "recent_changes", filter={"type": {"in": ["new"]}, "listing": {"verdict": {"in": ["eligible"]}}})
    assert [e["key"] for e in ev["items"]] == ["a:1"] and ev["items"][0]["first_run"] is False
    fields = {f["name"]: f for f in call(pg, "filter_fields", page="events")["result"]}
    assert (
        fields["listing.borough"]["path"] == ["listing", "borough"]
        and "Staten Island" in fields["listing.borough"]["choices"]
    )
    assert call(pg, "search", query="main st")["results"][0]["id"] == "a:1"
    doc = call(pg, "fetch", id="a:1")
    assert doc["metadata"]["verdict"] == "eligible" and json.loads(doc["text"])["units"]
    assert call(pg, "recent_changes")["items"]
    assert "eligible_now" in call(pg, "overview")


def test_bad_filters_are_refused(pg):
    from fastmcp.exceptions import ToolError

    for f in [{"nope": {"in": ["x"]}}, {"borough": {"in": ["Jersey City"]}}, {"units": {"rent": {"le": "cheap"}}},
              {"borough": {"le": 3}}]:  # fmt: skip
        with pytest.raises(ToolError):  # the schema refuses them before the tool runs
            call(pg, "find_listings", filter=f)
    with pytest.raises(ToolError, match="expected one or more"):  # well-typed, but meaningless
        call(pg, "find_listings", filter={"borough": {"in": []}})
    with pytest.raises(ToolError):
        call(pg, "find_listings", sort=[{"field": "ami"}])  # a Units sort


def resolve(root: dict, schema: dict) -> dict:
    """A schema node with its $ref followed and an optional `anyOf [X, null]` unwrapped."""
    if "anyOf" in schema:
        schema = next(x for x in schema["anyOf"] if x.get("type") != "null")
    if "$ref" in schema:
        schema = root["$defs"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def test_schemas_are_typed(pg):
    t = tools(pg)
    root = t["find_listings"].input_schema
    f = resolve(root, root["properties"]["filter"])
    borough = resolve(root, f["properties"]["borough"])
    assert resolve(root, borough["properties"]["in"]["items"])["enum"] == ["Manhattan", "Brooklyn", "Queens", "Bronx",
                                                                          "Staten Island"]  # fmt: skip
    assert borough["additionalProperties"] is False and "contains" not in borough["properties"]  # enums: no text ops
    assert "anyOf" not in f["properties"]["borough"]  # optional means not required, not nullable
    units = resolve(root, f["properties"]["units"])
    assert {"rent", "size", "bands"} <= set(units["properties"])
    sort = resolve(root, resolve(root, root["properties"]["sort"])["items"])
    assert sort["properties"]["field"]["enum"][0] == "verdict"
    ev_root = t["recent_changes"].input_schema
    ev = resolve(ev_root, ev_root["properties"]["filter"])
    assert "listing" in ev["properties"] and "type" in ev["properties"]
    out = t["find_listings"].output_schema
    listing = resolve(out, out["properties"]["items"]["items"])
    assert "haystack" not in listing["properties"] and "verdict_counts" in out["properties"]
    assert resolve(out, listing["properties"]["verdict"])["enum"] == ["eligible", "possible", "excluded"]
    assert listing["properties"]["commute"]["description"].startswith("Transit minutes")  # the read model's comments
    assert t["overview"].output_schema and t["sources"].output_schema and t["fetch"].output_schema


def test_view_output_is_typed_per_page(pg):
    out = tools(pg)["create_view"].output_schema
    state = resolve(out, out["properties"]["state"])
    listings, events = (resolve(out, state["properties"][k]) for k in ("listings", "events"))
    assert resolve(out, listings["properties"]["filter"]) is resolve(out, {"$ref": "#/$defs/ListingFilter"})
    assert resolve(out, events["properties"]["filter"]) is resolve(out, {"$ref": "#/$defs/EventFilter"})
    assert "mailed" in listings_cols(out, events) and "mailed" not in listings_cols(out, listings)
    sort = resolve(out, listings["properties"]["sort"]["items"])
    assert "verdict" in sort["properties"]["field"]["enum"] and "sort" not in events["properties"]
    assert out["properties"]["count"]["type"] == "integer" and "count" in out["required"]


def listings_cols(root: dict, page: dict) -> list[str]:
    return resolve(root, page["properties"]["columns"]["items"])["enum"]


def test_schemas_share_definitions(pg):
    """Each tool sends its $defs once; the filter schema is never expanded where it is used."""
    t = tools(pg)
    for name in ("find_listings", "find_units", "recent_changes", "create_view", "change_view"):
        s = t[name].input_schema
        assert "ListingFilter" in s["$defs"], name
        assert json.dumps(s).count('"Staten Island"') == 1, name  # BoroughFilter, once
    total = sum(len(x.model_dump_json(exclude_none=True)) for x in t.values())
    assert total < 150_000, f"tools/list is {total} bytes"  # it was 325 KB with every $ref expanded
    assert max(len(json.dumps(x.input_schema)) for x in t.values()) < 16_000


def test_graphql_tools(pg):
    from fastmcp.exceptions import ToolError

    seed(pg)
    q = "{ listings(filter: { verdict: { in: [eligible] } }, first: 5) { edges { node { key } } "
    q += "page_info { has_next_page } } }"
    res = call(pg, "graphql", query=q)
    assert res["data"]["listings"]["edges"] == [{"node": {"key": "a:1"}}] and res["errors"] == []
    ev = call(pg, "graphql", query="{ events { edges { node { listing { key } } } } }")["data"]["events"]
    assert {"listing": {"key": "a:1"}} in [e["node"] for e in ev["edges"]]  # DataLoaders work in-process too
    fields = call(pg, "graphql", query="{ __schema { queryType { fields { name } } } }")["data"]["__schema"]
    assert "listings" in {f["name"] for f in fields["queryType"]["fields"]}
    make = 'mutation { createView(input: { name: "Cheap", state: { listings: { q: "main" } } }) { view { id count } } }'
    with pytest.raises(ToolError, match="graphql_mutation"):
        call(pg, "graphql", query=make)  # the read-only tool never writes
    with pytest.raises(ToolError, match="use graphql for queries"):
        call(pg, "graphql_mutation", query=q)
    made = call(pg, "graphql_mutation", query=make)  # trusted: no same-origin check
    assert made["errors"] == [] and made["data"]["createView"]["view"]["count"] == 1
    gone = call(pg, "graphql_mutation", query="mutation { deleteView(input: { id: 99 }) { id } }")
    assert gone["errors"][0]["extensions"]["code"] == "NOT_FOUND"
    for tool in ("graphql", "graphql_mutation"):
        with pytest.raises(ToolError, match="only"):
            call(pg, tool, query="subscription { x }")
        bad = call(pg, tool, query="{ listings(")
        assert bad["data"] is None and "Syntax Error" in bad["errors"][0]["message"]
    missing = call(pg, "graphql", query="query($k: ID!) { listing(key: $k) { ext_id } }", variables={"k": "x"})
    assert missing["errors"][0]["extensions"]["code"] == "NOT_FOUND"
    t = tools(pg)
    assert t["graphql"].annotations.read_only_hint and not t["graphql"].annotations.destructive_hint
    m = t["graphql_mutation"].annotations
    assert not m.read_only_hint and m.destructive_hint  # deleteView


def test_view_tools(pg):
    from fastmcp.exceptions import ToolError

    seed(pg)
    st = {"listings": {"filter": {"verdict": {"in": ["eligible"]}}, "sort": [{"field": "title"}]}}
    v = call(pg, "create_view", name="Eligible", state=st, is_default=True)
    assert (v["page"], v["is_default"], v["count"]) == ("listings", True, 1)
    assert v["state"] == {"listings": {"filter": {"verdict": {"in": ["eligible"]}}, "q": "",
                                       "sort": [{"field": "title", "direction": "asc"}],
                                       "columns": v["state"]["listings"]["columns"]}}  # fmt: skip
    again = call(pg, "create_view", name="Again", state=v["state"])  # what it returns, it takes
    assert again["state"] == v["state"]
    ev = call(pg, "create_view", name="New", state={"events": {"filter": {"type": {"in": ["new"]}}}})
    assert ev["page"] == "events" and ev["position"] == 0 and ev["count"] == 3 and "sort" not in ev["state"]["events"]
    copy = call(pg, "duplicate_view", id=v["id"])
    assert copy["name"] == "Eligible (copy)" and not copy["is_default"] and copy["count"] == 1
    moved = call(pg, "change_view", id=copy["id"], name="Mine", move="up", is_default=True)
    assert (moved["name"], moved["position"], moved["is_default"], moved["count"]) == (
        "Mine",
        1,
        True,
        1,
    )  # Eligible, Again, then the copy
    listed = {x["id"]: x for x in call(pg, "saved_views")["result"]}
    assert listed[v["id"]]["count"] == 1 and not listed[v["id"]]["is_default"]
    with pytest.raises(ToolError, match="one page"):
        call(pg, "change_view", id=v["id"], state={"units": {}})
    with pytest.raises(ToolError, match="exactly one"):
        call(pg, "create_view", name="x", state={"listings": {}, "units": {}})
    with pytest.raises(ToolError, match="name"):
        call(pg, "create_view", name=" ", state={"listings": {}})
    with pytest.raises(ToolError, match="expected one or more"):
        call(pg, "create_view", name="x", state={"listings": {"filter": {"borough": {"in": []}}}})
    with pytest.raises(ToolError):
        call(pg, "create_view", name="x", state={"units": {"columns": ["mailed"]}})  # an events column
    assert call(pg, "delete_view", id=ev["id"]) == {"id": ev["id"]}
    for tool in ("delete_view", "duplicate_view", "change_view"):
        with pytest.raises(ToolError, match="No such view"):
            call(pg, tool, id=ev["id"])
    t = tools(pg)
    assert t["delete_view"].annotations.destructive_hint and not t["create_view"].annotations.read_only_hint
    assert t["find_listings"].annotations.read_only_hint


def test_duplicate_of_a_long_name_fits(pg):
    v = call(pg, "create_view", name="n" * 80, state={"listings": {}})
    copy = call(pg, "duplicate_view", id=call(pg, "duplicate_view", id=v["id"])["id"])
    assert copy["name"] == "n" * 73 + " (copy)"


def test_tracker_tools(pg):
    from fastmcp.exceptions import ToolError

    seed(pg)
    with Session(pg) as s, s.begin():
        s.add(TrackerRow(row_key=T.row_key("Row A"), position=2, property="Row A", status="Applied", cells={}))
    board = call(pg, "tracker_board")
    assert [r["property"] for r in board["rows"]] == ["Row A"] and board["rows"][0]["listings"] == []
    key = board["rows"][0]["row_key"]
    linked = call(pg, "link_tracker", listing_key="a:1", row_key=key)
    assert linked["row_key"] == key and linked["listing"]["application"] == "Applied"
    assert [x["key"] for x in call(pg, "tracker_board")["rows"][0]["listings"]] == ["a:1"]
    with pytest.raises(ToolError, match="No such tracker row"):
        call(pg, "link_tracker", listing_key="a:1", row_key="nope")
    with pytest.raises(ToolError, match="No such listing"):
        call(pg, "link_tracker", listing_key="zz:9", row_key=key)
    un = call(pg, "unlink_tracker", listing_key="a:1")
    assert un["row_key"] is None and un["listing"]["application"] is None
    with pytest.raises(ToolError, match="No manual link"):
        call(pg, "unlink_tracker", listing_key="a:1")


def test_writes_are_logged(pg, caplog, monkeypatch):
    from fastmcp.exceptions import ToolError
    from fastmcp.server.auth import AccessToken

    seed(pg)
    tok = AccessToken(token="t", client_id="c", scopes=[], claims={"sub": "u1", "email": "me@example.org"})
    monkeypatch.setattr("fastmcp.server.dependencies.get_access_token", lambda: tok)
    with caplog.at_level("INFO", logger="housing_watch"):
        v = call(pg, "create_view", name="Mine", state={"listings": {"filter": {"verdict": {"in": ["eligible"]}}}})
        with pytest.raises(ToolError):
            call(pg, "delete_view", id=999)
        call(pg, "graphql_mutation", query=f"mutation {{ deleteView(input: {{ id: {v['id']} }}) {{ id }} }}")
        call(pg, "find_listings")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mcp write")]
    assert lines == [
        "mcp write create_view by u1 <me@example.org>: name='Mine' state={listings} is_default=False",
        "mcp write delete_view by u1 <me@example.org>: id=999 refused: No such view",
        "mcp write graphql_mutation by u1 <me@example.org>: mutations=deleteView variables={}",
    ]  # reads are not logged


def test_never_runs_without_auth(monkeypatch):
    for k in ("HW_MCP_BASE_URL", "HW_MCP_OIDC_CONFIG_URL", "HW_MCP_CLIENT_ID", "HW_MCP_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="HW_MCP_BASE_URL"):
        mcp_server.auth_from_env()
