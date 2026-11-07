# Web and MCP

The design of both surfaces is in
[ARCHITECTURE.md](../ARCHITECTURE.md#read-queries-notify-web).

## Rules

- **One domain, two thin surfaces.** GraphQL (`web/schema.py`) and MCP
  (`mcp_server.py`) both call `queries/`, `views.py` and `tracker.py`. A new
  capability goes in the domain function first, then both surfaces. Domain
  refusals raise `models.NotFound`, `views.ViewError` or `filters.FilterError`;
  any other exception is a bug and its text goes only to the log.
- **The owner's writes, and only these.** `saved_views`, manual
  `tracker_links`, `listing_overrides`, `profile`, `source_state.paused` /
  `next_due` (`control.py`), and the tracker sheet's Status, Notes and new rows.
  Add another written table only with the owner's agreement.
- **Web auth.** The app has no login: it is reached only through Traefik at
  `housing.kanishksachdev.com`, behind authentik forward-auth set up in
  `homelab-config`. No port is published to the tailnet. Mutations are refused
  unless the request is same-origin (`web/schema.same_origin`). The web
  `/graphql` never passes `trusted`; a test holds that. GraphQL over WebSocket
  is refused because WebSockets skip CORS.
- **Host allowlist.** The web app and the MCP server refuse with 421 any
  request whose `Host` is not in `HW_ALLOWED_HOSTS` (`web/hosts.py`), so DNS
  rebinding cannot use the browser against them. An entry matches the header
  exactly, port included as sent; `host:*` takes any port. The default is
  `housing.kanishksachdev.com,localhost:*,127.0.0.1:*`. A new public name or
  a call by container name needs an entry, or it gets 421. The metrics port is
  not guarded: it serves counters, not user data.
- **MCP auth.** OAuth through FastMCP's OIDC proxy. It refuses to start when
  any `HW_MCP_*` setting is missing, so there is no open mode. Its `graphql`
  tool runs queries only and `graphql_mutation` mutations only. Each write is
  logged with its caller.
- **Types come from the read model.** A field added to a `queries/`
  dataclass appears in the API through `web/derive.py`; a new `orm.py` column
  becomes a filter field through `filters/fields.py`. Do not hand-write a Strawberry
  type for a DTO.
- `tests/test_mcp.py` holds `tools/list` under 165 KB. Keep schemas sent with
  their `$defs`.

## After changing a dataclass, a column, a filter or the schema

```bash
python -m housing_watch schema           # rewrite schema.graphql
cd frontend && npm run codegen           # rewrite src/gql/
```

Commit both. CI fails when `schema.graphql` is stale, when `src/gql/` is
stale, or when a frontend document does not validate against the schema.

## The UI

`frontend/` is React + Mantine + TanStack Query. Documents are written with
`graphql()` from the generated `src/gql/`; never edit `src/gql/` by hand.
`npm run dev` proxies `/graphql` and `/api` to `HW_API`, by default a local
`serve` on `127.0.0.1:8095` (`HW_WEB_PORT=8095`). `frontend/vite.config.ts`
refuses the production address (`100.79.4.41`). Do not reach production
another way: a view saved or a tracker row linked from the dev server writes it.
