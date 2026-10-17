# F-CLOUD — Hosted, multi-user deployment

_Proposed 2026-10-14 after the deployment audit. Status: approved 2026-10-14 as a plan; the
implementation is not approved and should not start before F-DATADIR, F-DESKTOP and a cost
decision._

Depends on [F-DATADIR](F-DATADIR-portable-data-dir.md). Sibling track:
[F-DESKTOP](F-DESKTOP-standalone-app.md).

## Goal

Several people use the app at once over the public internet, each seeing only their own regions,
models and exports, on an instance whose disk, cost and failure modes are bounded.

Estimated effort: two to three months. The blocker is not dependency weight — it is that the
application is single-tenant and stateful on the local filesystem.

## What exists today (measured 2026-10-14)

- **No concept of a user.** There is no authentication, no session principal and no owner column.
  `app/server/core/db.py::DB_PATH` is one SQLite file (WAL, opened per call) holding everyone's
  coordinates; `coordinates.json` and `region_settings.json` are single global files. Any visitor
  can read and overwrite anyone's work.
- **State is the local filesystem.** The 12 GB array cache, the 1.9 GB `output/` tree, the skyline
  run directories and the report roots in `app/server/routers/reports.py::_ROOTS` are all local
  paths. The app cannot be scaled to two instances, and losing the disk loses the data.
- **Disk is unbounded.** Roughly 9 GB of the cache sits outside `geo2stl/cache.py::NAMESPACE_TTL`
  and `output/` has no policy at all. On a metered volume this is the main cost risk. F-DATADIR
  fixes it; without that fix hosting is not viable.
- **Long jobs run in the web process.** City builds and region PDFs take minutes and run through
  `run_in_executor` inside the server. A request-scoped executor on a shared instance means one
  user's build starves everyone; the client already polls for progress, so a queue is a backend
  change only.
- **GPU coordination is a lock file.** `city2stl/resources.py` takes a machine-wide lock at
  `~/.cache/map2stl_gpu.lock`. That coordinates processes on one machine and does nothing across
  containers.
- **There is one server-side GUI call** — the tkinter folder picker behind the Edit panel. It must be
  removed: on a server it either fails or blocks a worker.
- **The tile-store path is client-settable and absolute.** That is the one genuine filesystem-scope
  risk found; it must become a server-side allowlist. Otherwise the audit found no path traversal
  (all four file-serving roots resolve, contain and allowlist extensions) and no `subprocess` or
  `eval` anywhere under `app/server/`.
- **`/api/diagnostics` sums the cache tree with an uncached `rglob`** — on a 12 GB tree that is a
  denial-of-service vector as well as slow.
- **About 25 external services** are called, several keyed and some paid, with hard limits —
  OpenTopography allows 50 calls per 24 hours per key. A shared server key means one user can
  exhaust it for everyone.
- **CPU-only hosting works.** The server starts torch-free, and the cloud session-start hook already
  builds a no-torch virtual environment. SegFormer on CPU measures roughly 18 times slower. Image
  size is about 2.2 GB CPU-only against about 3.5 GB with CUDA, plus roughly 400 MB of first-use
  model downloads that should be baked into the image.

## Approach

Four phases. Each is independently shippable and the order matters.

1. **Phase 1 — make it stateless and bounded.** F-DATADIR first, in full. Then move the data
   directory onto one persistent volume and run exactly one instance; remove the tkinter picker;
   replace the client-settable tile-store path with a server-side allowlist; cache the diagnostics
   size sum. At the end of this phase the app is hostable for a single trusted group behind an
   access gate, with known disk behaviour.
2. **Phase 2 — identity and tenancy.** Add authentication (an OIDC provider, not a hand-rolled
   password store), thread a principal through every request, and give every stored row and written
   artifact an owner. Migrate SQLite to Postgres — not for throughput but because the data must
   outlive the instance and support a real owner column with constraints. The two global JSON files
   become per-user rows. Every file-serving route filters by owner as well as by root. This phase is
   the bulk of the work and is where the audit's single-tenancy finding actually gets paid off.
3. **Phase 3 — move the work off the web process.** A job queue (Redis with RQ, or Celery) and
   separate worker containers for city builds, region PDFs and skyline runs. The existing polling
   endpoints become queue status reads. Artifacts go to object storage rather than a local
   `output/`, with signed URLs for download. Worker concurrency becomes the throughput knob, and the
   GPU lock file is replaced by a queue with one GPU worker — or dropped entirely if the deployment
   is CPU-only.
4. **Phase 4 — quotas, keys and cost control.** Per-user quotas on builds, exported bytes and
   external calls, enforced before the call is made. Server-held keys per service with per-user
   accounting, so an OpenTopography limit is hit by one user's budget and not by the server's. Cache
   byte ceiling tuned to the volume. Alerts on volume usage and on external-service error rates.

Start CPU-only. Revisit GPU workers only if measured demand justifies them; an 18-fold slowdown on
one model is a worse problem on paper than in practice, because the model runs on a minority of
requests.

## Target files

- Everything in F-DATADIR's target list, as a prerequisite
- `app/server/auth/` (new), `app/server/deps.py` (new) — principal, dependency injection
- `app/server/core/db.py` — Postgres, owner columns, migrations (Alembic)
- `app/server/routers/*` — owner filtering on every read and write
- `app/server/core/tile_store.py` — allowlisted tile sources, no client-supplied absolute path
- `app/server/routers/diagnostics.py` — cached size accounting
- `app/server/core/jobs/` (new) — queue client, task definitions, worker entry point
- `app/server/core/storage.py` (new) — object storage for artifacts, signed URLs
- `city2stl/resources.py` — GPU coordination becomes a queue concern
- `deploy/` (new) — Dockerfile (CPU-only base), compose file, volume and secret declarations
- The Edit panel components that call the folder picker

## Success criteria

1. Two users signed in at once cannot see or overwrite each other's regions, models or exports —
   asserted by tests, not by inspection.
2. The web container can be killed and restarted mid-build without losing the build or the user's
   data.
3. Disk use on the volume is bounded and observable: a report shows the budget, the split and the
   last prune.
4. No route accepts a filesystem path from the client.
5. A city build runs on a worker, not in the web process; the web process stays responsive during
   it.
6. One user exhausting their external-service quota does not affect another user.
7. The CPU-only image builds, starts with no GPU present, and completes a city export.

## Risks

- **The tenancy phase touches every router.** It cannot be done incrementally per route without a
  window where some data is protected and some is not. Gate the whole phase behind the access gate
  from phase 1 and do not open the instance publicly until phase 2 is complete.
- **SQLite to Postgres is a real migration,** including the WAL-era assumptions in per-call
  `sqlite3.connect`. Do it with Alembic from the start rather than hand-written DDL.
- **Paid external services.** Google Maps and Street View bill per request. Without phase 4 quotas,
  a public instance is an open invoice. Phase 4 is not optional for a public deployment.
- **Cost is dominated by the volume and the workers,** not by the web process. A 50 GB volume plus
  one idle worker is the floor; estimate before committing.
- **Scope.** This plan is large enough to stall the rest of the roadmap. Phase 1 is the part that
  also improves the desktop product; phases 2 to 4 only pay off if hosting is actually wanted.

## Decisions (lift into docs/decisions/ when done)

- Hosting is CPU-only to start; GPU workers are a later, measured decision.
- Phase 1 (stateless, bounded, no server-side GUI, no client paths) ships regardless, because it
  also improves the standalone product.
- Multi-tenancy uses an external identity provider and a real database with owner columns; no
  hand-rolled auth and no per-user SQLite files.
- Long jobs move to a queue with separate workers rather than growing the web process's executor.
- A public instance requires per-user quotas on paid external services.
