"""Read-only MCP server over the store, for agents and chat apps (Claude, ChatGPT).

Streamable HTTP at HW_MCP_BASE_URL + /mcp. Sign-in is OAuth: FastMCP's OIDC proxy lets MCP clients register
themselves (DCR), then sends the user to the upstream OIDC provider (HW_MCP_OIDC_CONFIG_URL), which decides
who may sign in. It never starts without that: there is no unauthenticated mode. Client registrations and
upstream tokens are kept encrypted under FASTMCP_HOME; losing them means reconnecting, nothing else.

`search` and `fetch` have the shapes ChatGPT's connectors and deep research expect; the other tools take typed
filters (the JSON tree of filters.py, as pydantic models in mcp_models.py) and return the same fields as the web
API, minus the applicant's PDFs; every tool's parameters and output have a JSON Schema. `graphql` runs a query
in-process against the web app's own schema (web/schema.py, `schema.graphql`); it refuses mutations.
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
from . import mcp_models as M
from .criteria import PROFILE, Profile

READ = {"readOnlyHint": True, "openWorldHint": False}
FILTERS = (
    "Filters are objects with a key per field holding its operators, all of which must hold, e.g. "
    '{"verdict": {"in": ["eligible"]}, "borough": {"in": ["Brooklyn", "Queens"]}, "units": {"rent": {"le": 3000}, '
    '"size": {"in": ["studio", "1br"]}}, "commute": {"le": 45}, "last_changed": {"last": {"amount": 7, "unit": '
    '"days"}}}. Unit conditions hold together on one unit. `filter_fields` lists every field and its operators.'
)


def _summary(x: queries.ListingSummary):
    return M.ListingModel.model_validate(queries.public(x))


def _conds(page: str, tree: dict) -> list[F.Cond]:
    return F.from_tree(tree, views.FIELDS[page])  # FilterError explains a bad one to the caller


def _event(e: queries.EventView) -> M.Change:
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
    return M.Change(**out)


def build(engine: Engine, p: Profile = PROFILE, auth=None):
    from fastmcp import FastMCP
    from fastmcp.exceptions import ToolError
    from graphql import GraphQLSyntaxError, OperationDefinitionNode, OperationType, parse
    from strawberry.types.graphql import OperationType as Op

    from .web.schema import context, schema

    mcp = FastMCP(
        "housing-watch",
        instructions=(
            "NYC affordable-housing listings scraped hourly from ~48 sources, judged against one applicant's "
            f"profile (household {p.household}, income ${p.income:,}, sizes {', '.join(sorted(p.sizes))}). "
            "verdict is eligible / possible / excluded with a reason; commute is transit minutes to work; "
            "application is the stage from the applicant's tracker sheet. " + FILTERS
        ),
        auth=auth,
    )

    @mcp.tool(annotations=READ)
    def search(query: str) -> M.SearchResults:
        """Search listings by building name, address, neighborhood, borough or notes. Returns ids for `fetch`."""
        res = queries.search_listings(engine, queries.ListingQuery([], query, offset=0, limit=20), p)
        return M.SearchResults(results=[M.SearchHit(id=x.key, title=f"{x.title} ({x.verdict})", url=x.url)
                                        for x in res.items])  # fmt: skip

    @mcp.tool(annotations=READ)
    def fetch(id: str) -> M.Document:
        """One listing by id (a `search` result or listing key): units, verdict, commute, tracker stage, history."""
        d = queries.listing_detail(engine, id, p)
        if d is None:
            raise ValueError(f"no listing {id!r}")
        body = asdict(d) | {"summary": _summary(d.summary).model_dump(mode="json"),
                            "events": [_event(e).model_dump(mode="json") for e in d.events]}  # fmt: skip
        return M.Document(id=id, title=d.summary.title, url=d.summary.url, text=json.dumps(body, default=str),
                          metadata=M.FetchMetadata(verdict=d.summary.verdict))  # fmt: skip

    @mcp.tool(annotations=READ)
    def find_listings(
        filter: Annotated[M.ListingFilter | None, Field(description=FILTERS)] = None,
        q: Annotated[str, Field(description="free-text search")] = "",
        sort: Annotated[
            list[M.ListingSort], Field(description="first decides, the rest break ties; empty: by verdict")
        ] = [],  # noqa: B006
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> M.ListingsPage:
        """Listings matching a filter, one row per building/opportunity. Removed listings are hidden unless
        a condition asks about removal (gone_since)."""
        lq = queries.ListingQuery(_conds("listings", M.tree(filter)), q, M.sorts("listings", sort), offset, limit)
        res = queries.search_listings(engine, lq, p)
        return M.ListingsPage(items=[_summary(x) for x in res.items], total=res.total, next_offset=res.next_offset,
                              verdict_counts=M.VerdictCounts(**res.verdict_counts))  # fmt: skip

    @mcp.tool(annotations=READ)
    def find_units(
        filter: Annotated[M.ListingFilter | None, Field(description=FILTERS)] = None,
        q: str = "",
        sort: list[M.UnitSort] = [],  # noqa: B006
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> M.UnitsPage:
        """Individual units or rent tiers (size, rent, AMI, income band) with their listing and verdict; unit
        conditions hold on the row's own unit."""
        uq = queries.ListingQuery(_conds("units", M.tree(filter)), q, M.sorts("units", sort), offset, limit)
        res = queries.search_units(engine, uq, p)
        return M.UnitsPage(items=[M.UnitModel.model_validate(queries.public(u)) for u in res.items],
                           total=res.total, next_offset=res.next_offset)  # fmt: skip

    @mcp.tool(annotations=READ)
    def recent_changes(
        filter: Annotated[
            M.EventFilter | None,
            Field(
                description='event conditions, e.g. {"type": {"in": ["changed", "new"]}, "at": {"last": {"amount": 2, '
                '"unit": "days"}}}; listing conditions go under "listing", e.g. {"listing": {"verdict": {"in": '
                '["eligible"]}}}'
            ),
        ] = None,  # fmt: skip
        include_noise: Annotated[bool, Field(description="status flaps to/from 'unknown'")] = False,
        before: Annotated[int | None, Field(description="next_before from the previous page")] = None,
    ) -> M.Changes:
        """What happened, newest first: new, changed (with what changed), closed, removed, reopened listings,
        watched pages that changed, and sources failing or recovering."""
        page = queries.events(engine, _conds("events", M.tree(filter)), before=before, limit=100, p=p)
        items = [_event(e) for e in page.items]
        return M.Changes(items=[e for e in items if include_noise or not e.noise], total=page.total,
                         next_before=page.next_before)  # fmt: skip

    @mcp.tool(annotations=READ)
    def overview() -> M.OverviewResult:
        """Counts by verdict and status, eligible listings open now, recent activity, failing sources, and the
        digest email that would go out next."""
        o = queries.overview(engine, p)
        built = digest.build(engine, p)
        return M.OverviewResult.model_validate(asdict(o) | {
            "eligible_now": [_summary(x) for x in o.eligible_now],
            "recent": [_event(e) for e in o.recent],
            "next_digest": built[1] if built and built[0] else None,
        })  # fmt: skip

    @mcp.tool(annotations=READ)
    def sources() -> list[M.SourceModel]:
        """Every source: last success, failures, listing counts, schedule."""
        return [M.SourceModel.model_validate(asdict(x)) for x in queries.sources(engine, p)]

    @mcp.tool(annotations=READ)
    def filter_fields(page: views.Page = "listings") -> list[M.FieldInfo]:
        """The fields filters can use on a page, with their path in the filter, type, operators and values."""
        return [M.FieldInfo(name=f.name, path=list(f.path), label=f.label, group=f.group, type=f.type,
                            ops=list(F.OPS[f.type]), choices=list(f.choices))
                for f in views.FIELDS[page].values()]  # fmt: skip

    @mcp.tool(annotations=READ)
    async def graphql(query: str, variables: dict | None = None) -> dict:
        """Run a read-only GraphQL query against the web app's schema; mutations are refused. Introspect it
        first: `{ __schema { queryType { fields { name description } } } }`. Lists are Relay connections:
        `{ listings(filter: { verdict: { in: [eligible] }, units: { rent: { le: 3000 } } }, first: 20) { total
        edges { node { key title } } page_info { has_next_page end_cursor } } }`, the next page with
        `after: <end_cursor>`. Enum values are GraphQL names (`Staten_Island`, `_1br`, `re_rental`). The result is
        free-form: whatever the query selects. Errors carry `extensions.code`."""
        try:
            doc = parse(query)
        except GraphQLSyntaxError as e:
            return {"data": None, "errors": [e.formatted]}
        if any(isinstance(d, OperationDefinitionNode) and d.operation != OperationType.QUERY for d in doc.definitions):
            raise ToolError("graphql tool is read-only")
        res = await schema.execute(query, variables, context_value=context(engine, p),
                                   allowed_operation_types={Op.QUERY})  # fmt: skip
        return {"data": res.data, "errors": [e.formatted for e in res.errors or []]}

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
