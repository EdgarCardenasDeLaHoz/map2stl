# Audits

_Last updated: 2026-05-02_

Audit docs capture findings, gap analysis, and historical assessments. Treat them as evidence and context, not as the default source of current behavior or the live backlog.

## Working rule

- If an audit reveals still-open work, restate that work in `../todos/README.md`, `../issues.md`, or `../proposals.md`.
- If an audit item is already shipped, point to `../completed/` and trim the audit back to the evidence and outcome.
- If an audit becomes fully historical, leave only the summary plus links to the owning current docs.

## Start here

- `ux-audit.md` for the historical frontend audit and the disposition of its formerly open items
- `dead-code-analysis.md` for cleanup status and the one remaining legacy-ML follow-up
- `test-coverage-audit.md` for the remaining test-gap summary after earlier fixes landed
- `accessibility-audit.md` for contrast evidence that should be restated elsewhere before implementation

_The layer system analysis moved to `archive/layer-system-analysis.md` once all issues were resolved. See `reference/layer-system.md` for the current architecture._