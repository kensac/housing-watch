# Fleet and deployment

How tasks, queues and chunks work is in
[ARCHITECTURE.md](../ARCHITECTURE.md#fleet-procrastinate). This is what to
keep true when changing them, and how code reaches the hosts.

## Rules for tasks

- **No job waits on another.** A chunked run defers its chunks and the last
  chunk to finish defers the merge. Waiting would let a run starve the worker
  slots its own chunks need.
- **A partial source is never recorded.** A chunk that fails its last retry
  fails the run, because recording part of a source would mark the rest
  `gone`.
- **Adapter errors are failed runs, not retries.** Procrastinate retries only
  infrastructure trouble and chunk errors (`fleet/tasks.py`:
  `RUN_RETRY`, `CHUNK_RETRY`).
- **Concurrency is safe because politeness is in `fetch.py`.** Do not add
  per-task rate limits; `HW_WORKER_CONCURRENCY` threads share one per-host
  slot.
- Queues are `sources` (every worker), `byparr` and `digest` (only workers
  whose `HW_WORKER_CAPS` names them). Anything needing the profile, SMTP,
  Google keys or a browser runs on a capped queue.
- `tests/test_fleet.py` drives real Procrastinate workers against Postgres.
  Extend it when you change task flow.

## Hosts

One image everywhere, one Postgres. The authority is the compose files in
`homelab-config` (`hetzner-vps-config/compose.housing-watch.yml`, and the
`housing-watch-worker` service in `oci-vps-config` and
`transmission-vps-config`), with a runbook at `docs/housing/README.md` there.
At the time of writing:

| Host | Command | Caps |
|---|---|---|
| hetzner | `serve` (worker, web, MCP), next to `housing-watch-db` | `byparr,digest` |
| oci | `worker` | none |
| transmission | `worker` | none |

## Deploying

1. A push to `main` builds `ghcr.io/kensac/housing-watch` for amd64 and arm64
   (`.github/workflows/build-push-ghcr.yml`), tagged `latest` and
   `sha-<short sha>`, with `HW_REVISION` set to the commit.
2. Each host's compose file pins the image by digest. Renovate in
   `homelab-config` moves those pins, and the host redeploys when that lands.
3. On start, each container migrates and then works.

Merging is not deploying. Hosts move independently, so two hosts can run
different commits.

## Telling what is live

- The digest pinned for each host in `homelab-config` on its `main`.
- On a host: `docker exec housing-watch printenv HW_REVISION` (the worker
  containers are `housing-watch-worker`).
- `runs.workers` and `fleet_workers` show which hosts are working, not which
  commit they run.
