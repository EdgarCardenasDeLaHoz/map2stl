# F-DATADIR — One data directory, one settings object, bounded disk

_Proposed 2026-10-14 after the deployment audit. Status: approved 2026-10-14. Not implemented._

Prerequisite for both [F-DESKTOP](F-DESKTOP-standalone-app.md) and
[F-CLOUD](F-CLOUD-hosted-multi-user.md), and worth shipping on its own: today a normal install
writes everything it produces into the repository folder and never deletes most of it.

## Goal

Every file the app writes at runtime lands under one configurable data directory that defaults
to the operating system's per-user application-data location, and every namespace under it has
an eviction policy. The repository folder becomes read-only at runtime, which is what a packaged
install and a container both require.

## What exists today (measured 2026-10-14, DESKTOP-TMJM22C)

### Five independent resolutions of the same repository root

`app/paths.py::REPO_ROOT` is meant to be the choke point, and that file holds nothing else. It is
not used by the writers. Each of these recomputes the root by counting `parents`:

| Resolution | Used for |
|---|---|
| `app/paths.py::REPO_ROOT` | the intended root; read by comparatively little |
| `app/server/config.py::_MAP2STL_DIR` | the JSON files, the OSM cache, the Earth Engine cache |
| `geo2stl/cache.py` (`_MAP2STL_DIR`, feeding `::CACHE_ROOT`) | the whole array cache |
| `app/server/routers/reports.py::_MAP2STL` | the report roots in `::_ROOTS` |
| `app/server/server.py` (`log_file`, module scope) | `server.log` |

A packaged install or a container changes the layout under every one of them at once, so each is
a separate break. Writers and their sizes here:

| Written path | Resolved by | Size here |
|---|---|---|
| `cache/` | `geo2stl/cache.py::CACHE_ROOT` — honours `MAP2STL_CACHE` / `STRM2STL_CACHE` | **12 GB** |
| `output/` | three independent `/ "output"` joins; there is no `OUTPUT_DIR` constant | **1.9 GB** |
| `output/usage/<date>.jsonl` | `app/server/routers/usage.py::usage_dir` — already env-overridable | small |
| `output/height_reports/` | `app/server/routers/reports.py::_ROOTS` | small |
| `output/<name>_puzzle.zip` | `app/session/terrain_session.py::TerrainSession.export_puzzle` | per build |
| `data.db` | `app/server/core/db.py::DB_PATH` — already honours `MAP2STL_DB_PATH` | 86 KB |
| `server.log` | `app/server/server.py`, module scope | small |
| `coordinates.json`, `region_settings.json` | `app/server/config.py::COORDINATES_PATH`, `::REGION_SETTINGS_PATH` | small |

Three of these — the cache, the database and the usage directory — **already** honour an
environment variable, so the pattern is proven and the work is mostly generalising it to the rest
and giving all of them one default. Two further roots default *outside* the repository, to
siblings of `Code/`: `app/server/config.py::MICROPOLITAN_STL_DIR` and `::H5_SRTM_ROOT`. No
packaged install or container will have those siblings; both are env-overridable, so neither is
fatal.

Two modules already do the whole thing correctly and are the pattern to copy:
`city2stl/height/providers/google_ob25d.py::cache_dir` (defaults to `~/.cache/ob25d`, honours
`CITY2STL_OB25D_DIR`, and its docstring states the reason — "outside OneDrive") and
`tools/align_tool/paths.py`, where every root is env-overridable.

### The cache is inside the synced folder, which is the reason for the placeholder code

`geo2stl/cache.py::_is_cloud_placeholder` reads `st_file_attributes` against OneDrive's
Files-On-Demand bits and `::read_array_cache` drops entries that are cloud-only, because the
MSIX-sandboxed app cannot trigger hydration. It no-ops off Windows, so it is not a blocker. But it
exists only because the 12 GB cache currently defaults *inside* a OneDrive-synced folder — exactly
what `google_ob25d.py::cache_dir` avoids. Moving the cache out makes this code dead weight.

### Settings are read ad hoc at import time

There is no `dotenv` and no `pydantic-settings`. `app/server/config.py` calls `os.environ.get` at
module scope, so a value is frozen at first import and cannot be changed without a restart. Only
`city2stl/skyline/_core/util.py::_load_env_file_if_present` reads `map2stl/.env`, which means the
server sees `GOOGLE_MAPS_API_KEY` only after some skyline module has been imported in the same
process.

`config.json` sits in the repository root, is git-ignored here but un-ignored by the parent
`Code/.gitignore` (`!config.json`), holds a plaintext OpenTopography key, and has no schema or
validation — a missing file degrades silently, which the module docstring of
`app/server/core/tile_store.py` describes as yielding "a perfectly flat map" rather than an error.

### Nothing outside the TTL table is ever deleted

`geo2stl/cache.py::prune_all_caches` iterates only the `::NAMESPACE_TTL` table (15 keys) and runs
once at startup. Measured against the 12 GB cache on this machine:

| Namespace on disk | Size | Covered by `NAMESPACE_TTL`? |
|---|---|---|
| `hydrorivers` | 4.6 GB | no — the table's nearest key is `hydrology`, a different directory |
| `opentopo` | 2.2 GB | yes — 90-day TTL and a 200-file cap, weak for GeoTIFFs this size |
| `ndsm` | 1.5 GB | no |
| `roof_tiles` | 392 MB | no |
| loose top-level `cache/*.json` digests | ~750 MB | no — outside any namespace directory, so `::prune_cache` never sees them |

So roughly 9 GB is outside any eviction policy, and `output/` (1.9 GB) has no policy at all. This
is a bug on a desktop install, not only a hosting cost: the app silently consumes the user's disk
for as long as it is used, and does it inside a synced folder.

## Approach

1. **`app/paths.py`: add `DATA_DIR`.** Resolution order: `MAP2STL_DATA_DIR`, then the platform
   per-user location (`%LOCALAPPDATA%\map2stl` on Windows, `$XDG_DATA_HOME/map2stl` else
   `~/.local/share/map2stl`), then — only when the repository is writable and a `.git` file or
   directory is present — the repository root, so a developer checkout behaves exactly as it does
   today. Keep `REPO_ROOT` for reading packaged assets.
2. **Collapse the five root resolutions to one** and route every writer through `DATA_DIR`:
   introduce the missing `OUTPUT_DIR`, and have `config.py`, `cache.py`, `reports.py` and
   `server.py` import from `app/paths.py` instead of counting `parents` themselves. `MAP2STL_CACHE`,
   `MAP2STL_DB_PATH`, the usage override, `MICROPOLITAN_STL_DIR` and `H5_SRTM_ROOT` keep their
   current names and semantics; only their fallbacks change, from repository-relative or sibling
   paths to `DATA_DIR` subdirectories.
3. **One settings object** (`app/server/settings.py`, new): a `pydantic-settings` `BaseSettings`
   read once at startup and passed in, layered env, then `.env`, then the per-PC config file, then
   defaults. Load `.env` in `app/server/server.py` at startup rather than depending on a skyline
   import having happened. Keep the existing environment variable names. Existing module-level
   constants in `app/server/config.py` become properties on the settings object, re-exported so no
   caller breaks in the same change.
4. **Migrate on first run**: if `DATA_DIR` is empty and the repository holds a `data.db`, `cache/`,
   `output/` or the two JSON files, move them and log one line per move. Never copy the 12 GB
   cache; either move it, or leave it and record the old root as a read-only secondary cache.
5. **Make eviction cover everything.** Add `hydrorivers`, `ndsm` and `roof_tiles` to
   `NAMESPACE_TTL` (and either rename `hydrology` to match the directory on disk or delete the
   unused key), tighten `opentopo` to a byte budget rather than a file count, and give
   `prune_cache` a sweep for the loose top-level digests. Add a total-bytes ceiling
   (`MAP2STL_CACHE_MAX_BYTES`, default around 8 GB) enforced least-recently-used across namespaces,
   and run the prune on a timer, not only at startup. Give `output/` a policy too: exports older
   than a configurable age are removed, and the deterministic `output/<name>_puzzle.zip` becomes a
   per-request temporary file, as the rest of `app/server/core/export.py` already does with
   `NamedTemporaryFile`.
6. **Report it.** `/api/diagnostics` already sums the cache tree; have it report the budget, the
   per-namespace split and when the last prune ran, so the user can see the policy working. (Its
   current unbounded `rglob` sum should be cached — see F-CLOUD, where it is also a denial-of-service
   vector.)
7. **Delete the OneDrive placeholder checks** (`geo2stl/cache.py::_is_cloud_placeholder` and its
   use in `::read_array_cache`) once the cache no longer defaults inside a synced folder.

### Boundary with F-KEYS

[F-KEYS](F-KEYS-keys-and-folders-panel.md) owns **credentials and machine folders** — the key
registry, the Earth Engine sign-in, and moving `ocean_root` and the `.h5` store into a per-PC
`~/.config/map2stl/machine.json` with migration. F-DATADIR owns **everything the app writes**:
cache, output, database, logs, usage, the two loose JSON files. The two meet at the settings object
in step 3, which should read F-KEYS's `machine.json` as one of its layers. If F-KEYS ships first,
F-DATADIR adopts its file; if F-DATADIR ships first, it creates the file and F-KEYS extends it.
Neither invents a second per-PC config. They touch the same files, so they run sequentially on one
machine, F-DATADIR first.

## Target files

- `app/paths.py` — `DATA_DIR`, `OUTPUT_DIR`
- `app/server/settings.py` (new), `app/server/config.py` — settings object; constants become
  properties; `_MAP2STL_DIR` goes away
- `geo2stl/cache.py` — root from `DATA_DIR`; `NAMESPACE_TTL` completed; byte ceiling; loose-digest
  sweep; placeholder checks deleted
- `app/server/core/db.py`, `app/server/server.py`, `app/server/routers/usage.py`,
  `app/server/routers/reports.py`, `app/server/routers/diagnostics.py`
- `app/session/terrain_session.py` — `TerrainSession.export_puzzle` writes a temporary file
- `scripts/start.ps1`, `scripts/setup-venv.ps1`, `readme.md`, `CLAUDE.md` — document the new
  location
- `tests/` — a test that the app writes nothing under the repository root when `MAP2STL_DATA_DIR`
  is set to a temporary directory

## Success criteria

1. With `MAP2STL_DATA_DIR` set to an empty temporary directory and the repository checkout
   read-only, the server starts, serves a region, builds a city model and exports an STL.
2. No file is created under the repository root during that run — asserted by a test that
   snapshots the tree before and after.
3. A fresh start with data in the old locations migrates it and logs one line per move; a second
   start migrates nothing.
4. `prune_all_caches` on a seeded 15 GB cache brings it under the configured ceiling and touches
   every namespace, including the loose top-level digests.
5. `/api/diagnostics` reports the cache budget, the per-namespace split and the last prune time.
6. The full suite passes, and `MAP2STL_CACHE`, `MAP2STL_DB_PATH`, `MICROPOLITAN_STL_DIR` and
   `H5_SRTM_ROOT` still behave as they do today.

## Risks

- **Moving a 12 GB cache.** A move across volumes degrades to a copy and could fill the disk.
  Prefer registering the old root as a secondary read-only cache over moving it; make the move
  opt-in and refuse it when free space is short.
- **Import-time constants are read widely.** `app/server/config.py` names are imported directly in
  many modules. Keep them as re-exported properties in the same change so the blast radius stays
  inside one file; do not rename anything in this plan.
- **A tighter eviction policy throws away expensive downloads.** OpenTopography is rate-limited to
  50 calls per 24 hours, so evicting `opentopo` is costly. Give it the longest TTL and the largest
  share of the budget, and evict `hydrorivers` first — it is regenerable from a local source.
- **Developer surprise.** A checkout currently keeps its cache in the repository and developers
  rely on that. The `.git`-present fallback preserves it; say so in `CLAUDE.md`.
- **Silent config degradation stays silent** unless step 3 adds validation. Fail loudly on a
  malformed `config.json`, and warn at startup when `ocean_root` resolves to an empty or missing
  folder — the roadmap already records that failure mode as observed.

## Decisions (lift into docs/decisions/ when done)

- Runtime writes go to a per-user data directory, not the install directory; the repository
  fallback exists only for developer checkouts and is keyed on `.git` being present.
- One root resolution in `app/paths.py`; no module recomputes it from `parents`.
- One settings object read at startup, replacing import-time `os.environ.get`. Environment
  variable names are preserved.
- Every cache namespace has a TTL and the cache as a whole has a byte ceiling. `output/` has an age
  policy. Nothing the app writes is unbounded.
- Per-PC machine paths live in one file shared with F-KEYS, not two.
