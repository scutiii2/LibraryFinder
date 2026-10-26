# Library Finder Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local browser UI that scans the configured utility library, streams real Laya decisions, and previews selected method source.

**Architecture:** A FastAPI application owns local search runs, with a worker thread placing Laya progress events on a queue. The browser starts a run with JSON, consumes SSE events in catalog order, and requests bounded source snippets for selected catalog records. Static assets render the approved Library Finder dashboard.

**Tech Stack:** Python 3.11, FastAPI, Uvicorn, Server-Sent Events, vanilla HTML/CSS/JavaScript, pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-library-finder-web-ui-design.md`

## Global Constraints

- Local-only server; do not create a hosted Site or send project data remotely beyond the existing local Laya model behavior.
- Preserve the existing `catalog`, `find`, and `show` CLI behavior.
- Use the configured source from `library-source.txt` or the existing adjacent-library default.
- Use `configs.json` as the default threshold; request-level overrides apply only to the active web run.
- A method passes only when `score > threshold`.
- Never read, serve, log, or include `.env` contents.
- The project has no Git repository; do not add commit steps.

## Review Focus

- A traversal-shaped source path or a method record outside the configured source root must be rejected by the source-preview endpoint (Task 3).
- A run with no scores above the threshold must complete successfully with an empty hits list (Task 2).
- Scores exactly equal to the threshold must be marked Decline, not Passed (Task 2).
- A second search while the first is active must receive HTTP 409 and must not replace the active run (Task 2).
- A Laya exception after some streamed evaluations must emit an error and terminal event rather than leaving the browser loading indefinitely (Task 2).

---

### Task 1: Add local web runtime and entry point

**Files:**
- Modify: `pyproject.toml`
- Create: `src/library_finder/web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `create_app(*, source_root: Path | None = None, default_threshold: float | None = None) -> FastAPI`.
- Produces: `run_server() -> None` for the `library-finder-web` console script.

- [ ] **Step 1: Write the failing app-factory and entry-point test**

```python
from library_finder.web import create_app

def test_create_app_serves_the_web_shell():
    client = TestClient(create_app(source_root=tmp_path, default_threshold=0.5))
    response = client.get("/")
    assert response.status_code == 200
    assert "Library Finder" in response.text
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m pytest tests/test_web.py::test_create_app_serves_the_web_shell -v`

Expected: FAIL because `library_finder.web` does not exist.

- [ ] **Step 3: Add the `web` optional dependency group and console script**

In `pyproject.toml`, define `web = ["fastapi", "uvicorn"]` and `library-finder-web = "library_finder.web:run_server"`.

- [ ] **Step 4: Implement `create_app` and `run_server` in `src/library_finder/web.py`**

Serve the UI shell at `/`. Resolve omitted `source_root` and `default_threshold` through the existing CLI configuration helpers. `run_server` starts Uvicorn on loopback only and opens the local URL with `webbrowser.open`.

- [ ] **Step 5: Run the focused test to verify it passes**

Run: `python -m pytest tests/test_web.py::test_create_app_serves_the_web_shell -v`

Expected: PASS.

### Task 2: Implement streamed local search runs

**Files:**
- Modify: `src/library_finder/web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `create_app(..., source_root, default_threshold)`.
- Produces: `POST /api/search` accepting `{"goal": str, "threshold": float | null}` and returning `{"run_id": str}`.
- Produces: `GET /api/search/{run_id}/events` SSE events named `evaluation`, `complete`, and `error`.

- [ ] **Step 1: Write failing tests for validation, event order, threshold behavior, empty hits, Laya failure, and active-run conflict**

Use injected scanner and ranker factory fakes. Assert evaluation events preserve catalog order; a score of `0.50` is Decline at threshold `0.50`; complete payload hits are descending by score; a second request gets 409; and an exception yields an `error` followed by terminal completion.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `python -m pytest tests/test_web.py -v`

Expected: FAIL because search routes and run state do not exist.

- [ ] **Step 3: Add run-state and injected dependency boundaries to `create_app`**

Accept optional `scanner` and `ranker_factory` callables for tests. Define an internal run record containing an event queue, the catalog, terminal state, and active flag. Permit exactly one active record.

- [ ] **Step 4: Implement search worker and SSE routes**

Scan a fresh catalog, create `LayaRanker(on_evaluation=...)`, call `find_methods`, and enqueue one evaluation payload per callback. Derive `passed` using strict `score > threshold`; enqueue a complete payload containing all hits sorted descending. Convert worker errors into safe error payloads without source or environment contents.

- [ ] **Step 5: Run focused tests to verify they pass**

Run: `python -m pytest tests/test_web.py -v`

Expected: PASS.

### Task 3: Add safe source-preview API

**Files:**
- Modify: `src/library_finder/web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: completed or active run catalog by `run_id`.
- Produces: `GET /api/search/{run_id}/source?method=<qualified_name>` returning `{"path": str, "start_line": int, "lines": list[str]}`.

- [ ] **Step 1: Write failing source-preview tests**

Test a selected known catalog method returns a bounded range containing its definition. Test an unknown method returns 404. Test a synthetic catalog record resolving outside `source_root` returns 400 or 403 and reads no file.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `python -m pytest tests/test_web.py -k source -v`

Expected: FAIL because the source route does not exist.

- [ ] **Step 3: Implement bounded source resolution and snippet extraction**

Resolve the catalog record file beneath `source_root`, reject paths outside it, and return a fixed window around `record.line` with numbered text. Do not use arbitrary client-provided paths.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run: `python -m pytest tests/test_web.py -k source -v`

Expected: PASS.

### Task 4: Build the dashboard shell and streaming client

**Files:**
- Create: `src/library_finder/static/index.html`
- Create: `src/library_finder/static/styles.css`
- Create: `src/library_finder/static/app.js`
- Modify: `src/library_finder/web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `/api/search`, `/api/search/{run_id}/events`, and `/api/search/{run_id}/source`.
- Produces: browser UI matching the approved dashboard behavior.

- [ ] **Step 1: Write failing shell assertions**

Extend the root-page test to assert the search form, threshold input, progress region, evaluation table, hits panel, and code-preview region are present.

- [ ] **Step 2: Run the focused shell test to verify it fails**

Run: `python -m pytest tests/test_web.py::test_create_app_serves_the_web_shell -v`

Expected: FAIL because the shell lacks the required controls.

- [ ] **Step 3: Implement static dashboard assets**

Implement the approved light dashboard with accessible labels. In `app.js`, submit searches, disable inputs while active, append SSE evaluation rows in received order, update progress, render sorted hits on completion, and fetch a source snippet when a row is selected. Show visible errors and re-enable search after a terminal event.

- [ ] **Step 4: Serve static assets from `create_app`**

Mount the static directory and return `index.html` from `/` without allowing filesystem paths outside package assets.

- [ ] **Step 5: Run focused shell and API tests to verify they pass**

Run: `python -m pytest tests/test_web.py -v`

Expected: PASS.

### Task 5: Document and verify the local UI

**Files:**
- Modify: `README.md`
- Test: `tests/test_run_bat.py`

**Interfaces:**
- Consumes: `library-finder-web` console script and `configs.json`.
- Produces: user instructions for installing `[web]`, starting the local UI, and setting the default threshold.

- [ ] **Step 1: Write a failing launcher/documentation-adjacent test if `run.bat web` is added**

Only add a `run.bat web` branch if the launcher is extended; otherwise leave the launcher unchanged and document `library-finder-web` as the command.

- [ ] **Step 2: Document the local installation and run workflow**

Add exact commands for `python -m pip install -e ".[dev,laya,web]"` and `library-finder-web`. State that the UI is local-only and that `configs.json` supplies the default threshold.

- [ ] **Step 3: Run the full test suite and syntax checks**

Run: `python -m pytest -q`

Expected: PASS with no failures.

Run: `python -m py_compile src/library_finder/*.py`

Expected: exit code 0.

- [ ] **Step 4: Run a browser smoke test when local preview support is available**

Start `library-finder-web`, load the local page, submit a short goal, and confirm that a streamed evaluation, summary, and code preview render. If local browser automation is unavailable, document that limitation and retain the API and static-shell verification evidence.
