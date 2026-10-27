# Architecture

housing-watch is four layers. Each depends only on the layers above it, so they can change independently.

```
adapters/  parse.py  fetch.py      SOURCES   what does each source list right now?  -> models.Result
criteria.py                        RULES     is a unit eligible for the profile?    (pure, read time)
orm.py  db.py  alembic/            STORE     raw facts + change events in Postgres  (no verdicts)
tasks.py  worker                   FLEET     who fetches what, when, from which IP  (Procrastinate)
queries.py  digest.py  web/ frontend/  READ      what changed, what fits                (UI, email)
```

## Sources (adapters)

- One module per source in `adapters/`. An adapter is pure input → output: it fetches through
  `fetch.py` and returns `models.Result` (`Listing`s with units and bands, or `Snapshot`s of
  unstructured pages). It never touches the database, the queue or criteria.
- `fetch.py` is the only network access. It enforces politeness per site and per process: at most
  one request in flight to a given host from one worker (a lock per host), a minimum gap between
  request starts (`HW_HOST_GAP`, 0.5 s), and one byparr render at a time. Threads inside a worker
  therefore never multiply the rate one site sees from one IP.
- Big sources can opt into chunking (`chunks()`, `fetch_chunk()`, `merge_cache()` on `Adapter`, plus
  `parallel`). The default is a single chunk, so an adapter that doesn't opt in is untouched.
- An adapter that calls `fetch.rendered()` needs byparr, which only hetzner has. Its tasks go to the
  `byparr` queue (see Fleet).
- Chunk caches: each chunk gets a copy of the planned cache and hands back the top-level keys it
  changed; the default `merge_cache()` applies those in chunk order. An adapter whose chunks write
  into one shared nested key must override `merge_cache()`.
- Paged sources (Reside, SANEL; `parallel = 4`, one in-flight request per worker IP) read their index
  in `chunks()`, deal only the pages due for a fetch over up to `parallel` chunks of at least
  `CHUNK_LEAST` (25) with `adapters.spread()`, and deal the cached rest over the same chunks, so the
  merged Result is still the whole source. The plan travels in `cache[PLAN]` (never saved). Every page,
  and every SANEL official document (`doc:<key>`), is its own top-level cache key, so chunk deltas
  never overlap. A cold Reside run is 4 chunks of ~530 pages; a run with few changed pages is one chunk,
  recorded inline.
- Reside's unit pages are often stale (no status label at all, or "Open Market" on a unit nobody can
  apply for), so its application form decides: `chunks()` reads the form's "Add unit" picker with a
  headless browser (`fetch.browser()`, Playwright; `NeedsByparr` sends planning to hetzner) into
  `cache["_form"]`, and every chunk marks a unit open if the form offers it, else `unknown` (verdict
  possible). A failed read keeps the last list for 12 h, then statuses fall back to the page labels.
  A label matches by address and unit number, or by rent on a page naming no unit (some labels name a
  size: "Apt 1 BR"); on a multi-unit page the units whose rents the form no longer lists are taken.

## Rules (criteria)

`criteria.listing_verdict(listing, profile)` → eligible / possible / excluded with a reason. Pure
function of a `models.Listing` and the `HW_*` profile; never stored, so changing the profile re-judges
everything.

## Store (Postgres, SQLAlchemy 2.0, Alembic)

- `orm.py` is the schema: `listings → units → bands`, `snapshots`, `events`, `runs`, `source_state`,
  `adapter_cache`, `chunk_results`, `fleet_workers`. Alembic owns all DDL, Procrastinate's included (revision 0002 runs
  its `SchemaManager.get_schema()`). Workers run `alembic upgrade head` at start under a Postgres
  advisory lock, so any worker can migrate. A Procrastinate upgrade that ships SQL migrations needs a
  new revision that applies them; `tests/test_fleet.py::test_procrastinate_schema_is_current` fails
  until it exists.
- `mapping.py` is the only `models` ↔ `orm` translation. `db.py` is the write path: `record_listings`,
  `record_snapshots`, `load_listing`, run and source-state bookkeeping, cache load/save, chunk results.
  Change detection (fingerprints → `new` / `changed` (detail `before`/`after`) / `closed` / `gone` /
  `reopened`, `watching` / `page_changed`, `source_failed` / `source_recovered`) lives here and only here.
- Events from a source's first successful run carry `detail.baseline = true`: they describe what was
  already listed, not a change, and the digest collapses them.
- `runner.py` records one run in one transaction: listings, snapshots, cache, `runs` and `source_state`
  land together or not at all.
- One database: `housing-watch-db` on hetzner, reachable over the tailnet. `HW_DATABASE_URL`
  (`postgresql+psycopg://...`) everywhere.

## Fleet (Procrastinate)

The job queue is [Procrastinate](https://procrastinate.readthedocs.io/) on the same Postgres: it
provides `SKIP LOCKED` claiming, LISTEN/NOTIFY, periodic tasks, `queueing_lock` dedupe, `lock`
serialization, retries and worker heartbeats. Its tables (`procrastinate_*`) are not in `orm.py`.

- **Workers** are the same image on every host: `python -m housing_watch worker`. `HW_WORKER_NAME`
  names the worker (the host); each worker upserts itself into `fleet_workers` every 30s, because
  Procrastinate's own workers table has no name. Each worker runs `HW_WORKER_CONCURRENCY` threads; politeness is in
  `fetch.py`, so that is safe.
- **Queues:** `sources` (every worker), `byparr` (workers whose `HW_WORKER_CAPS` include `byparr`, i.e.
  hetzner) and `digest` (cap `digest`: the worker with the profile and SMTP credentials, also hetzner).
  A task that hits `fetch.rendered()` on a worker without `BYPARR_URL` raises `fetch.NeedsByparr`; the
  retry strategy moves the same job to `byparr`, and `source_state.queue` remembers it so later runs
  start there.
- **Tasks:**
  - `tick` (periodic, every minute; Procrastinate defers it once per minute fleet-wide): claims sources
    whose `source_state.next_due` has passed (pushing it a full cycle ahead) and defers `source_run` for
    each; fails runs left unfinished for `HW_RUN_TIMEOUT_HOURS` (6); re-queues merges that were never
    deferred; defers `digest(scheduled=True)` at minute 0 of each `HW_DIGEST_HOURS` hour.
  - `source_run(source)` (`queueing_lock` `run:<source>`, `lock` `source:<source>`): plans chunks with
    `Adapter.chunks()`. With one chunk it fetches and records inline. Otherwise it writes the plan
    (keys plus the cache every chunk starts from) to `runs.plan` and defers one `source_chunk` per key;
    chunk *i* takes `lock` `<source>#<i % parallel>`, so at most `parallel` are in flight.
  - `source_chunk(source, key, run_id)`: fetches one chunk and writes its serialized `Result` and cache
    delta (top-level keys it changed) to `chunk_results`. Procrastinate keeps no return values, hence
    the table. The chunk that completes the set defers `source_merge`. A chunk that fails its last retry
    writes a failed row, which fails the run: a partial source is never recorded, so `gone` stays right.
  - `source_merge(source, run_id)`: concatenates the chunks' Results in plan order, folds their deltas
    with `Adapter.merge_cache()`, records, saves the cache and deletes the chunk rows. No job ever waits
    on another, so chunked runs cannot starve the worker slots their own chunks need.
  - `digest(scheduled)` (`lock` `digest`): the scheduled email, or, queued after any run that recorded
    events, an urgency check (`digest.urgent`) that mails at once when an eligible unit opened.
  - `retry_stalled` (every 5 min): jobs of workers that stopped heartbeating go back to `todo`.
    `cleanup` (daily): drops finished jobs (successes after 2 days, failures after a week).
- **Failures:** an adapter error is a failed run (recorded, not retried); Procrastinate retries only
  infrastructure trouble and chunk errors. A failed run increments `source_state.consecutive_failures`
  and emits `source_failed` on the 2nd and 10th consecutive failure; the next success emits
  `source_recovered`.
- **Metrics** (`metrics.py`, `:9091`, `HW_METRICS_PORT`): `hw_source_run_seconds` and
  `hw_source_failures_total` are per worker; `hw_jobs{task,status}`,
  `hw_source_last_success_timestamp_seconds` and `hw_source_consecutive_failures` are read from the
  database at scrape time, so every worker reports the same values (aggregate with `max`).

## Read (queries, digest, web)

- `queries.py` is the read API, returning plain dicts/dataclasses: listing search with filters,
  unit search (one row per unit), listing detail with units/bands/events, the per-source health table,
  event feed, overview counts.
  It attaches verdicts by calling `criteria` at read time.
  Every listing is judged once per store version and cached in memory, so pages filter in Python.
- `changes.py` turns a `changed` event's `before`/`after` into lines (status, deadline, units relabeled,
  added or removed, rent, availability, income limits) and flags noise: a status moving to "unknown", or
  back from it to what it was, with nothing else changed. `db.py` stores both in the event's detail.
- `mcp_server.py` exposes `queries.py` over MCP (streamable HTTP, a thread of `serve` on `HW_MCP_PORT`),
  with FastMCP's OIDC proxy for sign-in (clients register themselves; the upstream decides who).
  Every tool's parameters and result are typed (`mcp_models.py`), so its JSON Schema lists the filter fields,
  operators and enum values; enums there are the data's own strings ("1br"), not GraphQL names. A saved view
  comes back with its state typed by page, in the shape `create_view` takes. Schemas go out with their
  `$defs` (`dereference_schemas=False`): expanded, the filter schema repeated in every place it is used
  (325 KB of tools/list; now under 150 KB, which a test bounds). An optional field is not required rather
  than nullable, and unset fields are left out of results.
  Its write tools are the web app's mutations (saved views; manual tracker links), annotated as writes. Both
  are thin adapters over the same domain functions (`views.create/change/duplicate/delete`, `tracker.link/
  unlink`, `queries.tracker_link`), which raise `models.NotFound`, `views.ViewError` or `filters.FilterError`;
  GraphQL maps those to codes in `Schema.process_errors`, MCP to tool errors in its `tool` wrapper. Each MCP
  write is logged at INFO (`mcp write <tool> by <sub> <email>: <args>`, args as keys or lengths, never values
  of objects); reads are not. `graphql` runs read-only queries in-process against the web schema (same
  DataLoaders and limits); `graphql_mutation` runs mutations in a `trusted` context: OAuth already decided
  who may call it, so they skip the browser's same-origin check. `context(trusted=)` is keyword-only and the
  web app's /graphql never passes it (a test holds that).
- `digest.py` builds the email from `queries.py`: `send(engine, dry_run)` and `urgent(engine)` (an open,
  eligible listing has a pending new/reopened/changed event). Events with `detail.baseline = true` come from
  a source's first run; they collapse into one summary line and only their eligible listings are listed.
- `filters.py` is the generic filter layer: fields are read off the listings/units/bands (and events)
  columns in `orm.py`, so a new column is filterable with no other change (a column with
  `info={"values": <Literal alias>}` is an enum field). A filter is a JSON tree with a key per field holding
  its operators, all of which must hold: `{"borough": {"in": ["Queens"]}, "units": {"rent": {"le": 3000},
  "bands": {"household": {"eq": 1}}}}`; events nest the listing's fields under `listing`. The tree becomes
  typed conditions (`Cond`), then SQL. The same tree is the GraphQL input (`web/filter_types.py` derives
  `ListingFilterInput`/`EventFilterInput` and their output twins from the registry), the MCP tools' parameter
  (`mcp_models.py` derives pydantic models from it) and what a saved view stores. Unit conditions must hold
  on one unit, band conditions on one band of it; verdict fields are applied in Python after the SQL
  prefilter. Sorts are registries too (`queries.LISTING_SORTS`, `UNIT_SORTS`: label, key, natural direction),
  applied in order so later fields break ties.
- `web/app.py` serves GraphQL at `/graphql` (Strawberry; the schema is `web/schema.py`, snake_case fields,
  GraphiQL on GET) over `queries.py`, `views.py` and `tracker.py`, plus one REST route,
  `/api/listing/application`, for the filled application PDF (a binary download).
  - Types: one source of truth, the read model's dataclasses. `web/derive.py` turns each into a Strawberry
    type (nested dataclasses, optionals, lists; a field's trailing comment is its description), so a field
    added to a dataclass is in the API with no other change. Fields made with `queries.internal()`
    (`haystack`, `unit_sizes`, `unit_rents`) stay out. The DTOs, not the tables, are the source because
    verdicts, commutes and tracker stages are computed at read time. Closed value sets are `type X =
    Literal[...]` aliases in the domain (Borough, Size, Kind, PublishedStatus, Status, Verdict, EventType,
    StageGroup, Match, Health, JobStatus, Page), and derive() makes each one GraphQL enum; a value that is not
    a GraphQL name gets one ("your move" is `your_move`, "1br" is `_1br`, "Staten Island" is
    `Staten_Island`). The `Key`/`Serial` aliases are `ID`; a deadline is a `Date`. Open text stays String:
    titles, addresses, notes, set-asides, sources (history keeps retired ones), application stages (unknown
    sheet statuses are kept as written), queue and task names. An event's detail is the `EventDetail` union
    (ListingChange, PageChange, SourceFailure); a failed job's `args` is `JobArgs`, every parameter a task
    takes. Only envelopes (connections, inputs, payloads, meta) are declared in `schema.py`; filter, sort and
    column types in `web/filter_types.py`.
  - `schema.graphql` is the committed SDL (`python -m housing_watch schema`); a test and CI fail when it
    is stale.
  - Lists are Relay connections (`edges { cursor node }`, `page_info`, `first` ≤ 500, opaque `after`
    cursors: a position for listings and units, an event id for events), carrying `total`, `filters` (each
    condition in words, by field and operator), and for listings and units `verdict_counts` and
    `hidden_gone`. A filter that is well-typed but meaningless (an empty `in`, blank `contains`) is refused
    with BAD_USER_INPUT. `values` and the views mutations take a page's input as a `@oneOf` object; a saved
    view's state comes back as the `ViewState` union of the three pages' states.
  - Errors carry `extensions.code`: NOT_FOUND, BAD_USER_INPUT (also every document or variable error),
    FORBIDDEN (a cross-origin mutation), INTERNAL (a bug, the only kind logged). Resolvers raise the domain's
    own errors; `Schema.process_errors` gives them their code.
  - Depth (10), token (2000) and alias (15) limits; introspection stays on. Resolvers are async and run
    the blocking store reads in a worker thread; `Event.listing` goes through a per-request DataLoader.
    Mutations take an `input` and return a payload.
  `frontend/` is the UI, React + Mantine (default theme) + TanStack Query, built by the image into
  `web/dist/`, which the same app serves for every other path. Its documents are written with GraphQL Code
  Generator's `graphql()` (client preset, from `schema.graphql`, into the committed `src/gql/`), so each is a
  TypedDocumentNode sent by `api.ts`'s `request()`; shared shapes are fragments (`ListingFields`, ...).
  List state (filter, search, sort, columns,
  saved view, open listing) lives in the app's URL (`filter` as the JSON object, `sort=rent.desc,title.asc`,
  `cols`); `filters.ts` checks what it reads against the page's fields from `meta` before typing it as the
  generated input, and a link whose filter does not check out opens unfiltered with a notice. A typecheck
  fails if the saved-view fragments miss a field of a filter type. Lists scroll in batches of 200. Three list
  pages: Listings (a row per listing; its rent and AMI ranges cover the units that fit and match the unit
  filters), Units (a row per unit, so rent sorts exactly; unit and band filters hold on that unit) and
  Events. It is served by the hetzner worker container (`python -m housing_watch serve` = migrate +
  worker + web, the web started only when `HW_WEB_PORT` is set) on the tailnet only. The tables it
  writes are `saved_views` (`views.py`: version 2 JSON, the filter tree + sort pairs + columns per page,
  re-validated against the registries on load; alembic 0010 converted version 1's `field.op.values` strings) and manual tracker links, through mutations that refuse anything not same-origin.
  Nothing is seeded.
- `commute.py` routes eligible, listed listings to `HW_COMMUTE_TO` by transit (Google Routes API, weekday
  8:30 AM), each origin address once per destination (again after 60 days), into `commutes`; a periodic
  `commute` task on the digest worker does it. queries.py attaches the minutes and lines at read time
  (`commute` filter field, sort and column).
- `tracker.py` copies the owner's application tracker sheet (the source of truth for application status
  and notes) into `tracker_rows` every 5 minutes, read-only via a service account, and matches rows to
  listings at read time: the row's own source link (only when that link is one row's and one listing's),
  else a normalized street address, else the building name; a manual link in `tracker_links` wins.
  A Status is read as one of the dropdown's nine stages (`tracker.STAGES`, each your move / their move /
  done; legacy statuses mapped, unknown ones kept as written). The sheet holds only the current Status, so
  each sync appends a `tracker_events` row when a row's Status differs from its last event; a row's first
  event is a baseline when it predates tracking, so the UI says "since <date>" rather than inventing a start.
  The UI shows stage, whose move and days in stage on listings and units (`application` and
  `application_group` filter fields, `application` column sorted by stage order) and the stage history in
  the drawer; the Tracker page lists rows by stage, longest waiting first, and links unmatched ones.
- `applications.py` recognises known paper application forms by their fillable field names (today HPD's
  HC2.0 application) and fills them from the applicant file (`HW_APPLICANT_FILE`, private, host-only;
  signs and dates the applicant's line; never the SSN). The drawer checks a listing's linked PDFs lazily; a download is built
  per request and never stored.
- `scripts/load_sqlite_dev.py` (dev only) copies an old SQLite store into Postgres to run the UI on real data.

## Tests

- Adapter tests are offline against `tests/fixtures/`.
- Store, fleet and web tests need Postgres: they use the `pg` fixture (tests/conftest.py), which runs
  migrations into a throwaway database at `HW_TEST_DATABASE_URL` and is skipped when that is unset.
  CI provides it as a service container.
