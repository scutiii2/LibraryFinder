# Library Finder

Library Finder is a local CLI and web UI for discovering reusable utilities and methods in a Python library folder. It scans Python syntax only: it never imports utility code, opens Chrome, runs automation, or reads `.env` values or credentials.

## Project layout

```text
src/
  settings.py         project root, configs.json, .env and the data/ folder
  cli.py              command-line entry point
  core/               models, scanner, sources, catalog, search, service
  enrichment/         pipeline.py (catalog enrichment), parallel.py (workers)
  backends/           minicpm, errors, laya, http_json, factory
  web/                app.py (FastAPI), static/ (the page)
tests/                mirrors src/; run with: python -m pytest
docs/                 mockups/ and design/ (plans and specs)
configs.json.example  default settings; copy to configs.json (local, gitignored) to change them
.env.example          service URLs and tokens; copy to .env (local, gitignored)
run.bat               one-click launcher for the web UI
VERSION               application version
data/                 local, gitignored: catalog.json, library-source.txt
```

## Requirements

- Windows with Python 3.11 or newer.
- A running MiniCPM service for AI-assisted cataloging. Use `catalog --no-enrichment` for a local scan only.

Local data stays on this machine: `data/` (the catalog and saved library paths) is gitignored. See [SECURITY.md](SECURITY.md).

## Model services

AI-assisted catalog enrichment uses the MiniCPM HTTP service. Laya ranking can also use a separate HTTP service. Services can run on this machine or another device. Library Finder never starts them; `run.bat` starts only the web UI. Configure service URLs and Laya in `configs.json` (all keys optional):

```json
{
  "minicpm_url": "http://127.0.0.1:9130",
  "laya_backend": "service",
  "laya_url": "http://127.0.0.1:9120"
}
```

- MiniCPM is the only AI-assist backend, with one method per request. Remove `enrichment_backend` from existing configurations; the legacy value `minicpm` is accepted and ignored.
- `laya_backend`: `local` (default, loads Laya in this process) or `service` (calls the Laya service; nothing is loaded here).
- Keep device addresses and tokens out of the repo: copy `.env.example` to `.env` (gitignored) and set `MINICPM_URL`, `LAYA_URL`, `MINICPM_TOKEN` and `LAYA_TOKEN`. Order of precedence: real environment variables, then `.env`, then `configs.json`. Tokens are never read from `configs.json`.
- If the MiniCPM service is down, the build pauses and keeps results saved so far. If the Laya service is down, search falls back to deterministic results.
- Source snippets go to whatever host the URLs name, so use hosts you trust.

## Catalog notes

MiniCPM explains one new or changed method per request. Each successful method is checkpointed before the next request. Failed methods remain retryable on the next refresh.

Multiple libraries: in Settings, enter one library directory per line, save, then build the catalog. Duplicate and nested selections are scanned once. All directories share one searchable catalog, including freshness checks and source previews. Existing single-directory settings remain supported. Moving from a single-directory catalog to a combined catalog assigns directory-specific method identities and requires a fresh enrichment pass.

CLI example (uses the project virtual environment):

```powershell
python src/cli.py catalog --source "C:\libraries\first" "D:\libraries\second" --no-enrichment
```

Omit `--no-enrichment` for AI-assisted cataloging. Without `--source`, the CLI uses the directories saved in Settings.

AI-assisted catalog concurrency is controlled by `catalog_workers` in `configs.json` (integer 1–5, default 1). Set it to `5` for up to five parallel MiniCPM requests (the MiniCPM service runs `OLLAMA_NUM_PARALLEL` at once, default 2). Both CLI and web builds read it at the start of each build. Fast mode does not call MiniCPM. Each worker owns its client; completed method checkpoints are merged and saved serially. An interrupted worker leaves its remaining assignments pending while other workers continue.

## Install and use

Install the package into an approved Python environment, then run:

```powershell
python -m pip install -e ".[dev,laya]"
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

`catalog` writes `data/catalog.json` locally. By default it sends a bounded snippet for each initially discovered method to the configured MiniCPM service, then reuses unchanged explanations and only resubmits new, changed, or previously failed methods. Use `catalog --no-enrichment` for AST-only cataloging, or `catalog --strict` to fail for source or enrichment failures. `find` warns when library files have changed since the catalog was built.

Methods beginning with `_` are searchable but display `INTERNAL`; they may be unstable. Results are advisory and include source locations, inferred side effects, and setup implications. The CLI first retrieves from its local catalog, then attempts local Laya ranking. If Laya is not installed, fails, or returns an uncertain/invalid choice, deterministic results remain available.

Omit `laya` when only deterministic local matching is needed: `python -m pip install -e ".[dev]"`.
