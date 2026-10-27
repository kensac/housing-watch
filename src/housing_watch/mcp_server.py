"""MCP server over the store, for agents and chat apps (Claude, ChatGPT): every read and write the web app has.

Streamable HTTP at HW_MCP_BASE_URL + /mcp. Sign-in is OAuth: FastMCP's OIDC proxy lets MCP clients register
themselves (DCR), then sends the user to the upstream OIDC provider (HW_MCP_OIDC_CONFIG_URL), which decides
who may sign in. It never starts without that: there is no unauthenticated mode. Client registrations and
upstream tokens are kept encrypted under FASTMCP_HOME; losing them means reconnecting, nothing else.

`search` and `fetch` have the shapes ChatGPT's connectors and deep research expect; the other tools take typed
filters (the JSON tree of filters.py, as pydantic models in mcp_models.py) and return the same fields as the web
API, minus the applicant's PDFs; every tool's parameters and output have a JSON Schema, sent with its `$defs` (not
expanded, which would repeat the filter schema in every place it is used). The write tools are the web app's
mutations, thin adapters over the same views.py / tracker.py functions: saved views (create, change, duplicate,
delete) and manual tracker links (link, unlink). Each write is logged at INFO with its caller (the OAuth subject).
`graphql` runs read-only queries in-process against the web app's own schema (web/schema.py, `schema.graphql`);
`graphql_mutation` runs mutations, with a context marked trusted (OAuth already decided who may call it), so they
skip the browser's same-origin check. The web app's /graphql never sets it.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
from dataclasses import asdict
from typing import Annotated

from pydantic import Field
from sqlalchemy.engine import Engine

from . import digest, queries, tracker, views
from . import filters as F
from . import mcp_models as M
from .criteria import PROFILE, Profile
from .models import NotFound

log = logging.getLogger("housing_watch")
REFUSALS = (NotFound, views.ViewError, F.FilterError)  # the domain's refusals: the caller's to fix, not bugs
READ = {"readOnlyHint": True, "openWorldHint": False}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
DELETE = {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
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


def caller() -> str:
    """Who is calling, for the audit log: the OAuth subject and email, nothing else."""
    from fastmcp.server.dependencies import get_access_token

    t = get_access_token()
    if t is None:
        return "anonymous"
    c = t.claims or {}
    who, email = c.get("sub") or t.client_id, c.get("email") or c.get("preferred_username")
    return f"{who} <{email}>" if email else str(who)


def brief(v) -> str:
    """An argument in a few characters: objects by their keys, lists by their length."""
    if hasattr(v, "model_dump"):
        v = v.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(v, dict):
        return "{" + ",".join(v) + "}"
    if isinstance(v, list):
        return f"[{len(v)}]"
    r = repr(v)
    return r if len(r) <= 80 else r[:77] + "..."


def _errors(out) -> str:
    """A GraphQL result's error codes, for the audit log."""
    errs = out.get("errors") if isinstance(out, dict) else None
    return f" errors: {','.join(str((e.get('extensions') or {}).get('code')) for e in errs)}" if errs else ""


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

    def tool(annotations: dict, args=None, more: str = ""):
        """mcp.tool, with the domain's refusals as clean tool errors, and every write logged with its caller.
        `args` summarizes the arguments for the log (default: brief() of each); `more` extends the docstring."""

        def deco(fn):
            sig, writes = inspect.signature(fn), not annotations.get("readOnlyHint")

            def audit(kw: dict, outcome: str) -> None:
                if writes:
                    bound = sig.bind(**kw).arguments
                    said = args(bound) if args else " ".join(f"{k}={brief(v)}" for k, v in bound.items())
                    log.info("mcp write %s by %s: %s%s", fn.__name__, caller(), said, outcome)

            def refused(kw: dict, e: Exception):
                audit(kw, f" refused: {e}")
                return ToolError(str(e), log_level=logging.INFO)  # the caller's to fix: not an error here

            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def run(**kw):
                    try:
                        out = await fn(**kw)
                    except REFUSALS as e:
                        raise refused(kw, e) from None
                    audit(kw, _errors(out))
                    return out
            else:

                @functools.wraps(fn)
                def run(**kw):
                    try:
                        out = fn(**kw)
                    except REFUSALS as e:
                        raise refused(kw, e) from None
                    audit(kw, _errors(out))
                    return out

            doc = " ".join(x for x in (inspect.getdoc(fn), more) if x)
            return mcp.tool(annotations=annotations, description=doc)(run)

        return deco

    mcp = FastMCP(
        "housing-watch",
        instructions=(
            "NYC affordable-housing listings scraped hourly from ~48 sources, judged against one applicant's "
            f"profile (household {p.household}, income ${p.income:,}, sizes {', '.join(sorted(p.sizes))}). "
            "verdict is eligible / possible / excluded with a reason; commute is transit minutes to work; "
            "application is the stage from the applicant's tracker sheet. Read with search/fetch, find_*, "
            "recent_changes, overview, saved_views, tracker_board or `graphql` (queries only); write with the view and "
            "tracker tools or `graphql_mutation`. " + FILTERS
        ),
        auth=auth,
        dereference_schemas=False,  # $defs once per tool; expanded, the filter schema repeats in every use
    )

    @tool(READ)
    def search(query: str) -> M.SearchResults:
        """Search listings by building name, address, neighborhood, borough or notes. Returns ids for `fetch`."""
        res = queries.search_listings(engine, queries.ListingQuery([], query, offset=0, limit=20), p)
        return M.SearchResults(results=[M.SearchHit(id=x.key, title=f"{x.title} ({x.verdict})", url=x.url)
                                        for x in res.items])  # fmt: skip

    @tool(READ)
    def fetch(id: str) -> M.Document:
        """One listing by id (a `search` result or listing key): units, verdict, commute, tracker stage, history."""
        d = queries.listing_detail(engine, id, p)
        if d is None:
            raise ValueError(f"no listing {id!r}")
        body = asdict(d) | {"summary": _summary(d.summary).model_dump(mode="json"),
                            "events": [_event(e).model_dump(mode="json") for e in d.events]}  # fmt: skip
        return M.Document(id=id, title=d.summary.title, url=d.summary.url, text=json.dumps(body, default=str),
                          metadata=M.FetchMetadata(verdict=d.summary.verdict))  # fmt: skip

    @tool(READ)
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

    @tool(READ)
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

    @tool(READ)
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

    @tool(READ)
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

    @tool(READ)
    def sources() -> list[M.SourceModel]:
        """Every source: last success, failures, listing counts, schedule."""
        return [M.SourceModel.model_validate(asdict(x)) for x in queries.sources(engine, p)]

    @tool(READ)
    def filter_fields(page: views.Page = "listings") -> list[M.FieldInfo]:
        """The fields filters can use on a page, with their path in the filter, type, operators and values."""
        return [M.FieldInfo(name=f.name, path=list(f.path), label=f.label, group=f.group, type=f.type,
                            ops=list(F.OPS[f.type]), choices=list(f.choices))
                for f in views.FIELDS[page].values()]  # fmt: skip

    @tool(READ)
    def saved_views() -> list[M.ViewModel]:
        """Every saved view (named filter, search, sort and columns of a page) with its current match count."""
        return [M.ViewModel.of(v) for v in views.with_counts(engine, p)]

    @tool(READ)
    def tracker_board() -> M.TrackerModel:
        """Every row of the applicant's tracker sheet, by stage: its stage history, cells and matched listings.
        `row_key` is what `link_tracker` takes."""
        return M.TrackerModel.model_validate(asdict(queries.tracker_board(engine, p)))

    @tool(WRITE)
    def create_view(
        name: Annotated[str, Field(description=f"1 to {views.MAX_NAME} characters")],
        state: Annotated[M.ViewState, Field(description="Its page is the view's page. " + FILTERS)],
        is_default: Annotated[bool, Field(description="the page opens on it")] = False,
    ) -> M.ViewModel:
        """Save a view of a page."""
        return M.ViewModel.of(views.create(engine, state.state(), name, is_default, p))

    @tool(WRITE)
    def change_view(
        id: int,
        name: str | None = None,
        state: Annotated[M.ViewState | None, Field(description="for the view's own page; replaces it whole")] = None,
        is_default: bool | None = None,
        move: Annotated[views.Move | None, Field(description="one place among the page's views")] = None,
    ) -> M.ViewModel:
        """Rename a view, replace its state, set or clear it as its page's default, or move it one place."""
        v = views.change(engine, id, name=name, state=state.state() if state else None, is_default=is_default,
                         move=views.MOVES[move] if move else 0, p=p)  # fmt: skip
        return M.ViewModel.of(v)

    @tool(WRITE)
    def duplicate_view(id: int) -> M.ViewModel:
        """A copy of a view, named "<name> (copy)" (the name shortened to fit)."""
        return M.ViewModel.of(views.duplicate(engine, id, p))

    @tool(DELETE)
    def delete_view(id: int) -> M.Deleted:
        """Delete a saved view."""
        views.delete(engine, id)
        return M.Deleted(id=id)

    @tool(WRITE | {"idempotentHint": True})
    def link_tracker(
        listing_key: str,
        row_key: Annotated[str | None, Field(description="a `tracker_board` row; null: the listing is in no row")],
    ) -> M.TrackerLinkModel:
        """Link a listing to a tracker sheet row by hand, overriding automatic matching, or mark it as in no row."""
        tracker.link(engine, listing_key, row_key)
        return M.TrackerLinkModel.model_validate(asdict(queries.tracker_link(engine, listing_key, p)))

    @tool(WRITE | {"idempotentHint": True})
    def unlink_tracker(listing_key: str) -> M.TrackerLinkModel:
        """Drop a manual tracker link: the listing goes back to automatic matching."""
        tracker.unlink(engine, listing_key)
        return M.TrackerLinkModel.model_validate(asdict(queries.tracker_link(engine, listing_key, p)))

    def _parse(query: str):
        """(the document, None), or (None, the syntax error as a result)."""
        try:
            doc = parse(query)
        except GraphQLSyntaxError as e:
            return None, {"data": None, "errors": [e.formatted]}
        return doc, None

    def _only(doc, allowed: OperationType, other: str) -> None:
        ops = {d.operation for d in doc.definitions if isinstance(d, OperationDefinitionNode)}
        if ops - {allowed}:
            raise ToolError(f"this tool runs {allowed.value} operations only; {other}", log_level=logging.INFO)

    GQL = (
        "Lists are Relay connections: `{ listings(filter: { verdict: { in: [eligible] }, units: { rent: { le: 3000 } } "
        "}, first: 20) { total edges { node { key title } } page_info { has_next_page end_cursor } } }`, the next page "
        "with `after: <end_cursor>`. Enum values are GraphQL names (`Staten_Island`, `_1br`, `re_rental`). The result "
        "is free-form: whatever the query selects. Errors carry `extensions.code`."
    )

    @tool(READ, more=GQL)
    async def graphql(query: str, variables: dict | None = None) -> dict:
        """Run a read-only GraphQL query against the web app's schema (mutations: `graphql_mutation`). Introspect
        it first: `{ __schema { queryType { fields { name description } } } }`."""
        doc, bad = _parse(query)
        if bad:
            return bad
        _only(doc, OperationType.QUERY, "use graphql_mutation for mutations")
        res = await schema.execute(query, variables, context_value=context(engine, p),
                                   allowed_operation_types={Op.QUERY})  # fmt: skip
        return {"data": res.data, "errors": [e.formatted for e in res.errors or []]}

    def _fields(bound: dict) -> str:
        doc, _ = _parse(bound["query"])
        names = [f.name.value for d in (doc.definitions if doc else []) if isinstance(d, OperationDefinitionNode)
                 for f in d.selection_set.selections if hasattr(f, "name")]  # fmt: skip
        return f"mutations={','.join(names) or '?'} variables={brief(bound.get('variables') or {})}"

    @tool(DELETE, args=_fields, more=GQL)
    async def graphql_mutation(query: str, variables: dict | None = None) -> dict:
        """Run a GraphQL mutation against the web app's schema: createView, changeView, duplicateView, deleteView,
        linkTracker, unlinkTracker (the same writes as the other tools). Introspect it with `graphql`:
        `{ __schema { mutationType { fields { name description } } } }`."""
        doc, bad = _parse(query)
        if bad:
            return bad
        _only(doc, OperationType.MUTATION, "use graphql for queries")
        res = await schema.execute(query, variables, context_value=context(engine, p, trusted=True),
                                   allowed_operation_types={Op.MUTATION})  # fmt: skip
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
