# Working on this project

Instructions for any coding agent working in this repository. Read this file
first, then the topic document covering what you are about to do.

These are instructions, not history. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
is the design; the topic documents below are the rules for changing it.

## Non-negotiable

1. **The production database is read-only.** No INSERT, UPDATE, DELETE, DDL or
   migration against `housing-watch-db` unless the owner authorises that
   specific operation. A `run`, `migrate`, `worker` or `serve` pointed at it
   writes. So does any write tool or `graphql_mutation` on the Housing MCP
   server.
2. **Never attribute an AI anywhere.** No tool or model name, no "generated
   with", no `Co-Authored-By` trailer for one: not in commits, PR titles or
   bodies, code, comments or docs. See [working-agreement.md](docs/agents/working-agreement.md).
3. **Deterministic, no AI in the pipeline.** Adapters parse a source's own
   JSON, HTML, PDF text or OCR. The same input gives the same output.
4. **Never delete stored data.** A listing that disappears is marked `gone`,
   not removed. See [store.md](docs/agents/store.md) for the scratch tables
   that are the only exceptions.
5. **Adapters fetch only through `fetch.py`.** It is where politeness lives.
   See [adapters.md](docs/agents/adapters.md).

## Topic documents

| Read this | Before you |
|---|---|
| [working-agreement.md](docs/agents/working-agreement.md) | Take a task, open a PR, or write a doc |
| [adapters.md](docs/agents/adapters.md) | Add or fix a source |
| [store.md](docs/agents/store.md) | Touch `db.py`, `orm.py`, events, or a migration |
| [fleet.md](docs/agents/fleet.md) | Change a task or queue, or claim anything is deployed |
| [web-and-mcp.md](docs/agents/web-and-mcp.md) | Change the GraphQL schema, the UI, or the MCP server |
| [testing.md](docs/agents/testing.md) | Write a test or trust a green run |

## Repository map

- `src/housing_watch/adapters/`: one module per source. Each registers an
  `Adapter` subclass on import.
- `src/housing_watch/fetch.py`: all network access: HTTP, byparr renders,
  the headless browser, PDF text, OCR.
- `src/housing_watch/criteria.py`: the verdict, computed at read time.
- `src/housing_watch/models.py`, `orm.py`, `mapping.py`, `db.py`: domain
  types, the schema, the one translation between them, and the write path
  with change detection.
- `src/housing_watch/fleet/`: the Procrastinate app and tasks (`tasks.py`),
  one run in one transaction (`runner.py`), the exporter (`metrics.py`).
  `fleet/__init__.py` is its public surface.
- `src/housing_watch/queries.py`, `filters.py`, `views.py`, `changes.py`,
  `digest.py`: the read model, the filter registry, saved views, change lines,
  the email.
- `src/housing_watch/tracker.py`, `commute.py`, `applications.py`, `ami.py`,
  `parse.py`: the tracker sheet sync, transit commutes, application PDF
  filling, HUD charts, shared parsing helpers.
- `src/housing_watch/web/`: FastAPI app and Strawberry schema.
- `src/housing_watch/mcp_server.py`, `mcp_models.py`: the MCP server.
- `src/housing_watch/__main__.py`: the CLI (`serve`, `worker`, `migrate`,
  `run`, `digest`, `list`, `show`, `schema`).
- `frontend/`: the React + Mantine UI. `src/gql/` in it is generated.
- `alembic/`: all DDL, Procrastinate's included.
- `tests/`: offline adapter tests on `tests/fixtures/<source>/`, and Postgres
  tests through the `pg` fixture.
- `deploy/Dockerfile`: the one image every host runs.

## Commands

- `make check`: ruff lint and format, the schema check, the Python tests.
  Set `HW_TEST_DATABASE_URL` or the Postgres tests skip.
- `make show S=<source>`: what one adapter returns now. Needs `HW_INCOME`.
  Stores nothing.
- `python -m housing_watch schema`: rewrite `schema.graphql`.
- `cd frontend && npm ci && npm run codegen && npm run lint && npm run build`:
  CI's `web` job.
