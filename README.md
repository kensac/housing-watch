# housing-watch

A deterministic watcher for NYC affordable-housing listings. It polls about 50
sources on their own schedules: Housing Connect, HPD's re-rental agents, HDC,
HCR, Mitchell-Lama, Pronto, SANEL, Reside, MGNY, and others. It keeps every
listing and its change history in Postgres, then emails a digest of what is new
or has changed. A small fleet of workers on different hosts shares the fetching
through a Postgres job queue ([Procrastinate](https://procrastinate.readthedocs.io/)).
It uses no AI. Adapters parse each source's own
JSON, HTML tables or flyer PDFs (`pdftotext`, plus `tesseract` for image-only
tables), so the same input always gives the same output.

## How it works

- `src/housing_watch/adapters/` has one module per source. Each one returns
  `Listing`s (units, rents, income bands) or `Snapshot`s, which are watched
  pages with no structure.
- `db.py` stores everything and records `new` / `changed` / `closed` / `gone` /
  `reopened` / `page_changed` events. It stores raw data only; nothing is
  filtered at storage time. A source's first run is flagged as a baseline.
- `tasks.py` is the fleet: a per-minute `tick` queues due sources, any worker
  runs them, and big sources split into chunks that run on several workers.
- `criteria.py` labels each listing **eligible**, **possible** or **excluded**
  when it is read, and gives a reason. Anything it cannot rule out counts as
  possible and is still reported.
- `applications.py` pre-fills paper application PDFs that are a known form (today HPD's HC2.0
  application, used by Tax Solute, iAfford, AFNY and others) from `HW_APPLICANT_FILE`. The UI
  generates the filled PDF on request and stores nothing.
- `digest.py` sends a digest at `HW_DIGEST_HOURS`. When a new eligible unit is
  open, it sends right away instead.

`docs/ARCHITECTURE.md` is the full design.

## Configuration (environment)

| Var | Meaning |
|---|---|
| `HW_DATABASE_URL` | `postgresql+psycopg://user:pass@host:port/db` (required except for `show`) |
| `HW_INCOME` | annual household income (required for `serve`, `digest`, `show`) |
| `HW_HOUSEHOLD` | household size (default 1) |
| `HW_SIZES` | unit sizes wanted, e.g. `studio,1br` |
| `HW_EXCLUDED_BOROUGHS` | e.g. `Bronx` |
| `HW_WORKER_NAME` | this worker's name in logs and `runs.workers` (default: hostname) |
| `HW_WORKER_CONCURRENCY` | jobs a worker runs at once (default 4); politeness is per host regardless |
| `HW_WORKER_CAPS` | extra queues: `byparr` (has `BYPARR_URL`), `digest` (has profile + SMTP) |
| `HW_WEB_HOST`, `HW_WEB_PORT` | `serve` also runs the web UI when the port is set (default host `0.0.0.0`) |
| `HW_METRICS_PORT` | Prometheus exporter (default 9091; `0` disables) |
| `HW_DIGEST_TO`, `HW_DIGEST_FROM`, `HW_DIGEST_HOURS` | digest delivery (hours in container local time) |
| `HW_RUN_TIMEOUT_HOURS` | an unfinished run older than this is failed as abandoned (default 6) |
| `HW_HOST_GAP` | minimum seconds between requests to one host (default 0.5) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` | STARTTLS SMTP |
| `HW_APPLICANT_FILE` | applicant JSON for pre-filling known paper application forms (UI only; schema: `tests/fixtures/applications/applicant.example.json`). No SSN; signatures and dates are left blank |
| `BYPARR_URL` | FlareSolverr-compatible renderer for JS/Cloudflare pages |

## Running

```bash
uv sync
make check                                   # ruff + tests; set HW_TEST_DATABASE_URL for the Postgres ones
HW_INCOME=90000 make show S=pronto           # what one adapter sees now; stores nothing
python -m housing_watch migrate              # alembic upgrade head
python -m housing_watch run --source pronto  # one run now, inline, no queue
python -m housing_watch digest --dry-run
python -m housing_watch worker               # a fleet worker
python -m housing_watch serve                # migrate + worker + web: the image's default
```

The image is `ghcr.io/kensac/housing-watch` (amd64 and arm64), built on every
push to `main`. Its entrypoint is `python -m housing_watch`, with `serve` as the
default command.
