# Architecture

housing-watch is four layers. Each depends only on the layers above it, so they can change independently.

```
adapters/  parse.py  fetch.py      SOURCES   what does each source list right now?  -> models.Result
criteria.py                        RULES     is a unit eligible for the profile?    (pure, read time)
orm.py  db.py  alembic/            STORE     raw facts + change events in Postgres  (no verdicts)
tasks.py  worker                   FLEET     who fetches what, when, from which IP  (Procrastinate)
queries.py  digest.py  web/        READ      what changed, what fits                (UI, email)
```

## Sources (adapters)

- One module per source in `adapters/`. An adapter is pure input → output: it fetches through
  `fetch.py` and returns `models.Result` (`Listing`s with units and bands, or `Snapshot`s of
  unstructured pages). It never touches the database, the queue or criteria.
- `fetch.py` is the only network access. It enforces politeness per site and per process: at most
  one request in flight to a given host from one worker, a minimum gap between requests, and one byparr
  render at a time. Threads inside a worker therefore never multiply the rate one site sees from one IP.
- Big sources can opt into chunking (`chunks()`, `fetch_chunk()`, `merge_cache()` on `Adapter`, plus
  `parallel`). The default is a single chunk, so an adapter that doesn't opt in is untouched.
- An adapter that calls `fetch.rendered()` needs byparr, which only hetzner has. Its tasks go to the
  `byparr` queue (see Fleet).

## Rules (criteria)

`criteria.listing_verdict(listing, profile)` → eligible / possible / excluded with a reason. Pure
function of a `models.Listing` and the `HW_*` profile; never stored, so changing the profile re-judges
everything.

## Store (Postgres, SQLAlchemy 2.0, Alembic)

- `orm.py` is the schema: `listings → units → bands`, `snapshots`, `events`, `runs`, `source_state`,
  `adapter_cache`. Migrations in `alembic/versions/`, applied with `alembic upgrade head` at startup
  (advisory-locked, so any worker can run it).
- `db.py` is the write path and the only code that maps `models` ↔ `orm`: `record_listings`,
  `record_snapshots`, `load_listing`, run and source-state bookkeeping, cache load/save. Change
  detection (fingerprints → `new` / `changed` / `closed` / `gone` / `reopened` / `page_changed`,
  `source_failed` / `source_recovered`) lives here and only here.
- One database: `housing-watch-db` on hetzner, reachable over the tailnet. `HW_DATABASE_URL`
  (`postgresql+psycopg://...`) everywhere.

## Fleet (Procrastinate)

The job queue is [Procrastinate](https://procrastinate.readthedocs.io/) on the same Postgres: it
provides `SKIP LOCKED` claiming, LISTEN/NOTIFY, periodic tasks, `queueing_lock` dedupe, retries and
stalled-job recovery. Its tables (`procrastinate_*`) are not in `orm.py`.

- **Workers** are the same image on every host: `python -m housing_watch worker`. `HW_WORKER_NAME`
  names the worker (the host). Each worker runs a few threads (`HW_WORKER_CONCURRENCY`); politeness
  is in `fetch.py`, so that is safe.
- **Queues:** `sources` (any worker) and `byparr` (only workers with `HW_WORKER_CAPS=byparr`, i.e.
  hetzner). A task that hits `fetch.rendered()` on a worker without byparr fails fast with a typed
  error and is re-queued on `byparr`.
- **Tasks:**
  - `tick` (periodic, every minute, every worker; dedupe via `queueing_lock`): enqueues `source_run`
    for each source whose `source_state.next_due` has passed, and `digest` at the digest slots or when
    an urgent event is pending.
  - `source_run(source)` (`queueing_lock=source`): plans chunks. With one chunk it fetches and
    records inline. Otherwise it defers `source_chunk` jobs, at most `parallel` at a time, collects
    their results, then merges, records and saves the cache.
  - `source_chunk(source, key)`: fetches one chunk and returns the serialized `Result` plus its cache delta.
  - `digest()`: builds and sends the email (lock `digest`, so only one at a time).
- **Health:** a failed run increments `source_state.consecutive_failures` and emits `source_failed` on
  the 2nd and 10th consecutive failure. Workers expose Prometheus metrics on `:9091` (bound to their
  tailnet IP).

## Read (queries, digest, web)

- `queries.py` is the read API, returning plain dicts/dataclasses: listing search with filters,
  listing detail with units/bands/events, the per-source health table, event feed, overview counts.
  It attaches verdicts by calling `criteria` at read time.
- `digest.py` builds the email from `queries.py`.
- `web/` is the UI: FastAPI + Jinja2 templates + HTMX, with hand-written CSS (no build step). It is
  served by the hetzner worker container (`python -m housing_watch serve` = worker + web) on the
  tailnet only. It is read-only.

## Tests

- Adapter tests are offline against `tests/fixtures/`.
- Store, fleet and web tests need Postgres: they use the `pg` fixture (tests/conftest.py), which runs
  migrations into a throwaway database at `HW_TEST_DATABASE_URL` and is skipped when that is unset.
  CI provides it as a service container.
