# F-DESKTOP — Standalone installable application

_Proposed 2026-10-14 after the deployment audit. Status: approved 2026-10-14. Not implemented._

Depends on [F-DATADIR](F-DATADIR-portable-data-dir.md) and shares its settings layer with
[F-KEYS](F-KEYS-keys-and-folders-panel.md). Sibling track:
[F-CLOUD](F-CLOUD-hosted-multi-user.md).

## Goal

A user who has never installed Python runs one installer, clicks one icon, and gets the app in
their browser against a local server. No repository checkout, no venv, no `scripts/start.ps1`, no
editing of `config.json` by hand. The install directory is read-only at runtime; everything the
app writes lives under the per-user data directory from F-DATADIR.

Estimated effort once F-DATADIR is in: about two weeks.

## What exists today (measured 2026-10-14, DESKTOP-TMJM22C)

- **No entry point.** `pyproject.toml` declares `name = "map2stl"`, `version = "0.1.0"`,
  `requires-python = ">=3.11"` and a single dependency on `numpy2stl`, but has no
  `[project.scripts]`. Starting the app means `python -m uvicorn app.server.server:app --port 9000`
  from the checkout.
- **`numpy2stl` is a sibling-directory dependency,** not a published wheel. The cloud session-start
  hook clones `../numpy2stl`; an installer cannot.
- **The real dependency list is `requirements.txt`, not `pyproject.toml`** — fastapi 0.135.1,
  uvicorn 0.41.0, numpy 1.26.4, scipy 1.15.2, opencv-python, pymeshlab, manifold3d, osmnx,
  geopandas, laspy, pyarrow, s3fs, scikit-learn, earthengine-api. Torch, torchvision, timm,
  transformers and segmentation-models-pytorch are **commented out**; the server starts without
  them.
- **The Vue bundle is built, not committed.** `app/client/static/js/vue/` is compiled by Vite into
  a gitignored `dist/`, so a packaging step must run `npm install && npm run build`.
- **Models download on first use** — roughly 400 MB across SegFormer, Depth Anything and MobileSAM,
  to a Hugging Face cache, with no progress surfaced in the UI.
- **Secrets sit in files in the tree:** a plaintext OpenTopography key in `config.json` (git-ignored
  here but un-ignored by the parent `Code/.gitignore`) and `GOOGLE_MAPS_API_KEY` in `map2stl/.env`.
- **The port is fixed at 9000** and nothing opens a browser; the desktop shortcut added earlier
  depends on the checkout layout and the `~/.venvs/map2stl` virtual environment.
- **One server-side GUI call exists** — a tkinter folder picker used by the Edit panel. On a desktop
  install that is acceptable (the server *is* the user's machine); F-CLOUD must remove it.

## Approach

1. **Make the project installable.** Add `[project.scripts] map2stl = "app.cli:main"` and a small
   `app/cli.py` that picks a free port, starts uvicorn bound to `127.0.0.1` only, waits for the
   health endpoint and opens the default browser. Move the pinned list from `requirements.txt` into
   `pyproject.toml` dependencies (keeping `requirements.txt` as a lock for developers), with the ML
   stack behind an optional extra `[ml]`.
2. **Build `numpy2stl` as a wheel** into the same wheelhouse as the app, so neither a git clone nor
   a relative path is needed at install time. Nothing about the sibling-repository development
   layout changes.
3. **Bundle the frontend.** `npm install && npm run build` becomes a packaging step, and `dist/`
   ships inside the wheel as package data, beside the existing `app.client` static and template
   entries.
4. **Package as an embedded-CPython installer, not PyInstaller.** pymeshlab, manifold3d, geopandas,
   osmnx and opencv are heavy binary wheels with native data files; PyInstaller hooks for that set
   are a maintenance tax. Instead ship the official Windows embeddable CPython 3.12, a wheelhouse,
   and an installer (Inno Setup) that pip-installs the wheelhouse into a per-user virtual
   environment under the data directory. This keeps `pip install` as the single supported
   installation path on every platform.
5. **Bake the model weights** into the installer as an optional component, and set the Hugging Face
   cache into the data directory so a first run is offline-capable. When the component is skipped,
   download on first use and show progress in the UI rather than appearing to hang.
6. **Secrets through F-KEYS.** The installer writes no keys. On first run the app offers the keys
   and folders panel; keys go to the per-user config (and the OS credential store where available),
   never into the install directory. Every feature that needs a key must degrade with a visible
   explanation, not silently — the flat-map failure mode in `app/server/core/tile_store.py` is the
   example to fix.
7. **Decide the ML default.** Two installer variants (with and without torch) differ by roughly
   1.3 GB. Default to the torch-free build, with the ML extra as an in-app "download the detail
   models" action, since the server already starts without torch.
8. **Updates.** Publish the wheelhouse per release and give the app a "check for updates" action
   that compares `importlib.metadata.version("map2stl")` against a published version file. No
   silent auto-update.

## Target files

- `pyproject.toml` — `[project.scripts]`, dependencies, the `[ml]` extra, `dist/` package data
- `app/cli.py` (new) — port selection, uvicorn startup, browser launch
- `packaging/` (new) — Inno Setup script, wheelhouse build script, embeddable-Python fetch
- `app/server/core/tile_store.py` — fail visibly when no key or tile source is configured
- `scripts/start.ps1`, `readme.md`, `CLAUDE.md` — developer path unchanged, installed path
  documented
- `tests/` — a test that `map2stl --version` and the health endpoint work from an installed wheel in
  a clean virtual environment

## Success criteria

1. On a Windows machine with no Python, no Node and no checkout, the installer runs, the desktop
   icon starts the app, the browser opens, and a region renders.
2. Nothing is written under the install directory during that run.
3. With no keys configured, every affected feature shows a specific message naming the missing key;
   none silently produces wrong output.
4. The torch-free installer is under 1 GB; the ML variant installs and the detail models load.
5. `pip install map2stl` in a clean virtual environment on Linux and macOS gives a working
   `map2stl` command, with no sibling `numpy2stl` checkout present.
6. The full suite passes unchanged.

## Risks

- **Binary wheel availability** for pymeshlab and manifold3d on the exact Python version chosen; pin
  the embeddable CPython to a version all of them publish wheels for, and verify before building the
  installer.
- **Installer size and antivirus friction.** A 1 GB unsigned installer will be flagged. Code signing
  is a cost the user has to accept or decline; note it, do not assume it.
- **The sibling data folders** (`MICROPOLITAN_STL_DIR`, `H5_SRTM_ROOT`) do not exist in an install.
  F-DATADIR gives them data-directory defaults; this plan must confirm the features that use them
  degrade rather than crash.
- **Earth Engine credentials** currently fail to refresh (`RefreshError('invalid_grant: Bad
  Request')`, recorded in F-KEYS). An installed app cannot use the developer's cached credentials at
  all, so the sign-in flow in F-KEYS is a hard prerequisite for any Earth Engine feature in the
  packaged build.
- **The tkinter picker** needs a GUI toolkit present in the embedded Python. Confirm tkinter ships
  with the embeddable distribution, or replace the picker with a browser-side path entry.

## Decisions (lift into docs/decisions/ when done)

- Packaging is embedded CPython plus a wheelhouse plus an installer, not PyInstaller, because of the
  native-wheel dependency set.
- `pip install map2stl` is the single supported installation mechanism; the installer is a wrapper
  around it.
- The default build ships without torch; detail models are an opt-in download.
- The installer never writes credentials; keys are entered in the app and stored per user.
