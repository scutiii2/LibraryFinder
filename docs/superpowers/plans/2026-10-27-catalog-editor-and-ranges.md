# Catalog editor and range controls implementation plan

**Goal:** Implement the supplied catalog description editor and numeric control redesign in two commits, without pushing.

**Architecture:** Keep immutable catalog records and atomic JSON persistence. Share manual-description reuse rules between serial and parallel enrichment. Use a server-paginated list of 50 methods and an inline editor in Settings; use a Workers stepper and paired confidence sliders.

**Tech stack:** Python, FastAPI, vanilla JavaScript and CSS, pytest.

**Spec:** User-supplied request in this chat (2026-10-27).

## Constraints and review focus

- Preserve Fast mode, cancellation, existing input IDs and touched semantics; no dependencies.
- Protect manual records in all checkpoints, including changed fingerprints and overwrite builds.
- Serialize description writes with build/search admission using catalog_lock; persist before publishing state.
- Reject invalid fields and unsafe source paths; retain scanner facts for newly added descriptions.
- Guard unsaved edits and asynchronous list/editor responses; paginate and provide accessible status feedback.
- Verify narrow screens, focus states, slider synchronization and disabled controls in the running app.

## Tasks

- [ ] Add failing web/enrichment tests for list filtering/pagination, creation/edit/clear, validation, busy conflicts, persistence compatibility, manual preservation/overwrite and source safety.
- [ ] Add Enrichment.source/edited_fingerprint and shared reuse/staleness logic; update Laya attribution and catalog health.
- [ ] Add catalog list/detail/source/update APIs and overwrite_manual to both build endpoints, with atomic persistence and admission locking.
- [ ] Add paginated browser, accessible editor, source context and nested overwrite opt-in. Run tests and syntax check; commit `feat: description editor`.
- [ ] Add Workers stepper and confidence range pairs, preserving IDs/value/touched semantics; verify syntax and full suite.
- [ ] Inspect Settings/Search at desktop and mobile widths, exercise editor and range controls, resolve issues, commit `feat: redesigned range controls`, verify clean tree.
