# Library Finder

Library Finder is a local CLI and web UI for discovering reusable utilities and methods in a Python library folder. It scans Python syntax only: it never imports utility code, opens Chrome, runs automation, or reads `.env` values or credentials.

## Project layout

```text
src/
  settings.py         project root, configs.json, .env and the data/ folder
  cli.py              command-line entry point
  core/               models, scanner, sources, catalog, search, service
  enrichment/         pipeline.py (catalog enrichment), parallel.py (workers)
  backends/           copilot, minicpm, laya, http_json, factory (picks the backend)
  web/                app.py (FastAPI), static/ (the page)
tests/                mirrors src/; run with: python -m pytest
docs/                 mockups/ and design/ (plans and specs)
configs.json.example  default settings; copy to configs.json (local, gitignored) to change them
.env.example          service URLs and tokens; copy to .env (local, gitignored)
run.bat               one-click launcher for the web UI
VERSION               application version
data/                 local, gitignored: catalog.json, library-source.txt, copilot-profile/
```

## Requirements

- Windows with Python 3.11 or newer.
- Microsoft Edge and a Microsoft 365 Copilot account, only for AI-assisted cataloging. Use `catalog --no-copilot` to skip it.

Local data stays on this machine: `data/` (the catalog, saved library paths and the signed-in Edge profile) is gitignored. See [SECURITY.md](SECURITY.md).

## Model services (optional)

Catalog enrichment and Laya ranking can use separate HTTP services, running on this machine or another device. Library Finder never starts them; `run.bat` starts only the web UI. Set the backends in `configs.json` (all keys optional):

```json
{
  "enrichment_backend": "minicpm",
  "minicpm_url": "http://127.0.0.1:9130",
  "laya_backend": "service",
  "laya_url": "http://127.0.0.1:9120"
}
```

- `enrichment_backend`: `copilot` (default, browser) or `minicpm` (MiniCPM service, one method per request, no browser).
- `laya_backend`: `local` (default, loads Laya in this process) or `service` (calls the Laya service; nothing is loaded here).
- Keep device addresses and tokens out of the repo: copy `.env.example` to `.env` (gitignored) and set `MINICPM_URL`, `LAYA_URL`, `MINICPM_TOKEN` and `LAYA_TOKEN`. Order of precedence: real environment variables, then `.env`, then `configs.json`. Tokens are never read from `configs.json`.
- If the MiniCPM service is down, the build pauses and keeps results saved so far. If the Laya service is down, search falls back to deterministic results.
- Source snippets go to whatever host the URLs name, so use hosts you trust.

## Copilot catalog notes

Copilot catalog requests include up to five new/changed methods per worker. Batches are reduced when their combined source snippets exceed 24,000 characters. Replies are keyed by exact method identity; each validated result is checkpointed individually as soon as the batch reply is collected. Missing or invalid entries remain retryable without discarding successful entries from the same batch. This reduces request count but does not remove Copilot's service limits.

Multiple libraries: in Settings, enter one library directory per line, save, then build the catalog. Duplicate and nested selections are scanned once. All directories share one searchable catalog, including freshness checks and source previews. Existing single-directory settings remain supported. Moving from a single-directory catalog to a combined catalog assigns directory-specific method identities and requires a fresh enrichment pass.

CLI example (uses the project virtual environment):

```powershell
python src/cli.py catalog --source "C:\libraries\first" "D:\libraries\second" --no-copilot
```

Omit `--no-copilot` for AI-assisted cataloging. Without `--source`, the CLI uses the directories saved in Settings.

AI-assisted catalog concurrency is controlled by `catalog_workers` in `configs.json` (integer 1–5, default 1; the old name `copilot_workers` still works). Set it to `5` for up to five simultaneous Copilot conversations, or parallel MiniCPM requests (the MiniCPM service runs `OLLAMA_NUM_PARALLEL` at once, default 2). Both CLI and web builds read it at the start of each build. Fast mode never opens Copilot. Each worker keeps its own tab and thread; completed method checkpoints are merged and saved serially. A timed-out worker leaves its remaining assignments pending while other workers continue. Multiple workers have automated coverage with fake clients; live Copilot throughput and throttling still need validation.

## Install and use

Install the package into an approved Python environment, then run:

```powershell
python -m pip install -e ".[dev,laya,copilot]"
```

From this directory:

```powershell
python src/cli.py catalog
python src/cli.py find "wait for a ticket element and click it"
python src/cli.py show libs.drivers.TicketDriver.some_method
```

Settings live in `configs.json`, which is local and gitignored. Until you create it, the tracked `configs.json.example` is used; copy it to `configs.json` to change values. Set the strict Laya pass threshold there. A method passes only when
its Laya yes probability is greater than `confidence_threshold`.

## Local web UI

Install the optional local web dependencies, then start the browser UI:

```powershell
python -m pip install -e ".[dev,laya,web]"
library-finder-web
```

Or use the project launcher, which always opens the web UI and accepts no
arguments:

```powershell
.\run.bat
```

The server listens only on `127.0.0.1:8000`. Each search scans the configured
library locally, streams Laya decisions to the page, and uses `configs.json` as
the default confidence threshold. A threshold entered in the page applies only
to that search and does not modify the configuration file.

`catalog` writes `data/catalog.json` locally. By default it sends a bounded snippet for each initially discovered method to the signed-in Microsoft 365 Copilot account in Library Finder's dedicated Edge profile, then reuses unchanged explanations and only resubmits new, changed, or previously failed methods. The catalog conversation is deleted when the run finishes; a deletion warning means it could not be confirmed and should be checked manually. Use `catalog --no-copilot` for AST-only cataloging, or `catalog --strict` to fail for source or enrichment failures. `find` warns when library files have changed since the catalog was built.

Methods beginning with `_` are searchable but display `INTERNAL`; they may be unstable. Results are advisory and include source locations, inferred side effects, and setup implications. The CLI first retrieves from its local catalog, then attempts local Laya ranking. If Laya is not installed, fails, or returns an uncertain/invalid choice, deterministic results remain available.

Omit `laya` when only deterministic local matching is needed: `python -m pip install -e ".[dev]"`.
