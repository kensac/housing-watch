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


def test_schemas_are_typed(pg):
    def obj(schema: dict) -> dict:  # FastMCP inlines $defs: an optional object is anyOf [object, null]
        return next(x for x in schema.get("anyOf", [schema]) if x.get("type") in ("object", "array"))

    t = tools(pg)
    f = obj(t["find_listings"].input_schema["properties"]["filter"])
    borough = obj(f["properties"]["borough"])
    assert obj(borough["properties"]["in"])["items"]["enum"] == ["Manhattan", "Brooklyn", "Queens", "Bronx",
                                                                  "Staten Island"]  # fmt: skip
    assert borough["additionalProperties"] is False and "contains" not in borough["properties"]  # enums: no text ops
    units = obj(f["properties"]["units"])
    assert {"rent", "size", "bands"} <= set(units["properties"])
    sort = obj(t["find_listings"].input_schema["properties"]["sort"])["items"]
    assert sort["properties"]["field"]["enum"][0] == "verdict"
    ev = obj(t["recent_changes"].input_schema["properties"]["filter"])
    assert "listing" in ev["properties"] and "type" in ev["properties"]
    out = t["find_listings"].output_schema
    listing = obj(out["properties"]["items"])["items"]
    assert "haystack" not in listing["properties"] and "verdict_counts" in out["properties"]
    assert listing["properties"]["verdict"]["enum"] == ["eligible", "possible", "excluded"]
    assert t["overview"].output_schema and t["sources"].output_schema and t["fetch"].output_schema


def test_graphql_tool(pg):
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
    make = 'mutation { createView(input: { name: "Cheap", state: { listings: { q: "main" } } }) { view { id } } }'
    made = call(pg, "graphql", query=make)  # trusted: no same-origin check
    assert made["errors"] == [] and made["data"]["createView"]["view"]["id"]
    gone = call(pg, "graphql", query="mutation { deleteView(input: { id: 99 }) { id } }")
    assert gone["errors"][0]["extensions"]["code"] == "NOT_FOUND"
    with pytest.raises(ToolError, match="subscriptions"):
        call(pg, "graphql", query="subscription { x }")
    bad = call(pg, "graphql", query="{ listings(")
    assert bad["data"] is None and "Syntax Error" in bad["errors"][0]["message"]
    missing = call(pg, "graphql", query="query($k: ID!) { listing(key: $k) { ext_id } }", variables={"k": "x"})
    assert missing["errors"][0]["extensions"]["code"] == "NOT_FOUND"


def test_view_tools(pg):
    from fastmcp.exceptions import ToolError

    seed(pg)
    st = {"listings": {"filter": {"verdict": {"in": ["eligible"]}}, "sort": [{"field": "title"}]}}
    v = call(pg, "create_view", name="Eligible", state=st, is_default=True)
    assert (v["page"], v["is_default"], v["filter"]) == ("listings", True, {"verdict": {"in": ["eligible"]}})
    assert v["sort"] == [{"field": "title", "direction": "asc"}]
    ev = call(pg, "create_view", name="New", state={"events": {"filter": {"type": {"in": ["new"]}}}})
    assert ev["page"] == "events" and ev["position"] == 0
    copy = call(pg, "duplicate_view", id=v["id"])
    assert copy["name"] == "Eligible (copy)" and not copy["is_default"]
    moved = call(pg, "change_view", id=copy["id"], name="Mine", move="up", is_default=True)
    assert (moved["name"], moved["position"], moved["is_default"]) == ("Mine", 0, True)
    listed = {x["id"]: x for x in call(pg, "saved_views")["result"]}
    assert listed[v["id"]]["count"] == 1 and not listed[v["id"]]["is_default"]
    with pytest.raises(ToolError, match="one page"):
        call(pg, "change_view", id=v["id"], state={"units": {}})
    with pytest.raises(ToolError, match="exactly one"):
        call(pg, "create_view", name="x", state={"listings": {}, "units": {}})
    with pytest.raises(ToolError, match="name"):
        call(pg, "create_view", name=" ", state={"listings": {}})
    with pytest.raises(ToolError):
        call(pg, "create_view", name="x", state={"units": {"columns": ["mailed"]}})  # an events column
    assert call(pg, "delete_view", id=ev["id"]) == {"id": ev["id"]}
    for tool in ("delete_view", "duplicate_view", "change_view"):
        with pytest.raises(ToolError, match="no view"):
            call(pg, tool, id=ev["id"])
    t = tools(pg)
    assert t["delete_view"].annotations.destructive_hint and not t["create_view"].annotations.read_only_hint
    assert t["find_listings"].annotations.read_only_hint


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
    with pytest.raises(ToolError, match="no tracker row"):
        call(pg, "link_tracker", listing_key="a:1", row_key="nope")
    with pytest.raises(ToolError, match="no listing"):
        call(pg, "link_tracker", listing_key="zz:9", row_key=key)
    un = call(pg, "unlink_tracker", listing_key="a:1")
    assert un["row_key"] is None and un["listing"]["application"] is None
    with pytest.raises(ToolError, match="no manual link"):
        call(pg, "unlink_tracker", listing_key="a:1")


def test_never_runs_without_auth(monkeypatch):
    for k in ("HW_MCP_BASE_URL", "HW_MCP_OIDC_CONFIG_URL", "HW_MCP_CLIENT_ID", "HW_MCP_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="HW_MCP_BASE_URL"):
        mcp_server.auth_from_env()
