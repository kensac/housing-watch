# Store and events

The schema and write path are described in
[ARCHITECTURE.md](../ARCHITECTURE.md#store-postgres-sqlalchemy-20-alembic).

## Rules

- **Never delete stored data.** Listings, snapshots, events and runs stay. A listing a source stops publishing gets `gone_since` and a
  `gone` event; it comes back as `reopened`. When a listing changes,
  `record_listings` rewrites its units, and the `changed` event keeps the old
  and new values in `detail.before` / `detail.after`. No migration or script
  drops or prunes these rows.
- The deletes that exist are scratch or owner-controlled, and are the only
  ones: `chunk_results` rows once their run is recorded or failed
  (`db.clear_chunks`); a run handed to another queue before it started
  (`db.discard_run`); saved views, manual tracker links, listing overrides and the profile row
  the owner deletes; finished Procrastinate jobs
  (`fleet.tasks.cleanup`: successes after 2 days, failures after a week). Add a new
  one only with the owner's agreement.
- **Change detection lives only in `db.py`.** Event types are
  `models.EventType`. Do not emit events anywhere else.
- **Store raw facts, no verdicts.** Eligibility is `eligibility/` at read time,
  so a profile change re-judges everything without a write. The one exception
  to raw facts is an owner's override: `record_listings` stores raw + patch,
  and `listing_overrides.raw` keeps the source's values.
- **One run, one transaction.** `fleet.runner.record` writes listings, snapshots,
  cache, `runs` and `source_state` together. `db.py` functions take a
  `Session` and never commit.
- A source's first successful run is a baseline: its events carry
  `detail.baseline = true`. Keep that when you touch `record_*`.
- `mapping.py` is the only `models` to `orm` translation.

## Migrations

- Alembic owns all DDL, Procrastinate's tables included. Add
  `alembic/versions/NNNN_<name>.py` with the next number as `revision`.
- `tests/test_schema.py::test_migrations_match_models` fails when `orm.py` and
  the migrations disagree. `test_migrations_downgrade_and_upgrade_again` needs
  a working `downgrade()`.
- A Procrastinate upgrade that ships SQL migrations needs a revision that
  applies them; `test_procrastinate_schema_is_current` fails until it exists.
- Every worker runs `alembic upgrade head` at start under an advisory lock.
  Hosts are pinned to images independently (see [fleet.md](fleet.md)), so
  workers on the previous image keep running against the migrated schema.
  Write migrations the previous image can live with.
