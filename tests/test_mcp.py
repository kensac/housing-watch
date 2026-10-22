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


def test_never_runs_without_auth(monkeypatch):
    for k in ("HW_MCP_BASE_URL", "HW_MCP_OIDC_CONFIG_URL", "HW_MCP_CLIENT_ID", "HW_MCP_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="HW_MCP_BASE_URL"):
        mcp_server.auth_from_env()
