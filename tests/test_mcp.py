import asyncio
import json

import pytest

from housing_watch import mcp_server
from seed import seed


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
    with pytest.raises(ToolError, match="read-only"):
        call(pg, "graphql", query="mutation { deleteView(input: { id: 1 }) { id } }")
    with pytest.raises(ToolError, match="read-only"):
        call(pg, "graphql", query="query A { views { id } } mutation B { deleteView(input: { id: 1 }) { id } }")
    bad = call(pg, "graphql", query="{ listings(")
    assert bad["data"] is None and "Syntax Error" in bad["errors"][0]["message"]
    missing = call(pg, "graphql", query="query($k: ID!) { listing(key: $k) { ext_id } }", variables={"k": "x"})
    assert missing["errors"][0]["extensions"]["code"] == "NOT_FOUND"


def test_never_runs_without_auth(monkeypatch):
    for k in ("HW_MCP_BASE_URL", "HW_MCP_OIDC_CONFIG_URL", "HW_MCP_CLIENT_ID", "HW_MCP_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="HW_MCP_BASE_URL"):
        mcp_server.auth_from_env()
