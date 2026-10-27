"""Local MiniCPM service transport for catalog enrichment.

A 1B model writes plausible summaries and keywords but invents inputs, outputs, side effects and
caveats. It is therefore asked for four fields only; the rest come from the scanner's facts."""
import ast
import json
import os
import textwrap

from backends.errors import EnrichmentResponseError, EnrichmentUnavailable
from enrichment.parsing import parse_enrichment
from backends.http_json import ServiceError, ServiceUnreachable, request_json

DEFAULT_URL = "http://127.0.0.1:9130"
# Bound the source snippet for the 1B model.
SOURCE_LIMIT = 6000
_TEXT = {"type": "string"}
SCHEMA = {"type": "object",
          "properties": {"summary": _TEXT, "purpose": _TEXT, "automation_context": _TEXT,
                         "keywords": {"type": "array", "items": _TEXT}},
          "required": ["summary", "purpose", "automation_context", "keywords"]}

INTRO = "Document this Python method as a JSON object. Use only what the code shows.\nMethod facts: "

# One worked example, sent as an earlier chat turn. A 1B model follows the shape of an example far better
# than the wording of instructions, which it tends to copy into the answer.
_EXAMPLE_FACTS = {
    "qualified_name": "libs.drivers.PageDriver.PageDriver.wait_for_title", "signature": ["self", "expected", "timeout"],
    "docstring": "Wait until the page title equals expected.", "effects": ["webdriver"],
    "source": ("def wait_for_title(self, expected, timeout=10):\n    \"\"\"Wait until the page title equals expected.\"\"\"\n"
               "    try:\n        WebDriverWait(self.driver, timeout).until(lambda d: d.title == expected)\n"
               "        return True\n    except TimeoutException:\n        return False\n")}
_EXAMPLE_ANSWER = {
    "summary": "Waits until the page title equals the expected text and reports whether that happened.",
    "purpose": "Confirms that navigation finished on the page with the expected title.",
    "automation_context": "Used after a click or navigation to confirm that the right page loaded.",
    "keywords": ["wait", "title", "page", "timeout", "navigation"]}


def _facts(method):
    return {"qualified_name": method.qualified_name, "signature": method.parameters, "docstring": method.docstring,
            "effects": method.effects, "source": method.source[:SOURCE_LIMIT]}


def build_minicpm_prompt(method):
    """The final user turn: the facts of the method to document."""
    return INTRO + json.dumps(_facts(method))


def build_minicpm_messages(method):
    return [{"role": "user", "content": INTRO + json.dumps(_EXAMPLE_FACTS)},
            {"role": "assistant", "content": json.dumps(_EXAMPLE_ANSWER)},
            {"role": "user", "content": build_minicpm_prompt(method)}]


class _Returns(ast.NodeVisitor):
    """Finds `return <value>` in a function, ignoring nested functions and lambdas."""
    def __init__(self): self.value = False
    def visit_Return(self, node):
        if node.value is not None and not (isinstance(node.value, ast.Constant) and node.value.value is None):
            self.value = True
    def visit_FunctionDef(self, node): pass
    visit_AsyncFunctionDef = visit_Lambda = visit_FunctionDef


def returns_a_value(source):
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return False
    function = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if function is None:
        return False
    finder = _Returns()
    for statement in function.body:
        finder.visit(statement)
    return finder.value


def scanner_fields(method):
    """inputs, outputs and side_effects from the scanner's facts and the code, not from the model."""
    inputs = [name for name in method.parameters if name not in ("self", "cls")]
    if method.return_annotation:
        outputs = [f"Returns {method.return_annotation}"]
    elif returns_a_value(method.source):
        outputs = ["Returns a value"]
    else:
        outputs = []
    return inputs, outputs, list(method.effects)


_NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}
# Fragments of wording the model is known to copy back; text containing one is rejected or dropped.
_ECHOES = ("nothing to report", "no results", "[]", "developer might", "one sentence", "the method name", "the docstring")


def _is_echo(item):
    text = item.strip().lower()
    return not text or text.startswith("empty ") or "**" in text or any(fragment in text for fragment in _ECHOES)


def build_enrichment(value, method):
    """Combine the model's four fields with the scanner fields into a full enrichment."""
    if not isinstance(value, dict):
        raise EnrichmentResponseError("MiniCPM response must be a JSON object")
    for field in ("summary", "purpose", "automation_context"):
        text = value.get(field)
        if not isinstance(text, str) or _is_echo(text):
            raise EnrichmentResponseError("MiniCPM did not describe the method")
    keywords = value.get("keywords") if isinstance(value.get("keywords"), list) else []
    keywords = list(dict.fromkeys(k.strip() for k in keywords if isinstance(k, str) and k.strip().lower() not in _NUMBER_WORDS
                                  and not _is_echo(k) and len(k.split()) <= 3))
    inputs, outputs, side_effects = scanner_fields(method)
    return parse_enrichment(json.dumps({
        "summary": value["summary"], "purpose": value["purpose"], "automation_context": value["automation_context"],
        "keywords": keywords, "inputs": inputs, "outputs": outputs, "side_effects": side_effects, "caveats": []}))


def parse_reply(reply, method):
    try:
        value = json.loads(reply)
    except (TypeError, ValueError) as error:
        raise EnrichmentResponseError("MiniCPM did not return JSON") from error
    return build_enrichment(value, method)


class MiniCPMClient:
    """One method per service request."""

    def __init__(self, url=None, token=None, timeout=180, max_tokens=1024):
        self.url = (url or DEFAULT_URL).rstrip("/")
        self.token = token if token is not None else os.environ.get("MINICPM_TOKEN")
        self.timeout, self.max_tokens = timeout, max_tokens
        self.interrupted = False

    def enrich(self, method):
        last = None
        messages = build_minicpm_messages(method)
        for _ in range(2):  # one bounded retry, only for a reply that does not parse
            reply = self._chat(messages)
            try:
                return parse_reply(reply, method)
            except EnrichmentResponseError as error:
                last = error
        raise last

    def _chat(self, messages):
        payload = {"messages": messages, "schema": SCHEMA, "thinking": False, "temperature": 0,
                   "max_tokens": self.max_tokens}
        try:
            body = request_json("POST", self.url + "/v1/chat", payload, token=self.token, timeout=self.timeout)
        except ServiceUnreachable as error:
            self.interrupted = True
            raise EnrichmentUnavailable(f"MiniCPM service is not reachable at {self.url}. Start it and rebuild.") from error
        except ServiceError as error:
            if error.status == 401 or (error.status == 502 and not error.detail):
                self.interrupted = True
                raise EnrichmentUnavailable(f"MiniCPM service refused the request ({error}). Build paused.") from error
            raise EnrichmentResponseError(f"MiniCPM request failed ({error})") from error
        if body.get("finish_reason") == "length":
            raise EnrichmentResponseError("MiniCPM ran out of tokens before finishing the reply")
        return body.get("content") or ""

    def close(self):
        return True
