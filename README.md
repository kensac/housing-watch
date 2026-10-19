# housing-watch

A deterministic watcher for NYC affordable-housing listings. It polls about 50
sources on their own schedules: Housing Connect, HPD's re-rental agents, HDC,
HCR, Mitchell-Lama, Pronto, SANEL, Reside, MGNY, and others. It keeps every
listing and its change history in SQLite, then emails a digest of what is new
or has changed. It has no UI and uses no AI. Adapters parse each source's own
JSON, HTML tables or flyer PDFs (`pdftotext`, plus `tesseract` for image-only
tables), so the same input always gives the same output.

## How it works

- `src/housing_watch/adapters/` has one module per source. Each one returns
  `Listing`s (units, rents, income bands) or `Snapshot`s, which are watched
  pages with no structure.
- `db.py` stores everything and records `new` / `changed` / `closed` / `gone` /
  `reopened` / `page_changed` events. It stores raw data only; nothing is
  filtered at storage time.
- `criteria.py` labels each listing **eligible**, **possible** or **excluded**
  when it is read, and gives a reason. Anything it cannot rule out counts as
  possible and is still reported.
- `digest.py` sends a digest at `HW_DIGEST_HOURS`. When a new eligible unit is
  open, it sends right away instead.

## Configuration (environment)

| Var | Meaning |
|---|---|
| `HW_INCOME` | annual household income (required) |
| `HW_HOUSEHOLD` | household size (default 1) |
| `HW_SIZES` | unit sizes wanted, e.g. `studio,1br` |
| `HW_EXCLUDED_BOROUGHS` | e.g. `Bronx` |
| `HW_DB` | SQLite path (default `/data/housing-watch.db`) |
| `HW_DIGEST_TO`, `HW_DIGEST_FROM`, `HW_DIGEST_HOURS` | digest delivery |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` | STARTTLS SMTP |
| `BYPARR_URL` | FlareSolverr-compatible renderer for JS/Cloudflare pages |

## Running

```bash
uv sync
make check                                    # ruff + offline tests (fixtures only)
HW_INCOME=90000 make show S=pronto            # what one adapter sees now; stores nothing
HW_INCOME=90000 python -m housing_watch run   # run due adapters into the DB
python -m housing_watch digest --dry-run
python -m housing_watch loop                  # the container's entrypoint
```

The image is `ghcr.io/kensac/housing-watch`, built on every push to `main`.
