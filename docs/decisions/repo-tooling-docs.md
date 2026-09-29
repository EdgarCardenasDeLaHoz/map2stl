# Repos, tooling and docs

Repo names and layout, venvs, test markers, agent scripts, where docs and plans live, and the align tool's pack lookup. Related: [architecture.md](architecture.md).

### 2026-09-28 — Durable docs live in map2stl/docs, with one index and decisions per topic
- **Decision:**
  - Every durable doc lives in a git repo; `map2stl/docs/` owns the cross-repo [INDEX](../INDEX.md), decisions and plans.
  - One index: doc map, "where to edit", topic → `file::symbol` locator. No folder README stubs.
  - One home per fact type: how it works → `reference/`; why → `decisions/` (one file per topic, see [README](README.md)); what's next → `plans/active/` (finished → `plans/done/`, abandoned → `plans/archive/`); what broke → issues; what happened → `history/`.
  - `history/` is kept but out of the way (no mkdocs nav, still greppable).
  - Cite code as `file::symbol`, not line numbers.
  - `Code/` keeps only CLAUDE/AGENTS/README stubs, `memory-bank/activeContext.md` (volatile, fine unversioned) and `agent-scripts/`.
- **Why:** `Code/` is not versioned, so the index, 13 topic pages and 3.7k lines of decisions had no history; ~35 % of line-number anchors had drifted within weeks; the old decision log was one 3,693-line file.
- **Rejected:** keeping plans and the index in `Code/docs/` (the 2026-08-06 rule) — unversioned and split across two trees.
- **Supersedes / superseded by:** supersedes [2026-08-06 — Plans live in Code/docs](#2026-08-06--plans-live-in-codedocs-not-map2stldocsplans-superseded).
- **Source:** docs reorganization plan (session scratchpad, 2026-09-28).

### 2026-09-27 — The product repo is map2stl, renamed from strm2stl
- **Decision:** folder `Code/map2stl`, venv `~/.venvs/map2stl`, gitdir `~/.gitdirs/map2stl`, GitHub repo `map2stl`; environment variables are `MAP2STL_*` (old names still read); browser keys migrated.
- **How to apply:** on the other PC run `scripts/link-gitdir.ps1` then `scripts/setup-venv.ps1` in map2stl once. The local `origin` URL may still show the old name (GitHub redirects).
- **Supersedes / superseded by:** —
- **Source:** memory-bank activeContext 2026-09-27.

### 2026-09-27 — Agent helper scripts live in Code/agent-scripts, outside the git repos
- **Decision:** scripts Claude writes for rendering, reviewing, screenshotting or documenting go in `Code/agent-scripts/` (with a line in its README, locating the repo relative to themselves). One-off edit scripts stay in the session scratchpad. Outputs they produce for docs (e.g. guide images) may go into the repo.
- **Why:** the user wants the repos to hold the app and libraries only; agent tooling is kept separate but backed up by OneDrive.
- **Rejected:** `map2stl/scripts/` or the libraries.
- **Supersedes / superseded by:** —
- **Source:** user feedback 2026-09-27 (agent memory).

### 2026-09-25 — The venv is ~/.venvs/map2stl, outside OneDrive
- **Decision:**
  - One venv (Python 3.11) at `~/.venvs/map2stl`, built by `map2stl/scripts/setup-venv.ps1` from the shared Projects template; it serves both repos (`pytest.ini` also runs the numpy2stl tests).
  - The script points the repo's nbstripout filter / ipynb textconv at the venv python.
  - Dependencies the old venv had but no list declared were added (matplotlib, pandas, geopandas, osmnx, h5py, psutil, ipython, pymeshlab). torch and other ML packages stay optional; tests that need them skip.
- **Why:** the old `C:\venvs\map2stl` had vanished; a venv under OneDrive gets locked and breaks on the other PC. `.gitattributes` makes nbstripout required, so a dead python path makes `git status`/`add` fail outright.
- **Rejected:** `pip install` of the missing packages without declaring them.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-25 venv entry.

### 2026-09-17 — Code-quality audit phase 1 rules
- **Decision:**
  - Live-network tests are opt-in via the `integration` marker; the default run is `--strict-markers -m "not integration"` in both repos.
  - The roof CNN step in `map2stl/city2stl/roof_classifier.py::classify_roof_shapes` is opt-in (`cnn_model=None` skips it); no named-backbone fallback; checkpoints load with `torch.load(weights_only=True)`.
  - Concurrent identical hydrology/trails requests share one future (`map2stl/app/server/core/inflight.py`); waiters get the same error as the first caller.
  - Server-supplied strings go through `window.escapeHtml` before `innerHTML`.
  - Large structural refactors need a proposal and plan entry first.
- **Why:** the CNN fallback pulled ImageNet weights at run time and its output was never scored.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-17 entry.

### 2026-08-28 — Align-tool packs are looked up across vendor roots and may name their own slug
- **Decision:**
  - `map2stl/tools/align_tool/export_align_data.py::PACK_ROOTS` lists the vendor trees; `find_stl` falls back to the largest STL that is not a label, frame or water slab (miniatures name the plate after the city).
  - `map2stl/tools/align_tool/export_align_data.py::SLUGS` lets a colliding pack name its own slug (`resolve_center` takes a `plate_key`).
  - `map2stl/tools/align_tool/export_align_data.py::COVERAGE_UNKNOWN` packs sweep 0.50-3.07 of nominal instead of 0.78-1.20.
  - The search is seeded on geocoded "Downtown {region}" (`map2stl/tools/align_tool/locate.py::seed_center`), not the bbox midpoint.
  - The Paris miniature stays rejected rather than loosening the gate.
- **Why:**
  - Paris and Philadelphia have both a micropolitan plate and a miniature; derived slugs would overwrite each other's cache, rasters and alignment.
  - A bbox midpoint is dragged by outliers: Denver 11.6 km from LoDo (beyond the ~5.5 + 2.2 km search reach). Seed error: Boston 7285 → 544 m, Denver 11896 → 1133 m, Philadelphia 6842 → 138 m (Paris 691 → 2329 m, still in reach).
  - Paris's OSM window is 67.6 % building vs a 40.3 % plate (every passing plate ≤ 0.49). Erosion, gradient matching and 62.5 m cells all put it near Concorde/Invalides, but none lifts agreement above 4 of 16: position recoverable, confidence not.
- **Rejected:** mask erosion to match density — made the other failing plates worse.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 packs entry. Registration context: [registration-validation.md](registration-validation.md).

### 2026-08-26 — Subsystem doc pages rot; the index is the trusted entry point
- **Decision:** before trusting an anchor on a subsystem page, check its path against the filesystem. The index is the entry point.
- **Why:** five cited files had never existed (osm.py, water.py, build.py in the city2stl page; terrain.py, projection.py in the geo2stl page — the real module is projections.py). The index was accurate. A wrong pointer costs more than a missing one because it is trusted.
- **Supersedes / superseded by:** the 2026-09-28 reorganization merged those pages into the reference docs and added a link checker.
- **Source:** decisions.md 2026-08-26 entry.

### 2026-08-06 — Plans live in Code/docs, not map2stl/docs/plans [superseded]
- **Decision (then):** plan documents go flat in `Code/docs/` beside its INDEX.
- **Why:** splitting plans across two trees would make the index point outside its own tree.
- **Supersedes / superseded by:** superseded by [2026-09-28 — Durable docs live in map2stl/docs](#2026-09-28--durable-docs-live-in-map2stldocs-with-one-index-and-decisions-per-topic): plans are in `map2stl/docs/plans/`.
- **Source:** decisions.md 2026-08-06 entry.
