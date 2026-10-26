"""Local FastAPI UI for Library Finder."""

from __future__ import annotations

import json
import hashlib
import queue
import threading
import uuid
import webbrowser
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from library_finder.cli import configured_source, configured_sources, configured_threshold, configured_workers
from library_finder.sources import scan_sources, normalize_sources, catalog_roots
from library_finder.parallel_catalog import enrich_parallel
from library_finder.laya_ranker import LayaRanker
from library_finder.scanner import scan_library
from library_finder.service import find_methods
from library_finder.catalog import load_catalog, save_catalog, catalog_health
from library_finder.enrichment import enrich_catalog
from library_finder.copilot import CopilotClient


_STATIC_DIR = Path(__file__).with_name("static")
_PROJECT_DIR = Path(__file__).resolve().parents[2]


def create_app(*, source_root: Path | None = None, default_threshold: float | None = None,
               scanner: Callable = scan_library, ranker_factory: Callable = LayaRanker,
               copilot_factory: Callable = CopilotClient, data_directory: Path | None = None,
               model_loader: Callable | None = None) -> FastAPI:
    root = Path(data_directory) if data_directory is not None else _PROJECT_DIR
    @asynccontextmanager
    async def lifespan(app):
        if ranker_factory is LayaRanker:
            logging.getLogger("uvicorn.error").info("Loading Laya before accepting searches…")
            loader = model_loader or (lambda: LayaRanker().prepare())
            app.state.laya_engine = await asyncio.to_thread(loader)
            logging.getLogger("uvicorn.error").info("Laya ready.")
        yield
        app.state.laya_engine = None
    app = FastAPI(title="Library Finder", lifespan=lifespan)
    app.state.laya_engine = None
    app.state.source_roots = normalize_sources((source_root,) if source_root else configured_sources(root))
    app.state.source_root = app.state.source_roots[0]
    app.state.default_threshold = configured_threshold(root) if default_threshold is None else default_threshold
    app.state.run = None
    app.state.catalog_building = False
    app.state.catalog_cancel = threading.Event()
    app.state.catalog_lock = threading.Lock()
    app.state.catalog = None
    try:
        saved = load_catalog(root / "catalog.json")
        if set(catalog_roots(saved)) == set(app.state.source_roots):
            app.state.catalog = saved
    except (OSError, ValueError, KeyError, TypeError):
        pass
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        page = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
        for asset in ("styles.css", "catalog-mode.css", "app.js"):
            version = hashlib.sha256((_STATIC_DIR / asset).read_bytes()).hexdigest()[:12]
            page = page.replace(f'/static/{asset}"', f'/static/{asset}?v={version}"')
        return page.replace("{{DEFAULT_THRESHOLD}}", f"{app.state.default_threshold:.2f}")

    @app.get("/api/settings")
    def settings() -> dict:
        catalog = app.state.catalog
        return {"source_root": str(app.state.source_root),
                "source_roots": list(map(str, app.state.source_roots)),
                "threshold": app.state.default_threshold,
                "catalog_methods": len(catalog.methods) if catalog else None,
                "catalog_health": catalog_health(catalog, app.state.source_roots)}

    @app.post("/api/settings")
    def update_settings(payload: dict) -> dict:
        if app.state.catalog_building:
            raise HTTPException(409, "Wait for catalog building to finish.")
        if app.state.run and app.state.run["active"]:
            raise HTTPException(409, "Wait for the active search to finish.")
        values = payload.get("source_roots", payload.get("source_root"))
        if isinstance(values, str): values = [line.strip() for line in values.splitlines() if line.strip()]
        if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
            raise HTTPException(422, "Enter library directories, one per line.")
        try: directories = normalize_sources(values)
        except ValueError as error: raise HTTPException(422, str(error)) from error
        threshold = payload.get("threshold", app.state.default_threshold)
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
            raise HTTPException(422, "Threshold must be a number from 0 through 1.")
        (root / "library-source.txt").write_text("\n".join(map(str, directories)), encoding="utf-8")
        config_path = root / "configs.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["confidence_threshold"] = threshold
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        if directories != app.state.source_roots:
            app.state.catalog = None
        app.state.source_roots = directories
        app.state.source_root = directories[0]
        app.state.default_threshold = threshold
        return settings()

    def build_catalog(report=None, mode="ai") -> dict:
        report = report or (lambda event: None)
        report({"type": "progress", "stage": "scanning"})
        if app.state.run and app.state.run["active"]:
            raise HTTPException(409, "Wait for the active search to finish.")
        scanned = scan_sources(app.state.source_roots, scanner=scanner)
        previous = app.state.catalog
        old = {(m.file, m.qualified_name): m for m in previous.methods} if previous else {}
        queued = sum(not (old.get((m.file, m.qualified_name)) and old[(m.file, m.qualified_name)].fingerprint == m.fingerprint and old[(m.file, m.qualified_name)].enrichment_status == "success" and old[(m.file, m.qualified_name)].enrichment) for m in scanned.methods)
        report({"type": "progress", "stage": "preparing", "queued": queued, "total": len(scanned.methods)})
        workers = configured_workers(root) if mode == "ai" else 1
        client = copilot_factory() if mode == "ai" and workers == 1 else None
        def checkpoint(catalog):
            save_catalog(catalog, root / "catalog.json")
            app.state.catalog = catalog
        deleted = None
        try:
            callbacks = dict(on_checkpoint=checkpoint, cancel_event=app.state.catalog_cancel,
                on_progress=lambda current, total, method, status: report({"type": "progress", "stage": "enriching", "current": current, "total": total, "method": method.qualified_name, "status": status}),
                on_error=lambda method, reason: report({"type": "method_error", "method": method.qualified_name, "message": reason}))
            if workers > 1:
                catalog, summary, deleted = enrich_parallel(scanned, previous, copilot_factory, workers,
                    on_worker=lambda event: report({"type": "worker_progress", **event}), **callbacks)
            else:
                catalog, summary = enrich_catalog(scanned, previous, client, **callbacks)
        finally:
            report({"type": "progress", "stage": "cleanup"})
            if client is not None: deleted = client.close()
        report({"type": "progress", "stage": "saving"})
        save_catalog(catalog, root / "catalog.json")
        app.state.catalog = catalog
        return {"methods": len(catalog.methods), "files": len(catalog.files),
                "errors": len(catalog.errors), "reused": summary.reused,
                "enriched": summary.enriched, "failed": summary.failed,
                "conversation_deleted": deleted, "pending": summary.pending,
                "interrupted": mode == "ai" and (bool(getattr(client, "interrupted", False)) or summary.pending > 0),
                "mode": mode, "cancelled": app.state.catalog_cancel.is_set()}

    @app.post("/api/catalog/cancel")
    def cancel_catalog() -> dict:
        with app.state.catalog_lock:
            if not app.state.catalog_building:
                raise HTTPException(409, "No catalog build is running.")
            app.state.catalog_cancel.set()
        return {"cancel_requested": True}

    @app.post("/api/catalog")
    def build_catalog_sync(mode: str = "ai") -> dict:
        if mode not in ("ai", "fast"): raise HTTPException(422, "Choose fast or ai catalog mode.")
        with app.state.catalog_lock:
            if app.state.catalog_building:
                raise HTTPException(409, "Catalog building is already running.")
            app.state.catalog_building = True
            app.state.catalog_cancel.clear()
        try:
            return build_catalog(mode=mode)
        finally:
            app.state.catalog_building = False

    @app.post("/api/catalog/stream")
    def stream_catalog(mode: str = "ai") -> StreamingResponse:
        if mode not in ("ai", "fast"): raise HTTPException(422, "Choose fast or ai catalog mode.")
        with app.state.catalog_lock:
            if app.state.catalog_building or (app.state.run and app.state.run["active"]):
                raise HTTPException(409, "Another operation is running.")
            app.state.catalog_building = True
            app.state.catalog_cancel.clear()
        updates = queue.Queue()
        def worker():
            try:
                result = build_catalog(updates.put, mode=mode)
                updates.put({"type": "complete", **result})
            except Exception:
                updates.put({"type": "error", "message": "Catalog build failed. Check the server log and retry."})
            finally:
                app.state.catalog_building = False
        threading.Thread(target=worker, daemon=True).start()
        def stream():
            while True:
                event = updates.get()
                yield json.dumps(event) + "\n"
                if event["type"] in ("complete", "error"): break
        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.post("/api/search")
    def search(payload: dict) -> dict:
        if app.state.catalog_building:
            raise HTTPException(409, "Wait for catalog building to finish.")
        goal = str(payload.get("goal", "")).strip()
        threshold = payload.get("threshold", app.state.default_threshold)
        if not goal:
            raise HTTPException(422, "A search goal is required.")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
            raise HTTPException(422, "Threshold must be a number from 0 through 1.")
        active = app.state.run
        if active is not None and active["active"]:
            raise HTTPException(409, "A search is already running.")
        run = {"id": uuid.uuid4().hex, "events": queue.Queue(), "active": True, "catalog": None, "total": 0}
        app.state.run = run

        def worker() -> None:
            try:
                catalog = app.state.catalog or scan_sources(app.state.source_roots, scanner=scanner)
                run["catalog"] = catalog
                total = len(catalog.methods)
                run["total"] = total

                def report(current, count, method, score) -> None:
                    run["events"].put({"type": "evaluation", "current": current, "total": count,
                                       "method": method.qualified_name, "file": method.file, "line": method.line,
                                       "score": score, "passed": score > threshold})

                ranker = (LayaRanker(engine=app.state.laya_engine, on_evaluation=report)
                          if ranker_factory is LayaRanker and app.state.laya_engine is not None
                          else ranker_factory(on_evaluation=report))
                result = find_methods(
                    goal,
                    catalog,
                    ranker,
                    raise_ranking_errors=True,
                )
                hits = sorted(({"method": item.method.qualified_name, "score": item.confidence}
                               for item in result.recommendations if item.confidence > threshold),
                              key=lambda item: item["score"], reverse=True)
                run["events"].put({"type": "complete", "total": total, "hits": hits})
            except Exception:
                run["events"].put({"type": "error", "message": "Search evaluation failed."})
                run["events"].put({"type": "complete", "total": run["total"], "hits": []})
            finally:
                run["active"] = False

        threading.Thread(target=worker, daemon=True).start()
        return {"run_id": run["id"]}

    @app.get("/api/search/{run_id}/events")
    def events(run_id: str) -> StreamingResponse:
        run = app.state.run
        if run is None or run["id"] != run_id:
            raise HTTPException(404, "Search run not found.")

        def stream():
            while True:
                event = run["events"].get()
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] == "complete":
                    break
        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/search/{run_id}/source")
    def source(run_id: str, method: str) -> dict:
        run = app.state.run
        if run is None or run["id"] != run_id or run["catalog"] is None:
            raise HTTPException(404, "Search run not found.")
        record = run["catalog"].method(method)
        if record is None:
            raise HTTPException(404, "Catalog method not found.")
        root_path = Path(app.state.source_root).resolve()
        source_path = (root_path / record.file).resolve()
        if source_path.suffix != ".py" or any(part.startswith(".env") for part in source_path.parts):
            raise HTTPException(403, "Only Python source previews are supported.")
        if not any(source_path != directory and directory in source_path.parents for directory in app.state.source_roots):
            raise HTTPException(403, "Source path is outside the configured library.")
        try:
            all_lines = source_path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise HTTPException(404, "Source file is unavailable.") from error
        start = max(1, record.line - 3)
        end = min(len(all_lines), record.line + 12)
        return {"path": record.file, "start_line": start,
                "lines": all_lines[start - 1:end]}

    return app


class BrowserReadyServer(uvicorn.Server):
    async def startup(self, sockets=None):
        # Uvicorn waits for the application lifespan (including Laya loading)
        # and binds its listening sockets before startup returns.
        await super().startup(sockets=sockets)
        if self.started:
            webbrowser.open("http://127.0.0.1:8000")


def run_server() -> None:
    server = BrowserReadyServer(uvicorn.Config(create_app(), host="127.0.0.1", port=8000))
    server.run()
