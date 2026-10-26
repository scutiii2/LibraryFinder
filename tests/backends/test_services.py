import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backends.factory import enrichment_factory, ranker_factory
from settings import configured_backends
from backends.copilot import CopilotClient, CopilotResponseError, CopilotUnavailable
from backends.laya import LayaRanker, LayaServiceRanker
from backends.minicpm import MiniCPMClient
from core.models import MethodRecord

GOOD = {"summary": "s", "purpose": "p", "inputs": [], "outputs": [], "side_effects": [],
        "automation_context": "c", "keywords": ["k"], "caveats": []}


def _method(name="libs.find"):
    return MethodRecord(name, "libs", "driver.py", 1, "public", None, (), (), None, (), None, (), (), ())


@pytest.fixture
def service():
    """Fake HTTP service: set .replies (list of (status, body)); requests are recorded in .seen."""
    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            server.seen.append((self.command, self.path, body, self.headers.get("X-Internal-Token")))
            status, payload = server.replies.pop(0) if server.replies else (200, {})
            data = json.dumps(payload).encode()
            self.send_response(status); self.send_header("Content-Length", str(len(data))); self.end_headers()
            self.wfile.write(data)
        do_GET = do_POST = _answer
        def log_message(self, *args): pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.seen, server.replies = [], []
    server.url = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def test_minicpm_returns_enrichment_and_sends_schema_request(service):
    service.replies = [(200, {"content": json.dumps(GOOD), "finish_reason": "stop"})]
    result = MiniCPMClient(service.url, token="t").enrich(_method())
    assert result.summary == "s" and result.keywords == ("k",)
    method, path, body, token = service.seen[0]
    assert (method, path, token) == ("POST", "/v1/chat", "t")
    assert body["schema"]["required"] and body["thinking"] is False


def test_minicpm_retries_once_on_unparseable_reply(service):
    service.replies = [(200, {"content": "not json", "finish_reason": "stop"}),
                       (200, {"content": json.dumps(GOOD), "finish_reason": "stop"})]
    assert MiniCPMClient(service.url).enrich(_method()).purpose == "p"
    assert len(service.seen) == 2


def test_minicpm_reports_token_budget_exhaustion(service):
    service.replies = [(200, {"content": "", "finish_reason": "length"})] * 2
    with pytest.raises(CopilotResponseError, match="tokens"):
        MiniCPMClient(service.url).enrich(_method())


def test_minicpm_service_down_pauses_the_build():
    client = MiniCPMClient("http://127.0.0.1:1", timeout=2)
    with pytest.raises(CopilotUnavailable, match="not reachable"):
        client.enrich(_method())
    assert client.interrupted is True
    assert client.close() is True


def test_minicpm_bad_request_fails_only_that_method(service):
    service.replies = [(422, {"detail": "bad"})]
    client = MiniCPMClient(service.url)
    with pytest.raises(CopilotResponseError, match="422"):
        client.enrich(_method())
    assert client.interrupted is False


def test_laya_service_ranker_asks_service_and_reads_noul(service):
    service.replies = [(200, {"status": "ok"}), (200, {"answers": {"fit": {"noul": 0.8}}}),
                       (200, {"answers": {"fit": {"noul": 0.2}}})]
    result = LayaServiceRanker(service.url, token="t").rank("find", (_method("libs.a"), _method("libs.b")))
    assert [r["confidence"] for r in result["recommendations"]] == [0.8, 0.2]
    asks = [item for item in service.seen if item[1] == "/v1/ask"]
    assert asks[0][2]["strategy"] == "auto" and asks[0][3] == "t"


def test_laya_service_ranker_raises_when_service_is_down():
    with pytest.raises(RuntimeError, match="not reachable"):
        LayaServiceRanker("http://127.0.0.1:1").prepare()


def test_backends_default_to_local_copilot_and_laya(tmp_path):
    assert configured_backends(tmp_path)["enrichment_backend"] == "copilot"
    assert enrichment_factory(tmp_path) is CopilotClient
    assert ranker_factory(tmp_path) is LayaRanker


def test_backends_switch_to_services_by_config(tmp_path):
    (tmp_path / "configs.json").write_text(json.dumps({
        "enrichment_backend": "minicpm", "minicpm_url": "http://box:9130",
        "laya_backend": "service", "laya_url": "http://box:9120"}))
    client = enrichment_factory(tmp_path)()
    assert isinstance(client, MiniCPMClient) and client.url == "http://box:9130"
    assert isinstance(ranker_factory(tmp_path)(on_evaluation=None), LayaServiceRanker)


def test_unknown_backend_is_rejected(tmp_path):
    (tmp_path / "configs.json").write_text(json.dumps({"enrichment_backend": "gpt"}))
    with pytest.raises(ValueError, match="enrichment_backend"):
        configured_backends(tmp_path)


def test_env_file_supplies_urls_and_tokens_without_overriding_real_environment(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(os, "environ", {"LAYA_URL": "http://real:9120"})
    (tmp_path / "configs.json").write_text(json.dumps({"enrichment_backend": "minicpm", "minicpm_url": "http://cfg:1"}))
    (tmp_path / ".env").write_text("# comment\nMINICPM_URL=http://10.0.0.5:9130\nexport MINICPM_TOKEN=\"tok\"\nLAYA_URL=http://file:9120\n")
    settings = configured_backends(tmp_path)
    assert settings["minicpm_url"] == "http://10.0.0.5:9130"
    assert settings["laya_url"] == "http://real:9120"
    assert os.environ["MINICPM_TOKEN"] == "tok"
    assert enrichment_factory(tmp_path)().token == "tok"


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8-sig", "utf-8"])
def test_env_file_accepts_powershell_and_bom_encodings(tmp_path, monkeypatch, encoding):
    import os
    monkeypatch.setattr(os, "environ", {})
    (tmp_path / ".env").write_bytes("MINICPM_URL=http://10.0.0.5:9130\r\n".encode(encoding))
    assert configured_backends(tmp_path)["minicpm_url"] == "http://10.0.0.5:9130"


def test_laya_service_ranker_omits_labels_the_service_rejects(service):
    service.replies = [(200, {"status": "ok"}), (200, {"answers": {"fit": {"noul": 0.7}}})]
    LayaServiceRanker(service.url).rank("find", (_method(),))
    ask = next(item for item in service.seen if item[1] == "/v1/ask")
    assert "labels" not in ask[2]["questions"]["fit"]


def test_gateway_errors_are_retried_then_succeed(service, monkeypatch):
    monkeypatch.setattr("backends.http_json.time.sleep", lambda _: None)
    service.replies = [(502, {}), (503, {}), (200, {"content": json.dumps(GOOD), "finish_reason": "stop"})]
    assert MiniCPMClient(service.url).enrich(_method()).summary == "s"
    assert len(service.seen) == 3


def test_minicpm_prompt_has_no_template_values_to_copy():
    from backends.minicpm import build_minicpm_prompt
    prompt = build_minicpm_prompt(_method())
    assert "parameter: description" not in prompt and "short summary" not in prompt
