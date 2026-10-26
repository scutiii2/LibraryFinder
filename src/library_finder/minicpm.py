"""Local MiniCPM service transport for catalog enrichment (replaces the Copilot browser)."""
import os

from library_finder.copilot import (CopilotResponseError, CopilotUnavailable, build_prompt,
                                    parse_enrichment)
from library_finder.http_json import ServiceError, ServiceUnreachable, request_json

DEFAULT_URL = "http://127.0.0.1:9130"
# A 1B model gets a shorter source snippet than Copilot's 12,000 characters.
SOURCE_LIMIT = 6000
_TEXT = {"type": "string"}
_LIST = {"type": "array", "items": _TEXT}
SCHEMA = {"type": "object",
          "properties": {"summary": _TEXT, "purpose": _TEXT, "inputs": _LIST, "outputs": _LIST,
                         "side_effects": _LIST, "automation_context": _TEXT, "keywords": _LIST, "caveats": _LIST},
          "required": ["summary", "purpose", "inputs", "outputs", "side_effects", "automation_context", "keywords", "caveats"]}


class MiniCPMClient:
    """Same interface as CopilotClient; one method per request, no browser, nothing to clean up."""
    conversation_url = None

    def __init__(self, url=None, token=None, timeout=180, max_tokens=2048):
        self.url = (url or DEFAULT_URL).rstrip("/")
        self.token = token if token is not None else os.environ.get("MINICPM_TOKEN")
        self.timeout, self.max_tokens = timeout, max_tokens
        self.interrupted = False

    def enrich(self, method):
        last = None
        prompt = build_prompt(method, SOURCE_LIMIT)
        for _ in range(2):  # one bounded retry, only for a reply that does not parse
            reply = self._chat(prompt)
            try:
                return parse_enrichment(reply)
            except CopilotResponseError as error:
                last = error
        raise last

    def _chat(self, prompt):
        payload = {"messages": [{"role": "user", "content": prompt}], "schema": SCHEMA,
                   "thinking": False, "temperature": 0, "max_tokens": self.max_tokens}
        try:
            body = request_json("POST", self.url + "/v1/chat", payload, token=self.token, timeout=self.timeout)
        except ServiceUnreachable as error:
            self.interrupted = True
            raise CopilotUnavailable(f"MiniCPM service is not reachable at {self.url}. Start it and rebuild.") from error
        except ServiceError as error:
            if error.status in (401, 502):
                self.interrupted = True
                raise CopilotUnavailable(f"MiniCPM service refused the request ({error}). Build paused.") from error
            raise CopilotResponseError(f"MiniCPM request failed ({error})") from error
        if body.get("finish_reason") == "length":
            raise CopilotResponseError("MiniCPM ran out of tokens before finishing the reply")
        return body.get("content") or ""

    def close(self):
        return True
