# F-KEYS — Keys & data folders panel

_Proposed 2026-10-14 (user choice: all four options). Status: planned; PNG mockups awaiting
approval. Not implemented._

## Goal

One "Keys & data folders" sheet that lists every key, sign-in and machine folder the app uses,
says plainly whether each one works on **this** computer, and fixes each in one step: a
one-click Google sign-in for Earth Engine (no code pasting), Test and Replace per key, and
folders stored per PC so the other PC's paths stop showing as broken.

## What exists today (measured 2026-10-14, DESKTOP-TMJM22C)

- The dialog is the "Service Authentication" modal in
  `app/client/static/js/vue/components/layout/MainHeader.vue` (`keysOpen`, `startEeAuth`,
  `completeEeAuth`, `saveOtopoKey`, `browseTilePath`, `saveTilePath`). Server:
  `app/server/routers/auth.py` (`auth_status`, `_check_earth_engine`, `_check_opentopo`,
  `save_opentopo_key`, `_apply_opentopo_key`, `get_tile_store`, `save_tile_store`,
  `browse_tile_store`, `start_earth_engine_auth`, `complete_earth_engine_auth`).
- **Earth Engine:** `~/.config/earthengine/credentials` exists (written 2026-08-26; client_id,
  client_secret, project, refresh_token, scopes) but a token refresh answers
  `RefreshError('invalid_grant: Bad Request')`: the refresh token was revoked or expired.
  `_check_earth_engine` folds that into "Not authenticated", the same text as "never signed
  in". Sign-in uses `ee.oauth.Flow(auth_mode="notebook")`: open a Google page, copy a code,
  paste it back. Every EE-backed fetch then fails and toasts
  (`geo2stl/sat2stl.py::initialize_earth_engine` caches the failure for a few minutes only).
- **earthengine-api 1.7.48** in `~/.venvs/map2stl`. `ee.oauth.Flow(auth_mode="localhost[:PORT]")`
  exists (default port `ee.oauth.DEFAULT_LOCAL_PORT` = 8085, `localhost:0` = any free port), but
  its callback server (`ee.oauth._start_server`) binds `localhost`, checks no `state`, raises
  `KeyError` on any request without `?code=` (a favicon, or `?error=access_denied` when the user
  cancels) and `fetch_code` blocks with no timeout. The public helpers it uses are reusable:
  `ee.oauth.get_authorization_url`, `ee.oauth.request_token`, `ee.oauth.write_private_json`,
  `ee.oauth.get_credentials_path`, `ee.oauth.CLIENT_ID` / `CLIENT_SECRET` (the library's
  installed-app client; the current credentials file holds a different, notebook-mode client).
- **Keys the code reads** (none shown in full anywhere; only OpenTopography is in the dialog):

  | Key | Read by | Used for |
  |---|---|---|
  | `OPENTOPO_API_KEY` env, else `opentopo_api_key` in `config.json` | `geo2stl/opentopo.py::_load_api_key`, `app/server/config.py::OPENTOPO_API_KEY` | SRTM / Copernicus / ALOS downloads, nDSM and 3DEP heights (50 calls per 24 h) |
  | Earth Engine OAuth (`~/.config/earthengine/credentials`) | `geo2stl/sat2stl.py::initialize_earth_engine` | water mask, ESA land cover, satellite |
  | `GOOGLE_MAPS_API_KEY` env, else `google_maps_api_key` in `config.json` | `city2stl/height/providers/google_3d.py::get_api_key` | Google 3D Tiles heights |
  | `GOOGLE_MAPS_API_KEY` / `GOOGLE_STREETVIEW_API_KEY`, `GOOGLE_MAPS_SIGN_SECRET` | `city2stl/skyline/streetview_io.py::_resolve_api_key`, `::_streetview_signing_enabled` | Street View fetch and URL signing |
  | `FLICKR_API_KEY` | `city2stl/skyline/region_config.py` (module constant) | skyline photo search |

  The Google key is in `map2stl/.env` (git-ignored, 39 characters). Only
  `city2stl/skyline/_core/util.py::_load_env_file_if_present` reads `.env`, so the server sees
  that key only after a skyline module was imported in the same process.
- **Machine paths in the synced `config.json`:** `ocean_root` (the local tile store, read by
  `app/server/core/tile_store.py::_read_config` / `::status` / `::set_path` and
  `geo2stl/tiles.py::get_tile_files`) holds the other PC's `C:/Users/eac84/Desktop/…` path.
  `prusa_slicer_exe`, `slicer_configs_dir`, `output_dir` and `cache_dir` are read by nothing.
  `STRM_H5_ROOT`, `MAP2STL_CACHE` and friends are env-only. No per-machine config exists.

## Approach

1. **Key registry** (`app/server/core/credentials.py`, new). One `KeySpec` per row: id, label,
   kind (`key` / `oauth` / `folder`), where it is read from in order (process env, `.env`,
   `config.json`, the per-PC file), what it powers, whether it is optional (ADVANCED), and a
   cheap `test()`. `describe()` returns `{id, label, kind, present, last4, source, state,
   detail, checked_at}`, never the value; `last4` only when the value is at least 12
   characters. `.env` is read here once, so the Google key no longer depends on import order.
   Tests per key: OpenTopography = one tiny `globaldem` request (costs one of the 50 daily
   calls, so on demand only); Google Maps = a Street View **metadata** request (free); signing
   secret = a signed metadata request; Flickr = `flickr.test.echo`; folders = exists + tile
   count. Startup checks presence only, plus the Earth Engine check below.
2. **Earth Engine status that tells the cases apart** (`credentials.py::check_earth_engine`,
   replacing `auth.py::_check_earth_engine`). Run once at startup in the executor
   (`app/server/server.py::_lifespan`), cached; Test re-runs it. States: `ok`; `missing` (no
   credentials file); `expired` (file present, refresh answers `invalid_grant`; report the
   file's date; classify on the error code, not the message, since Google said only "Bad
   Request" today); `no_project` (signed in, no Cloud project in the file or registered for
   EE); `offline` (transport error); `error` (anything else, message shown under Details).
3. **One-click sign-in on a loopback redirect** (`app/server/core/ee_signin.py`, new). `POST
   /api/auth/earth-engine/start` makes a PKCE S256 verifier and a random `state`, starts a
   one-shot listener on `127.0.0.1` with an OS-chosen free port (port 0; 8085 as the fallback
   if Google refuses an ephemeral port), and returns the Google URL built by
   `ee.oauth.get_authorization_url(challenge, ee.oauth.SCOPES, "http://127.0.0.1:<port>")` plus
   `&state=`. The **page** opens it with `window.open` in the user's own browser (the server
   never calls `webbrowser`). The listener ignores requests without the right `state`, answers
   `?error=` with a "cancelled" page, accepts exactly one valid code, exchanges it with
   `ee.oauth.request_token`, writes the file with `ee.oauth.write_private_json` (keeping the old
   file's `project`), resets `sat2stl.reset_earth_engine_status_cache`, and shuts down; 5-minute
   deadline. `GET /api/auth/earth-engine/wait` long-polls the attempt (no polling loop). The
   paste-a-code flow stays only as a hidden fallback when the page is not on the server's
   machine (request host is not loopback), so no capability is lost.
4. **Quiet status instead of toasts.** The header shows one line from the cached status
   ("Earth Engine: sign-in expired · Fix"), opening the sheet. EE-backed routes return a
   structured `{"code": "ee_auth", "state": …}`; the client shows that error once as the header
   line, not as a toast per request (`app/client/static/js/modules/layers/water-mask.js`,
   `app/client/static/js/modules/dem/dem-main.js` toast at the "See Diagnostics or Keys" hint).
5. **Per-PC folders** (`app/server/core/machine_config.py`, new): `~/.config/map2stl/machine.json`
   in the user profile (not OneDrive; not `%LOCALAPPDATA%`, which the Claude desktop app's MSIX
   sandbox redirects), holding `ocean_root`, an optional `strm_h5_root`, and later per-PC
   paths. Lookup order: env var, then `machine.json`, then the legacy `config.json` value,
   then the default. Migration on first start on each PC: copy `config.json`'s `ocean_root`
   into `machine.json` only if that folder exists here; otherwise report "Not on this PC" and
   show the legacy path. `tile_store.set_path` writes `machine.json` only. `config.json`'s
   `ocean_root` is left in place until both PCs have migrated (removal listed for approval).
   Keys stay in `config.json` (synced: one key serves both PCs); the EE sign-in is already
   per profile, so each PC signs in once.
6. **The sheet** (`MainHeader.vue`, or a new `components/layout/KeysSheet.vue`), to the
   mockups: banner with the one thing to do; ONLINE SERVICES (Earth Engine, OpenTopography,
   Google Maps); FOLDERS ON THIS COMPUTER (tiles, `.h5` store ADVANCED); OPTIONAL · SKYLINE
   TOOLS (signing secret, Flickr) behind "Show 2 more"; Test all; per row Test / Replace (or
   Sign in / Choose folder); the computer name in the subtitle. Diagnostics' "API keys" block
   reads the same registry (`app/server/routers/diagnostics.py::_auth_summary`).

## Mockups (light, plus one dark check)

`Code/claude/mockups/2026-10-14-keys/` (made by `make.py`, headless Edge via Playwright at
1300 px wide; key endings in them are sample values):
`keys-ee-expired.png`, `keys-signing-in.png`, `keys-all-good.png` (1040 px tall, every row),
`keys-key-missing.png`, `keys-ee-expired-dark.png`.

## Target files

- `app/server/core/credentials.py` (new): `KeySpec`, `describe`, `check_earth_engine`, `test_key`.
- `app/server/core/ee_signin.py` (new): `start`, `wait`, `_LoopbackHandler`.
- `app/server/core/machine_config.py` (new): `path`, `get`, `set`, `migrate_from_config`.
- `app/server/routers/auth.py`: `auth_status` → registry; new `GET /api/keys`,
  `POST /api/keys/{id}` (replace), `POST /api/keys/{id}/test`; `start_earth_engine_auth`,
  new `wait_earth_engine_auth`; `complete_earth_engine_auth` kept as the remote fallback.
- `app/server/core/tile_store.py::status`, `::set_path`, `::_read_config` → `machine_config`.
- `geo2stl/tiles.py::get_tile_files`: take the root from the caller, not `config.json`.
- `app/server/server.py::_lifespan`: startup check.
- `app/client/static/js/vue/components/layout/MainHeader.vue`: sheet + header status line.
- `app/client/static/js/modules/layers/water-mask.js`, `modules/dem/dem-main.js`: no per-request EE toast.
- Docs: `docs/reference/api.md` (routes), `docs/INDEX.md` locator, `docs/guides/` keys page,
  `docs/decisions/` (per-PC config, loopback sign-in).

## Success criteria

- With today's credentials file the sheet says "Sign-in expired … saved on 26 Aug 2026", not
  "Not authenticated"; deleting the file says "Not signed in"; no toast fires on composite.
- Sign in with Google completes with one click in the browser and no pasted code; Earth Engine
  shows Ready without a server restart; cancelling at Google shows "Cancelled", not a hang.
- Every row in the table above appears; no API response or log line contains more than the
  last 4 characters of any key (test greps responses and the usage log for each full key).
- On this PC the tile row says "Not on this PC" until a folder is chosen; choosing one writes
  `~/.config/map2stl/machine.json` and leaves `config.json` untouched; the other PC keeps its
  own folder.
- Tests: registry describe/last4, EE state classification (mocked refresh errors), loopback
  handler (state mismatch, `error=`, single use, timeout), machine-config migration.

## Risks

- **Google may refuse the library's installed-app client** for the `cloud-platform` scope or
  an ephemeral port (unverified-app screen or `redirect_uri_mismatch`). Step 0 of the build is
  a manual spike on this PC; fallbacks: port 8085, then `gcloud auth application-default
  login` if installed, then the paste flow. The new file's `client_id` differs from today's
  notebook client; `ee.Initialize` reads it from the file, so that is fine.
- **Security:** keys never leave the server beyond the last 4 characters; Replace bodies are
  not logged (the usage log already scrubs fields named key/token/secret; the input ids must
  keep such a name); the listener binds `127.0.0.1` only, checks `state`, uses PKCE, accepts
  one code, closes after 5 minutes, and never echoes the code in its page; the redirect URI is
  loopback only. Key-write routes refuse non-loopback clients.
- OpenTopography Test spends one of the 50 daily calls (401 = rate limit, not a bad key: read
  the body), so it never runs automatically.
- Two PCs writing `config.json` through OneDrive can still conflict for keys; folders no longer
  do. `.env` rewrite on Replace keeps the file's other lines and line endings.
- `city2stl/` providers keep their own key lookups; the registry must agree with
  `google_3d.get_api_key` (same precedence) until they call the registry (other agents own
  `city2stl/*` now: coordinate before editing).

## Decisions (lift into docs/decisions/ when done)

- Machine paths live in `~/.config/map2stl/machine.json`, not in the synced `config.json` and
  not keyed by `COMPUTERNAME` inside it (two PCs writing one OneDrive file make conflict copies).
- Earth Engine sign-in uses our own loopback listener on top of `ee.oauth` helpers, not
  `ee.oauth.Flow("localhost")` (no `state`, no timeout, crashes on cancel).
