from __future__ import annotations

import threading
import json

import pytest
from fastapi import HTTPException

from library_finder.models import Catalog, MethodRecord
from library_finder.web import create_app


@pytest.fixture(autouse=True)
def isolated_web_storage(tmp_path, monkeypatch):
    """Every web test must write only inside its own temporary directory."""
    storage = tmp_path / "web-data"
    storage.mkdir()
    (storage / "configs.json").write_text('{"confidence_threshold":0.65,"copilot_workers":1}')
    monkeypatch.setattr("library_finder.web._PROJECT_DIR", storage)


def _method(name: str, file: str = "driver.py", line: int = 1) -> MethodRecord:
    return MethodRecord(name, "libs", file, line, "public", None, (), (), None, (), None, (), (), ())


def test_laya_is_loaded_once_during_server_startup(tmp_path):
    import asyncio
    engine = object()
    calls = []
    def load():
        calls.append(1)
        return engine
    app = create_app(source_root=tmp_path, model_loader=load)
    assert calls == []
    async def start():
        async with app.router.lifespan_context(app):
            assert app.state.laya_engine is engine
            assert calls == [1]
    asyncio.run(start())
    assert app.state.laya_engine is None


def test_browser_opens_only_after_server_startup_completes(monkeypatch):
    import asyncio
    import uvicorn
    from library_finder.web import BrowserReadyServer
    order = []
    async def startup(server, sockets=None):
        order.append("model loaded and server listening")
        server.started = True
    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    monkeypatch.setattr("library_finder.web.webbrowser.open", lambda url: order.append("browser opened"))
    server = BrowserReadyServer(uvicorn.Config("unused"))
    asyncio.run(server.startup())
    assert order == ["model loaded and server listening", "browser opened"]


def test_browser_does_not_open_if_startup_is_not_ready(monkeypatch):
    import asyncio
    import uvicorn
    from library_finder.web import BrowserReadyServer
    async def startup(server, sockets=None): pass
    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    monkeypatch.setattr("library_finder.web.webbrowser.open", lambda url: pytest.fail("Server is not ready"))
    asyncio.run(BrowserReadyServer(uvicorn.Config("unused")).startup())


def _catalog(*methods: MethodRecord) -> Catalog:
    return Catalog(1, "libs", (), methods, (), "now")


def _endpoint(app, path: str):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


def _events(app) -> str:
    events = []
    while not events or events[-1]["type"] != "complete":
        events.append(app.state.run["events"].get(timeout=2))
    return "\n".join(json.dumps(event) for event in events)


def test_create_app_serves_configured_dashboard_shell(tmp_path):
    app = create_app(source_root=tmp_path, default_threshold=0.62)
    response = _endpoint(app, "/")()

    assert "Library Finder" in response
    assert 'id="goal"' in response
    assert 'id="threshold"' in response
    assert 'value="0.62"' in response
    assert 'id="progress"' in response
    assert 'id="results"' in response
    assert 'id="hits"' in response
    assert 'id="code"' in response


def test_catalog_enriches_with_copilot_and_reports_cleanup(tmp_path):
    catalog = _catalog(_method("libs.Driver.find"))

    class Client:
        conversation_url = "https://m365.cloud.microsoft/chat/conversation/current"
        def enrich(self, method):
            from library_finder.models import CopilotEnrichment
            return CopilotEnrichment("summary", "purpose", (), (), (), "context", (), ())
        def close(self):
            return True

    app = create_app(source_root=tmp_path, scanner=lambda _: catalog, copilot_factory=Client)
    result = _endpoint(app, "/api/catalog")()

    assert result == {"methods": 1, "files": 0, "errors": 0, "reused": 0,
                      "enriched": 1, "failed": 0, "conversation_deleted": True, "pending": 0, "interrupted": False, "mode": "ai", "cancelled": False}


def test_cancel_catalog_sets_shared_event_only_during_active_build(tmp_path):
    app = create_app(source_root=tmp_path)
    cancel = _endpoint(app, "/api/catalog/cancel")
    with pytest.raises(HTTPException): cancel()
    app.state.catalog_building = True
    assert cancel() == {"cancel_requested": True}
    assert app.state.catalog_cancel.is_set()


def test_fast_catalog_does_not_open_copilot(tmp_path, monkeypatch):
    monkeypatch.setattr("library_finder.web.save_catalog", lambda *args: None)
    def forbidden(): raise AssertionError("Fast mode must not open Copilot")
    app = create_app(source_root=tmp_path, scanner=lambda _: _catalog(_method("libs.find")), copilot_factory=forbidden)
    result = _endpoint(app, "/api/catalog")("fast")
    assert result["pending"] == 1
    assert result["enriched"] == 0
    assert result["interrupted"] is False
    assert result["conversation_deleted"] is None
    assert app.state.catalog.methods[0].enrichment_status == "pending"


def test_stream_catalog_reports_method_cleanup_and_completion(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr("library_finder.web.save_catalog", lambda *args: None)
    class Client:
        def enrich(self, method):
            from library_finder.models import CopilotEnrichment
            return CopilotEnrichment("summary", "purpose", (), (), (), "context", (), ())
        def close(self): return True
    app = create_app(source_root=tmp_path, scanner=lambda _: _catalog(_method("libs.find")), copilot_factory=Client)
    response = _endpoint(app, "/api/catalog/stream")()
    async def collect():
        return [json.loads(chunk) async for chunk in response.body_iterator]
    events = asyncio.run(collect())
    events = [event for event in events if event["type"] != "worker_progress"]
    assert [event.get("stage", event["type"]) for event in events] == ["scanning", "preparing", "enriching", "cleanup", "saving", "complete"]
    assert events[1]["queued"] == 1
    assert events[2]["current"] == 1
    assert events[-1]["enriched"] == 1


def test_search_streams_catalog_order_and_strict_sorted_hits(tmp_path):
    (tmp_path / "driver.py").write_text("def first(): pass\ndef second(): pass\n")
    first, second = _method("libs.Driver.first"), _method("libs.Driver.second", line=2)
    catalog = _catalog(first, second)

    class Ranker:
        def __init__(self, on_evaluation=None): self.on_evaluation = on_evaluation
        def rank(self, goal, methods):
            self.on_evaluation(1, 2, methods[0], 0.50)
            self.on_evaluation(2, 2, methods[1], 0.80)
            return {"recommendations": [
                {"method": first.qualified_name, "confidence": 0.50, "reason": "Laya"},
                {"method": second.qualified_name, "confidence": 0.80, "reason": "Laya"},
            ]}

    app = create_app(source_root=tmp_path, default_threshold=0.5,
                     scanner=lambda _: catalog, ranker_factory=Ranker)
    started = _endpoint(app, "/api/search")({"goal": "find"})
    assert _endpoint(app, "/api/search/{run_id}/events")(started["run_id"]).media_type == "text/event-stream"
    events = _events(app)

    assert events.index(first.qualified_name) < events.index(second.qualified_name)
    assert '"passed": false' in events
    assert '"hits": [{"method": "libs.Driver.second", "score": 0.8}]' in events


def test_search_rejects_invalid_requests_and_concurrent_run(tmp_path):
    started = threading.Event()
    release = threading.Event()
    catalog = _catalog(_method("libs.Driver.find"))

    class SlowRanker:
        def __init__(self, on_evaluation=None): self.on_evaluation = on_evaluation
        def rank(self, goal, methods):
            started.set()
            assert release.wait(timeout=2)
            self.on_evaluation(1, 1, methods[0], 0.7)
            return {"recommendations": [{"method": methods[0].qualified_name, "confidence": 0.7, "reason": "Laya"}]}

    app = create_app(source_root=tmp_path, scanner=lambda _: catalog, ranker_factory=SlowRanker)
    search = _endpoint(app, "/api/search")
    with pytest.raises(HTTPException, match="goal") as blank:
        search({"goal": "", "threshold": 0.5})
    assert blank.value.status_code == 422
    with pytest.raises(HTTPException, match="Threshold") as invalid:
        search({"goal": "x", "threshold": True})
    assert invalid.value.status_code == 422
    first = search({"goal": "x", "threshold": 0.5})
    assert started.wait(timeout=2)
    with pytest.raises(HTTPException, match="already") as conflict:
        search({"goal": "y", "threshold": 0.5})
    assert conflict.value.status_code == 409
    release.set()
    assert '"type": "complete"' in _events(app)


def test_search_reports_laya_error_then_completes(tmp_path):
    catalog = _catalog(_method("libs.Driver.find"))

    class BrokenRanker:
        def __init__(self, on_evaluation=None): pass
        def rank(self, goal, methods): raise RuntimeError("model unavailable")

    app = create_app(source_root=tmp_path, scanner=lambda _: catalog, ranker_factory=BrokenRanker)
    run_id = _endpoint(app, "/api/search")({"goal": "x"})["run_id"]
    events = _events(app)
    assert '"type": "error"' in events
    assert '"type": "complete"' in events
    assert "model unavailable" not in events


def test_source_preview_is_bounded_and_rejects_unknown_or_outside_records(tmp_path):
    (tmp_path / "driver.py").write_text("# one\n# two\ndef find():\n    return 'ok'\n# five\n")
    known = _method("libs.Driver.find", line=3)
    outside = _method("libs.Driver.outside", "../secret.py")
    catalog = _catalog(known, outside)

    class Ranker:
        def __init__(self, on_evaluation=None): self.on_evaluation = on_evaluation
        def rank(self, goal, methods): return {"recommendations": []}

    app = create_app(source_root=tmp_path, scanner=lambda _: catalog, ranker_factory=Ranker)
    run_id = _endpoint(app, "/api/search")({"goal": "x"})["run_id"]
    _events(app)
    source = _endpoint(app, "/api/search/{run_id}/source")
    preview = source(run_id, known.qualified_name)

    assert preview["start_line"] == 1
    assert any("def find" in line for line in preview["lines"])
    with pytest.raises(HTTPException) as missing:
        source(run_id, "missing")
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as external:
        source(run_id, outside.qualified_name)
    assert external.value.status_code == 403
