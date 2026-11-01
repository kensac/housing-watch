# Adapters

An adapter answers one question: what does this source list right now? The
design, including chunking and the Reside and SANEL specifics, is in
[ARCHITECTURE.md](../ARCHITECTURE.md#sources-adapters).

## Rules

- **Pure input to output.** An adapter returns a `models.Result` of
  `Listing`s or `Snapshot`s. It never touches the database, the queue or
  `criteria`. Store every listing the source publishes; the verdict is applied
  at read time, never by dropping rows.
- **Network only through `fetch.py`** (`get`, `html`, `json`, `post_json`,
  `conditional`, `rendered`, `browser`, `pdf_text`). It enforces one request in
  flight per host per worker, a `HW_HOST_GAP` gap between request starts, and
  one render at a time. A direct `httpx` call skips all of that.
- **Deterministic.** No AI, no model calls, no randomness. Parse the source's
  own JSON, HTML, PDF text (`pdftotext`) or OCR (`tesseract`).
- **Fail loudly.** Raise on anything unexpected: a missing header, a count
  that does not match, a page shape you do not recognise. An empty `Result`
  is recorded as "the source lists nothing" and marks every stored listing of
  the source `gone`. Set `min_listings` when a source should never be near
  empty; `fleet.runner.record` refuses a smaller result.
- **Cadence.** `every_hours` defaults to 1 and may not exceed it
  (`test_every_source_runs_at_least_hourly`).
- **Work with an empty cache.** `self.cache` is scratch that saves refetches
  (`fetch.conditional`, `adapters.by_content`, `pdf_cached`). Use
  `adapters.prune` to drop entries for things the source no longer lists.
- **Rendering needs byparr.** `fetch.rendered()` and `fetch.browser()` raise
  `NeedsByparr` on a worker without `BYPARR_URL`; the fleet moves the job to
  hetzner. Prefer a plain request when the data is reachable without one.

## Adding or fixing one

1. Put the parsing in plain functions (`parse_index(html)`,
   `parse_detail(url, html)`) and keep `fetch()` thin around them. Most
   adapters already do; it is what makes them testable offline.
2. Save real responses under `tests/fixtures/<source>/` and test the parse
   functions against them in `tests/test_<source>.py`. Assert concrete units,
   rents and bands, and that a malformed page raises.
3. `HW_INCOME=150000 make show S=<source>` prints what the adapter returns from
   the live site, with verdicts, and stores nothing. It does send real requests
   to the source.
4. Never run `python -m housing_watch run --source ...` against the production
   database to try an adapter. That records a run.
