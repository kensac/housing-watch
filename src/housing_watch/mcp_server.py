"""Read-only MCP server over the store, for agents and chat apps (Claude, ChatGPT).

Streamable HTTP at HW_MCP_BASE_URL + /mcp. Sign-in is OAuth: FastMCP's OIDC proxy lets MCP clients register
themselves (DCR), then sends the user to the upstream OIDC provider (HW_MCP_OIDC_CONFIG_URL), which decides
who may sign in. It never starts without that: there is no unauthenticated mode. Client registrations and
upstream tokens are kept encrypted under FASTMCP_HOME; losing them means reconnecting, nothing else.

`search` and `fetch` have the shapes ChatGPT's connectors and deep research expect; the other tools take the
web UI's filter grammar (filters.py) and return the same fields as its API, minus the applicant's PDFs.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Annotated

from pydantic import Field
from sqlalchemy.engine import Engine

from . import digest, queries, views
from . import filters as F
from .criteria import PROFILE, Profile

READ = {"readOnlyHint": True, "openWorldHint": False}
GRAMMAR = (
    "Filters are strings `<field>.<op>[.<values>]`, values comma-separated, e.g. `verdict.in.eligible`, "
    "`borough.in.Brooklyn,Queens`, `units.rent.le.3000`, `status.in.open,waitlist`, `commute.le.45`, "
    "`last_changed.last.7d`. `filter_fields` lists every field and its operators."
)
_PRIVATE = ("unit_sizes", "unit_rents", "haystack")


def _summary(x: queries.ListingSummary) -> dict:
    return {k: v for k, v in asdict(x).items() if k not in _PRIVATE}


def _conds(page: str, raw: list[str]) -> list[F.Cond]:
    return [F.parse_one(s, views.FIELDS[page]) for s in raw]  # FilterError explains a bad one to the caller


def _event(e: queries.EventView) -> dict:
    d = e.detail or {}
    out = {"at": e.at, "type": e.type, "source": e.source, "key": e.key, "title": e.title or e.summary,
           "verdict": e.verdict, "first_run": e.baseline}  # fmt: skip
    if e.type == "changed":
        out["changes"] = digest._changes(d)
        out["noise"] = bool(d.get("noise"))
    elif e.type == "page_changed":
        out["added"], out["new_links"] = d.get("added", [])[:15], d.get("new_links", [])
    elif e.type in ("source_failed", "source_recovered"):
        out["error"] = d.get("error")
    return out


def build(engine: Engine, p: Profile = PROFILE, auth=None):
    from fastmcp import FastMCP

    mcp = FastMCP(
        "housing-watch",
        instructions=(
            "NYC affordable-housing listings scraped hourly from ~48 sources, judged against one applicant's "
            f"profile (household {p.household}, income ${p.income:,}, sizes {', '.join(sorted(p.sizes))}). "
            "verdict is eligible / possible / excluded with a reason; commute is transit minutes to work; "
            "application is the stage from the applicant's tracker sheet. " + GRAMMAR
        ),
        auth=auth,
    )

    @mcp.tool(annotations=READ)
    def search(query: str) -> dict:
        """Search listings by building name, address, neighborhood, borough or notes. Returns ids for `fetch`."""
        res = queries.search_listings(engine, queries.ListingQuery([], query, "verdict", 0, 20), p)
        return {"results": [{"id": x.key, "title": f"{x.title} ({x.verdict})", "url": x.url} for x in res.items]}

    @mcp.tool(annotations=READ)
    def fetch(id: str) -> dict:
        """One listing by id (a `search` result or listing key): units, verdict, commute, tracker stage, history."""
        d = queries.listing_detail(engine, id, p)
        if d is None:
            raise ValueError(f"no listing {id!r}")
        body = asdict(d) | {"summary": _summary(d.summary), "events": [_event(e) for e in d.events]}
        return {"id": id, "title": d.summary.title, "url": d.summary.url,
                "text": json.dumps(body, default=str), "metadata": {"verdict": d.summary.verdict}}  # fmt: skip

    @mcp.tool(annotations=READ)
    def find_listings(
        filters: Annotated[list[str], Field(description=GRAMMAR)] = [],  # noqa: B006
        q: Annotated[str, Field(description="free-text search")] = "",
        sort: Annotated[str, Field(description="verdict, rent, commute, last_changed, first_seen, ...")] = "",
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        offset: int = 0,
    ) -> dict:
        """Listings matching filters, one row per building/opportunity. Removed listings are hidden unless
        a filter asks for them."""
        lq = queries.ListingQuery(_conds("listings", filters), q, sort or views.DEFAULT_SORT["listings"], offset, limit)
        res = queries.search_listings(engine, lq, p)
        return {"items": [_summary(x) for x in res.items], "total": res.total, "next_offset": res.next_offset,
                "verdict_counts": res.verdict_counts}  # fmt: skip

    @mcp.tool(annotations=READ)
    def find_units(
        filters: Annotated[list[str], Field(description=GRAMMAR)] = [],  # noqa: B006
        q: str = "",
        sort: str = "",
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        offset: int = 0,
    ) -> dict:
        """Individual units or rent tiers (size, rent, AMI, income band) with their listing and verdict."""
        uq = queries.ListingQuery(_conds("units", filters), q, sort or views.DEFAULT_SORT["units"], offset, limit)
        res = queries.search_units(engine, uq, p)
        items = [{k: v for k, v in asdict(u).items() if k != "haystack"} for u in res.items]
        return {"items": items, "total": res.total, "next_offset": res.next_offset}

    @mcp.tool(annotations=READ)
    def recent_changes(
        filters: Annotated[
            list[str],
            Field(
                description="event filters, e.g. `type.in.changed,new`, `at.last.2d`; "
                "listing filters such as `verdict.in.eligible` also apply"
            ),
        ] = [],  # noqa: B006
        include_noise: Annotated[bool, Field(description="status flaps to/from 'unknown'")] = False,
        before: Annotated[int | None, Field(description="next_before from the previous page")] = None,
    ) -> dict:
        """What happened, newest first: new, changed (with what changed), closed, removed, reopened listings,
        watched pages that changed, and sources failing or recovering."""
        page = queries.events(engine, _conds("events", filters), before=before, limit=100, p=p)
        items = [_event(e) for e in page.items]
        return {"items": [e for e in items if include_noise or not e.get("noise")], "total": page.total,
                "next_before": page.next_before}  # fmt: skip

    @mcp.tool(annotations=READ)
    def overview() -> dict:
        """Counts by verdict and status, eligible listings open now, recent activity, failing sources, and the
        digest email that would go out next."""
        o = queries.overview(engine, p)
        built = digest.build(engine, p)
        return asdict(o) | {
            "eligible_now": [_summary(x) for x in o.eligible_now],
            "recent": [_event(e) for e in o.recent],
            "next_digest": built[1] if built and built[0] else None,
        }

    @mcp.tool(annotations=READ)
    def sources() -> list:
        """Every source: last success, failures, listing counts, schedule."""
        return [asdict(x) for x in queries.sources(engine, p)]

    @mcp.tool(annotations=READ)
    def filter_fields(page: Annotated[str, Field(description="listings, units or events")] = "listings") -> list:
        """The fields filters can use on a page, with type, operators and allowed values."""
        return [{"name": f.name, "label": f.label, "type": f.type, "ops": list(F.OPS[f.type]),
                 "choices": list(f.choices)} for f in views.FIELDS[page].values()]  # fmt: skip

    return mcp


def auth_from_env():
    """The OIDC proxy from HW_MCP_* settings; raises when any is missing, so the server never runs open."""
    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    need = ("HW_MCP_BASE_URL", "HW_MCP_OIDC_CONFIG_URL", "HW_MCP_CLIENT_ID", "HW_MCP_CLIENT_SECRET", "HW_MCP_JWT_KEY")
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"mcp: set {', '.join(missing)}")
    return OIDCProxy(
        config_url=os.environ["HW_MCP_OIDC_CONFIG_URL"],
        client_id=os.environ["HW_MCP_CLIENT_ID"],
        client_secret=os.environ["HW_MCP_CLIENT_SECRET"],
        jwt_signing_key=os.environ["HW_MCP_JWT_KEY"],
        base_url=os.environ["HW_MCP_BASE_URL"],
        audience=os.environ["HW_MCP_CLIENT_ID"],  # the upstream access token is for this client, not any
        forward_resource=False,  # the upstream issues tokens for its client, not for the MCP resource URL
        required_scopes=["openid"],
        valid_scopes=["openid", "email", "profile", "offline_access"],
        extra_authorize_params={"scope": "openid email profile offline_access"},  # offline_access: refresh tokens
    )


def app(engine: Engine, p: Profile = PROFILE):
    return build(engine, p, auth_from_env()).http_app(path="/mcp")
