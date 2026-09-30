# F-USAGE — Local usage log

Status: logging live (2026-09-30); analysis pending real sessions. User-requested: "track my button clicks and usage to inform
yourself of usage and user flow". Chosen: a built-in, local-only log (not a one-session
listener), recording clicks, control values, timing, the map box and errors.

## Goal

Know how the app is really used — which steps, in what order, how long each takes, where
the user backtracks or hits errors — so UI and pipeline work targets the real flow.

## Approach

- `app/client/static/js/modules/core/usage-log.js` (imported early by `main.js`):
  - capture-phase `click` on buttons, links, tabs, `summary`, checkboxes / radios;
    `change` on inputs, selects and textareas (id, label text, panel, new value);
  - app events: `EV.BBOX_CHANGED` (the box), `EV.REGION_SELECTED`, `EV.DEM_LOADED`;
  - `window.showToast` (message + type), `window.onerror`, unhandled rejections;
  - `/api/` requests through `window.fetch`: method, path, status, duration (polling routes
    such as `/api/export/status` only when they finish or fail);
  - every event: time, ms since the previous event, session id (one per page load).
  - Never recorded: password fields and any control whose id / name mentions key, token,
    secret or password (the OpenTopography key field).
  - Buffered; sent every 5 s and on page hide (`sendBeacon`). Pause switch: the
    `map2stl_usage_log` localStorage key (`off` pauses), also `window.usageLog.pause()`.
- `app/server/routers/usage.py`: `POST /api/usage` appends to
  `output/usage/<date>.jsonl` (git-ignored, never sent anywhere); `GET /api/usage/status`.
- `Code/claude/scripts/usage_report.py`: sessions, action sequences, time per step, errors.

## Target files

`app/client/static/js/modules/core/usage-log.js`, `app/client/static/js/main.js`,
`app/server/routers/usage.py`, `app/server/server.py`, `tests/test_usage_router.py`,
`Code/claude/scripts/usage_report.py`, docs (INDEX, api.md, frontend-modules.md).

## Success criteria

- Clicking through a city build in the browser writes one line per action with the
  control, value and timing; nothing with "key" / "token" in it is logged.
- The log survives reloads; the report script prints the flow of a session.
- No visible slowdown (listeners are passive, one POST per 5 s at most).

## Risks

- Noise (map drags, slider scrubbing): `change` fires once per release, not per `input`;
  map moves arrive as `BBOX_CHANGED` only.
- Wrapping `fetch` must not change behaviour: the wrapper returns the original promise.

## Progress

- 2026-09-30: plan written; logger, route, tests and report script in. Verified in the browser: tab clicks, API timings, toasts reach `output/usage/`, key / password fields stored as `[redacted]`. Next: read real sessions with `usage_report.py` and turn the findings into roadmap items.
