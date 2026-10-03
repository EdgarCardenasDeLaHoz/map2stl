# CLAUDE.md — map2stl

> **Index file only.** Rules and workflow live here; everything else is reached through
> [docs/INDEX.md](docs/INDEX.md) (doc map, "where to edit", topic → `file::symbol` locator).

## Context management (read first)

- `/compact` is **user-triggered**; Claude cannot run it.
  - After each commit Claude ends its response with `--- Task complete. Run /compact before continuing. ---`.
  - Run `/compact` when you see that signal, or when context exceeds ~60%.
- Open `docs/INDEX.md`, then load only the 1–2 docs or files it points to.
- Keep a session on one module. If scope creeps, ask the user before editing other modules.

## Quick start

Venv: `~/.venvs/map2stl` (local, never under OneDrive). Create/refresh it with
`powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1`, which also points the
`nbstripout` git filter at it (git refuses to stage notebooks until it does).

```bash
cd map2stl && source ~/.venvs/map2stl/Scripts/activate
python -m uvicorn app.server.server:app --port 9000 --reload   # starts FastAPI
python -m pytest tests/ -v    # also runs ../numpy2stl/tests; e2e/, integration and slow are opt-in
python scripts/quicktest.py    # while developing: only tests the uncommitted changes touch (+ last failures), Python and JS at once
npm install && npm run build  # after editing .vue / .ts (the Vue bundle in dist/ is gitignored)
```

## Where things are

- **Index:** [docs/INDEX.md](docs/INDEX.md). Start every task there.
- **How it works:** `docs/reference/` (overview, pipeline, api, sdk, frontend, packages, …).
- **Why:** `docs/decisions/<topic>.md`. **What's next:** `docs/plans/README.md`. **Bugs/debt:** `docs/issues.md`.
- **User guides:** `docs/guides/` (served at `/guides`; every `.md` there becomes a guide, so only user-facing docs go in).
- **History** (not how the code works now): `docs/history/`.
- **Height models:** no CNN height model runs at runtime; the ML training history is in
  [docs/history/ml-height/README.md](docs/history/ml-height/README.md), live checkpoints in `models/README.md`.
- **Skyline** (street-view heights): [city2stl/skyline/README.md](city2stl/skyline/README.md).

## Proposal + plan + edit workflow

The point is **persistence**: long sessions and compactions destroy in-conversation context;
the proposal and plan files survive, so a future session can pick the work up cold.

**For new features or significant refactors not requested in the current conversation**, do
steps 1–3 *before* writing implementation code. The user does not need to pre-approve —
capturing the proposal and plan is the gate — then proceed straight to implementation.

1. **Proposal:** add an entry to [docs/proposals.md](docs/proposals.md) (status `pending`): short
   description, target files, effort. Its ID is the durable handle.
2. **Plan:** write `docs/plans/active/<ID>-<slug>.md` before touching code: goal, approach in
   3–6 bullets, target files, success criteria, risks. The proposal entry **must link to it**.
   Add the plan's row to the roadmap in [docs/plans/README.md](docs/plans/README.md).
3. **Implement.**

- Items the user asks for directly ("fix X", "add Y") skip steps 1–2 unless they grow large.
- When several approved proposals are ready, ask the user which comes first.
- When a plan is done: move it to `docs/plans/done/`, drop its roadmap lines, and lift its
  "Decisions" section into `docs/decisions/<topic>.md`.

## Recording decisions

Choices, and measured-and-refused hypotheses, go in `docs/decisions/<topic>.md` (topic list and
rules in [docs/decisions/README.md](docs/decisions/README.md)), newest first, in this format:

```
### YYYY-MM-DD — <decision, stated as a fact>
- **Decision:** what was chosen.
- **Why:** the reason; the measurement that settled it; user quote if any.
- **Rejected:** <alternative> — why not.
- **Supersedes / superseded by:** link to the other entry, or "—".
- **Source:** plan link or commit (optional).
```

- Superseded entries stay, heading tagged ` [superseded]`, linked both ways.
- Cite code as `path::symbol`, never `path:line`.

## Documentation checklist (after every code edit, before committing)

- [ ] Module docstring still true?
- [ ] `docs/INDEX.md` locator updated if a symbol was added, renamed, moved or removed?
- [ ] `docs/reference/frontend-modules.md` function index updated (JS/Vue functions added/removed/renamed)?
- [ ] `docs/reference/*` page for the subsystem still true (e.g. api.md for a route change)?
- [ ] `docs/issues.md` status updated if a known issue was fixed?
- [ ] `docs/proposals.md` / `docs/plans/README.md` updated if this implements a proposal or plan item?
- [ ] A decision made? → `docs/decisions/<topic>.md`.
- [ ] Surrounding code audited for dead branches, unused parameters, stale comments?

"Done" means the next session's reader can find the change from `docs/INDEX.md`.

### Edit hygiene (while the region is in context)

- **Update the code's docs in the same turn.** Docstrings, index entries and issue status decay
  fast when deferred; they are cheapest to fix while the file is open.
- **Audit the surrounding region for bloat:** dead branches, unused parameters, stale comments,
  redundant guards, two functions that have converged.
- **Note or fix follow-ups** that became visible while editing.

## Editing rules

1. **Never use `os.chdir()`** — it breaks relative paths for every other request.
2. **Never call `asyncio.get_event_loop()`** — use `asyncio.get_running_loop()` inside async functions.
3. **Frontend has two halves on one page.** Vue 3 + Pinia (`app/client/static/js/vue/`, built by
   Vite) owns the markup; plain ES modules (`app/client/static/js/modules/`, loaded by `main.js`,
   which also imports `app.js`) own most behaviour.
   - Modules mostly coordinate through `window.appState`, `window.events` and `window.*`
     functions; pure helpers may be shared with ES `import` (e.g. `app/client/static/js/modules/export/print-scale.js`).
   - Control `id=` attributes in `.vue` files are the interface the modules read: grep an id
     before renaming it.
4. **Shared state goes on `window.appState`**, not in a module-local closure. After
   DOMContentLoaded it is a bridge onto the Pinia store (`app/client/static/js/vue/stores/app.ts`); a key a component
   reads must be declared there (and in `ALL_KEYS` in `app/client/static/js/vue/main-vue.ts` if seeded earlier).
   Details: [docs/reference/frontend.md](docs/reference/frontend.md).
5. **Libraries stay below the app:** numpy2stl is geo-free; `geo2stl/` and `city2stl/` never
   import `app`. Request handling goes in `app/server/routers/`, work in `app/server/core/` or a library.
6. **Patch where the name is looked up in tests** — the full module path under `app.server...`
   (e.g. `app.server.routers.<router>.<name>`), never a shortened `app.routers...` path.
7. **Backend:** blocking work runs in `run_in_executor`.

## Common agent mistakes

- Treating `numpy2stl/` as the product: `map2stl/` is the application; numpy2stl is its mesh library.
- Broad repo searches before checking `docs/INDEX.md` (or `graphify query` from `Code/`).
- Assuming a Vue component and an ES module share state through imports: they meet on
  `window.appState` and on control ids.
- Duplicating reference material (api, overview, frontend) in a new doc instead of linking to it.
- Starting a significant unrequested feature without a `docs/proposals.md` entry and a plan.
- Citing `path:line`: cite `path::symbol`.
