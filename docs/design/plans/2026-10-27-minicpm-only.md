# MiniCPM-only enrichment implementation plan

Goal: remove the browser enrichment backend while preserving saved catalogs, scanning, search, Laya, overwrite, workers, checkpoints and cancellation.
Spec: user-provided request, 2026-10-27.

## Task 1: Neutral shared types (commit 1)

- Move exceptions to src/backends/errors.py, preserving inheritance and messages.
- Move strict JSON decoding and parse_enrichment to src/enrichment/parsing.py.
- Rename the enrichment model and pipeline protocol and update imports.
- Port shared parser tests and add saved catalog compatibility coverage.
- Run `python -m pytest tests -q`; commit after green.

## Task 2: MiniCPM-only backend (commit 2)

- Delete browser and batch implementation/tests.
- Make enrichment_factory(project_root) return the MiniCPM factory.
- Reject obsolete enrichment_backend values; accept and ignore minicpm.
- Remove strategy API/UI and conversation deletion results; retain neutral cleanup.
- Rename the web injection hook to client_factory and CLI scan-only flag to --no-enrichment.
- Drop the old workers alias; retain catalog_workers validation.
- Update dependencies, docs and examples; mark historical design files superseded.
- Preserve docs/mockups/redesign.html and unrelated behavior.
- Test migration, factory defaults, settings, builds and existing enrichment behavior.
- Run full tests, forbidden-reference scan, JS syntax and web/MiniCPM smoke checks.
- Review, commit, do not push.

Review focus: saved tuple fields, cancellation/pending state, parallel checkpoint durability, fast mode without AI calls, service failures without fallback.
