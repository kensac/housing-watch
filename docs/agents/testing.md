# Testing

## Two populations

- **Adapter tests** are offline. They read saved responses from
  `tests/fixtures/<source>/` and call the adapter's parse functions. No test
  sends a request to a live source.
- **Store, fleet, web and MCP tests** need Postgres. They use the `pg` fixture
  in `tests/conftest.py`, which creates a throwaway database at
  `HW_TEST_DATABASE_URL`, migrates it to head, empties every app table before
  each test, and drops the database after the session. With `-n auto` each
  xdist worker gets its own database.

**Without `HW_TEST_DATABASE_URL` the Postgres tests skip, and the run still
looks green.** A local run that skipped them verified only the adapters. Any
disposable Postgres 18 works:

```bash
docker run -d --name hw-test-pg -e POSTGRES_PASSWORD=pg -p 5432:5432 postgres:18-alpine
export HW_TEST_DATABASE_URL=postgresql+psycopg://postgres:pg@127.0.0.1:5432/postgres
```

Never point it at production. The fixture truncates tables.

## Other requirements

- `pdftotext` (poppler) for the PDF adapters, and `tesseract` for the OCR
  ones. The OCR tests skip without tesseract, which also looks green.
- `conftest.py` sets an example profile (`HW_INCOME=150000`, studio and 1BR,
  Bronx excluded) before `criteria` is imported. Tests assert verdicts against
  it.
- `tests/helpers.py` holds the shared `bands` and `T` helpers;
  `tests/seed.py` builds store rows for read-side tests. Reuse them.

## CI's gates

CI (`.github/workflows/ci.yml`) runs two jobs. Run both before pushing,
docs-only changes included:

- `check`: `make check` with `HW_TEST_DATABASE_URL` set (ruff lint, ruff
  format, `schema --check`, pytest).
- `web`: in `frontend/`, `npm ci`, `npm run codegen` with no diff in
  `src/gql/`, `npm run lint`, `npm run build`.
