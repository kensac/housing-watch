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


def test_tools(pg):
    seed(pg)
    found = call(pg, "find_listings", filters=["verdict.in.eligible"])
    assert [x["key"] for x in found["items"]] == ["a:1"] and "haystack" not in found["items"][0]
    assert call(pg, "search", query="main st")["results"][0]["id"] == "a:1"
    doc = call(pg, "fetch", id="a:1")
    assert doc["metadata"]["verdict"] == "eligible" and json.loads(doc["text"])["units"]
    assert call(pg, "recent_changes")["items"]
    assert "eligible_now" in call(pg, "overview")


def test_bad_filter_is_reported(pg):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        call(pg, "find_listings", filters=["nope.in.x"])


def test_graphql_tool(pg):
    from fastmcp.exceptions import ToolError

    seed(pg)
    q = '{ listings(f: ["verdict.in.eligible"], first: 5) { edges { node { key } } page_info { has_next_page } } }'
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
    missing = call(pg, "graphql", query="query($k: String!) { listing(key: $k) { ext_id } }", variables={"k": "x"})
    assert missing["errors"][0]["extensions"]["code"] == "NOT_FOUND"


def test_never_runs_without_auth(monkeypatch):
    for k in ("HW_MCP_BASE_URL", "HW_MCP_OIDC_CONFIG_URL", "HW_MCP_CLIENT_ID", "HW_MCP_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="HW_MCP_BASE_URL"):
        mcp_server.auth_from_env()
