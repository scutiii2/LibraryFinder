from dataclasses import replace

import pytest
from fastapi import HTTPException

from core.catalog import load_catalog, save_catalog
from core.models import Enrichment
from test_web import _catalog, _endpoint, _method, isolated_web_storage
from web.app import create_app


def app_with_catalog(tmp_path, *methods):
    app = create_app(source_root=tmp_path)
    app.state.catalog = replace(_catalog(*methods), source_root=str(tmp_path))
    return app


def payload(**changes):
    return dict(summary="Summary", purpose="Purpose", automation_context="Context", keywords=["ticket"], caveats=[], **changes)


def test_list_filters_paginates_and_returns_scanner_context(tmp_path):
    methods = [_method(f"libs.find{i}") for i in range(120)]
    methods[0] = replace(methods[0], enrichment_status="error")
    app = app_with_catalog(tmp_path, *methods)
    listing = _endpoint(app, "/api/catalog/methods")
    page = listing(query="find", status="", offset=50, limit=50)
    assert page["total"] == 120 and len(page["methods"]) == 50
    assert page["methods"][0]["qualified_name"] == "libs.find50"
    assert listing(status="failed")["total"] == 1
    assert listing(status="pending")["total"] == 119
    assert listing(query="absent")["total"] == 0
    assert "inputs" in page["methods"][0]["description"]
    with pytest.raises(HTTPException): listing(limit=101)
    with pytest.raises(HTTPException): listing(status="bad")


@pytest.mark.parametrize("existing", [False, True])
def test_save_create_edit_clear_and_roundtrip(tmp_path, existing):
    method = replace(_method("libs.find"), fingerprint="v1", parameters=("self", "ticket"), effects=("network",))
    if existing:
        method = replace(method, enrichment=Enrichment("AI", "AI purpose", ("original",), (), (), "", (), ()), enrichment_status="success")
    app = app_with_catalog(tmp_path, method)
    update = _endpoint(app, "/api/catalog/methods/{qualified_name:path}/description")
    result = update(method.qualified_name, payload())
    assert result["status"] == "manual"
    saved = app.state.catalog.methods[0]
    assert saved.enrichment.source == "manual" and saved.enrichment.edited_fingerprint == "v1"
    assert saved.enrichment.inputs == (("original",) if existing else ("ticket",))
    assert load_catalog(tmp_path / "web-data" / "data" / "catalog.json") == app.state.catalog
    listing = _endpoint(app, "/api/catalog/methods")
    assert listing(status="manual")["total"] == 1
    update(method.qualified_name, {"clear": True})
    assert app.state.catalog.methods[0].enrichment is None
    assert app.state.catalog.methods[0].enrichment_status == "pending"


@pytest.mark.parametrize("changes", [{"summary":" "}, {"purpose":4}, {"summary":"x"*2001}, {"keywords":["x"]*31}, {"keywords":["x"*61]}, {"keywords":"x"}, {"automation_context":None}, {"caveats":[3]}, {"clear":"yes"}])
def test_invalid_description_is_rejected_without_mutation(tmp_path, changes):
    app = app_with_catalog(tmp_path, _method("libs.find"))
    before = app.state.catalog
    update = _endpoint(app, "/api/catalog/methods/{qualified_name:path}/description")
    value = payload(); value.update(changes)
    with pytest.raises(HTTPException) as error: update("libs.find", value)
    assert error.value.status_code == 422
    assert app.state.catalog == before


@pytest.mark.parametrize("operation", ["build", "search"])
def test_description_rejects_busy_operations(tmp_path, operation):
    app = app_with_catalog(tmp_path, _method("libs.find"))
    app.state.catalog_building = operation == "build"
    app.state.run = {"active": operation == "search"}
    with pytest.raises(HTTPException) as error:
        _endpoint(app, "/api/catalog/methods/{qualified_name:path}/description")("libs.find", payload())
    assert error.value.status_code == 409


def test_old_enrichment_loads_and_new_fields_roundtrip(tmp_path):
    enrichment = Enrichment("s", "p", (), (), (), "", (), ())
    catalog = _catalog(replace(_method("f"), enrichment=enrichment))
    value = catalog.to_dict()
    value["methods"][0]["enrichment"].pop("source", None)
    value["methods"][0]["enrichment"].pop("edited_fingerprint", None)
    assert type(catalog).from_dict(value).methods[0].enrichment.source == "ai"
    destination = tmp_path / "roundtrip.json"
    save_catalog(catalog, destination)
    assert load_catalog(destination) == catalog


@pytest.mark.parametrize("file, code", [("driver.py",200), ("../outside.py",403), (".env/hidden.py",403), ("notes.txt",403), ("missing.py",404)])
def test_catalog_source_safety_without_search(tmp_path, file, code):
    (tmp_path / "driver.py").write_text("def find(): pass\n")
    app = app_with_catalog(tmp_path, _method("libs.find", file))
    preview = _endpoint(app, "/api/catalog/source")
    if code == 200:
        assert preview("libs.find")["lines"] == ["def find(): pass"]
    else:
        with pytest.raises(HTTPException) as error: preview("libs.find")
        assert error.value.status_code == code


def test_save_failure_does_not_publish_catalog(tmp_path, monkeypatch):
    app = app_with_catalog(tmp_path, _method("libs.find"))
    previous = app.state.catalog
    def fail(*args): raise OSError("disk full")
    monkeypatch.setattr("web.catalog_editor.save_catalog", fail)
    with pytest.raises(OSError):
        _endpoint(app, "/api/catalog/methods/{qualified_name:path}/description")("libs.find", payload())
    assert app.state.catalog is previous


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("overwrite_manual, expected", [(False, "manual"), (True, "ai")])
def test_build_api_manual_overwrite_opt_in(tmp_path, stream, overwrite_manual, expected):
    import asyncio
    import json
    method = replace(_method("libs.find"), fingerprint="new")
    manual = Enrichment("s", "p", (), (), (), "", (), (), source="manual", edited_fingerprint="old")
    class Client:
        def enrich(self, method): return Enrichment("ai", "p", (), (), (), "", (), ())
        def close(self): pass
    app = create_app(source_root=tmp_path, scanner=lambda _: _catalog(method), client_factory=Client)
    app.state.catalog = _catalog(replace(method, enrichment=manual, enrichment_status="success"))
    response = _endpoint(app, "/api/catalog/stream" if stream else "/api/catalog")(overwrite=True, overwrite_manual=overwrite_manual)
    if stream:
        async def collect(): return [json.loads(chunk) async for chunk in response.body_iterator]
        events = asyncio.run(collect())
        assert events[-1]["type"] == "complete"
        assert events[1]["queued"] == int(overwrite_manual)
    assert app.state.catalog.methods[0].enrichment.source == expected
