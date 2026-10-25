# Entry points, scripts, tests

Locator for application entry points, scripts, tests and tooling. Back to the [index](../INDEX.md); paths are relative to `Code/`.

### Entry points, scripts, tests

- Launchers: `Start 3D Maps.bat`, `Stop 3D Maps.bat` (workspace root) → `map2stl/scripts/start.ps1`, `map2stl/scripts/stop.ps1`
- Venv: `map2stl/scripts/setup-venv.ps1` (creates `~/.venvs/map2stl`); Linux cloud sessions `map2stl/.claude/hooks/session-start.sh` (SessionStart hook: clones ../numpy2stl, venv without torch, npm install; readme "Running in the cloud"); git database link `map2stl/scripts/link-gitdir.ps1`
- Tests: `map2stl/pytest.ini` also collects `numpy2stl/tests`; `-m integration`, `-m slow` and `-m ml` (torch) are opt-in; the default run has no network (`map2stl/tests/conftest.py::_block_network`: outbound sockets raise unless the test is marked `integration` or `requires_network`); tests needing what a fresh clone lacks skip themselves (`requires_cache`, `requires_gpu`, `requires_network`, `requires_keys`: `map2stl/tests/conftest.py::pytest_collection_modifyitems`); `tests/e2e/` (playwright) and `tests/manual/` are never collected; `-n 6` runs in parallel (pytest-xdist)
- Pre-push hooks: `map2stl/.githooks/pre-push` (pytest `-n 6`, vitest, eslint), `numpy2stl/.githooks/pre-push` (numpy2stl's own tests); `core.hooksPath` set by `map2stl/scripts/setup-venv.ps1` and `numpy2stl/scripts/link-gitdir.ps1`
- Test fixtures from `numpy2stl/tests/conftest.py` apply to *every* test when collected from map2stl (pytest scopes conftests outside the rootdir globally): prefix benchmark fixtures `bench_`, and don't request `monkeypatch` in its autouse fixtures
- Helper scripts for agents (renders, screenshots, doc link checker): `claude/scripts/README.md`
- Shared machine resources (GPU queue without CPU fallback, RAM check, concurrency budget; rules in `map2stl/CLAUDE.md` › Shared machine resources): `map2stl/city2stl/resources.py` (`wait_for_gpu`, `wait_for_ram`, `ram_ok`, `free_gpu_cache`)
- Agent task board (cloud and local sessions; rules, owners, merge process): `map2stl/docs/agents/TASKS.md`; cloud rules: `map2stl/CLAUDE.md` › Cloud agents
- Performance: `perf {json}` log line per expensive step and per /api request: `map2stl/geo2stl/perf.py::perf_step` (request timer `app/server/server.py::_perf_request`); the audit that drives the app and flags duplicate work, hangs and slow actions: `claude/scripts/perf_audit.py` (runs its own server on a copy of `data.db` via `MAP2STL_DB_PATH`, `app/server/core/db.py::DB_PATH`)
- One computation per key under concurrent requests: `app/server/routers/composite.py::_single_flight`, `map2stl/geo2stl/water_layers.py::_carve_lock`, `map2stl/geo2stl/dem.py::shared_base_grid`, `map2stl/geo2stl/sat2stl.py::fetch_bbox_image` (Earth Engine download), in-memory memos `map2stl/geo2stl/tiles.py::stitch_tiles_no_rasterio`, `map2stl/geo2stl/hydrology.py::fetch_hydrorivers`; trails skipped above `app/server/routers/terrain.py::TRAILS_MAX_DIAG_KM`
