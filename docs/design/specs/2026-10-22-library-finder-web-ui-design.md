# Library Finder local web UI

## Goal

Provide a local browser UI for the existing Library Finder workflow. It runs the configured local catalog and Laya evaluation, streams each decision, shows passed hits, and previews the selected method's source.

## Scope

- Local FastAPI server only. No hosted deployment, authentication, persistence, or remote data access.
- Existing CLI commands remain supported.
- The UI uses the configured library source and `configs.json` confidence threshold.
- One evaluation can run at a time.

## Architecture

`library_finder.web` owns the FastAPI application and serves static HTML, CSS, and JavaScript.

The browser calls `POST /api/search` with a goal and optional threshold. The server scans the configured source into a fresh in-memory catalog for that run, starts Laya evaluation in a background task, and returns a run ID. When omitted, the threshold comes from `configs.json`; a supplied value applies only to that run and does not rewrite configuration. `GET /api/search/{run_id}/events` streams Server-Sent Events in catalog order. Each method event contains the qualified name, relative file, source line, score, and strict pass decision. A final event contains the passed methods sorted by descending score.

`GET /api/method-source` accepts a catalog method identifier and returns a bounded source snippet. It resolves the record path beneath the configured library root and rejects any path outside that root.

## UI

The UI follows the approved mockup:

- Search input and confidence-threshold control.
- Progress counter and bar using streamed current and total values.
- Method table with score and Passed or Decline status.
- Hits summary sorted from highest to lowest score.
- Selected-method code preview with relative source path and line numbers.

## Errors and safety

- Invalid goal, invalid threshold, missing configuration, unavailable source folder, and Laya failures render a visible error state.
- The UI never reads or returns `.env` contents.
- Source preview uses catalog records only and enforces source-root containment.
- A second search during an active run receives a conflict response.

## Dependencies and entry point

Add FastAPI and Uvicorn as an optional `web` dependency group. Add a `library-finder-web` console script that starts the server and opens the local UI.

## Verification

- Unit tests for threshold validation, stream event ordering, final hit ordering, source containment, and active-run conflict.
- Browser smoke test against the local server when the environment supports it.
- Existing CLI tests stay green.
