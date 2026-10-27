"""Local, opt-in Microsoft 365 Copilot transport for catalog enrichment."""
import json
import time
import re
import subprocess
import urllib.request
from threading import Lock
from pathlib import Path
from urllib.parse import urlparse

from backends.errors import EnrichmentUnavailable, EnrichmentResponseError, EnrichmentTimeout, EnrichmentCancelled
from enrichment.parsing import decode_reply, parse_enrichment
from settings import DATA_DIR
from core.models import Enrichment, MethodRecord

M365_URL = "https://m365.cloud.microsoft/chat"
_BROWSER_START_LOCK = Lock()





def browser_failure_reason(error):
    message = str(error).lower()
    if "strict mode violation" in message:
        return "The page matched multiple controls instead of one."
    if "closed" in message and any(word in message for word in ("page", "target", "browser", "context")):
        return "The Copilot tab or browser connection was closed."
    if "detached" in message:
        return "Copilot replaced the control during the operation."
    if "timeout" in type(error).__name__.lower():
        return "The control did not become ready before the timeout."
    return "The browser operation failed; its cause could not be classified."


def attribute_or_none(locator, name, timeout=3000):
    """An attribute, or None when Copilot re-rendered the element away (a default wait would hang 30s)."""
    try: return locator.get_attribute(name, timeout=timeout)
    except Exception: return None


def send_prompt(page):
    # Copilot can retain hidden copies of its composer during layout updates.
    send = page.locator('button[aria-label="Send"]:visible')
    send.first.wait_for(state="visible", timeout=30000)
    if send.count() != 1:
        raise EnrichmentUnavailable("Multiple visible Copilot Send buttons found. Build paused before sending.")
    # Playwright waits for this unique control to be enabled and actionable.
    send.click(timeout=30000)


def wait_for_reply(read, previous_answers, previous_done, *, timeout_s=120,
                   quiet_s=1, stuck_busy_s=10, previous_text="", is_busy=lambda: False,
                   should_cancel=lambda: False,
                   now=time.monotonic, sleep=time.sleep):
    deadline = now() + timeout_s
    last = ""
    changed = now()
    complete_json = False
    busy = False
    saw_new_reply = False
    while now() < deadline:
        if should_cancel(): raise EnrichmentCancelled("Catalog build cancelled.")
        answers, done, text = read()
        if answers > previous_answers or (text and text != previous_text):
            saw_new_reply = True
            if text != last:
                last, changed = text, now()
            # Copilot can recycle answer nodes and Copy buttons. A complete JSON
            # object plus stable text and an idle UI also proves completion.
            complete_json = False
            try:
                complete_json = isinstance(decode_reply(last), dict)
            except (ValueError, TypeError):
                pass
            marker_complete = answers > previous_answers and done > previous_done
            required_quiet = quiet_s if marker_complete else max(3, quiet_s)
            busy = is_busy()
            # A Stop control can stay visible after a Copilot re-render (theme change,
            # hidden copies). Complete JSON unchanged for stuck_busy_s outranks it.
            stuck_busy = complete_json and now() - changed >= stuck_busy_s
            if last and (marker_complete or complete_json) and now() - changed >= required_quiet and (not busy or stuck_busy):
                return last
        sleep(0.5)
    raise EnrichmentTimeout(f"Copilot timed out waiting for the new reply to finish (new reply detected: {saw_new_reply}; complete JSON: {complete_json}; visible generation active: {busy})")


def is_generating(page):
    controls = page.locator('button[aria-label="Stop generating"], button[aria-label="Stop generation"], button[aria-label="Stop response"]')
    return any(control.is_visible() for control in controls.all())


def build_prompt(method: MethodRecord, source_limit: int = 12000) -> str:
    if isinstance(method, tuple):
        return build_batch_prompt(method)
    shape = {"summary":"short summary", "purpose":"purpose", "inputs":["parameter: description"], "outputs":["return value description"], "side_effects":["effect"], "automation_context":"context", "keywords":["keyword"], "caveats":["limitation"]}
    return "Return ONLY a JSON object matching this structure. summary, purpose, automation_context must be strings. inputs, outputs, side_effects, keywords, caveats must be arrays of strings; use [] when empty. No Markdown fences or extra keys. Infer cautiously from the source; do not invent APIs. Expected structure: " + json.dumps(shape) + "\nMethod to analyze: " + json.dumps({"qualified_name": method.qualified_name, "signature": method.parameters, "docstring": method.docstring, "effects": method.effects, "source": method.source[:source_limit]})


def build_batch_prompt(methods):
    if not 1 <= len(methods) <= 5: raise ValueError("A batch must contain 1–5 methods")
    shape = {m.qualified_name: {"summary":"", "purpose":"", "inputs":[], "outputs":[],
        "side_effects":[], "automation_context":"", "keywords":[], "caveats":[]} for m in methods}
    return ('Analyze each supplied method independently. Return ONLY one JSON object keyed by the EXACT qualified_name of each method. '
        'Each value must contain summary, purpose, automation_context (strings), and inputs, outputs, side_effects, keywords, caveats (arrays of strings). '
        'Include every supplied method exactly once. Do not shorten the keys or add a wrapper. '
        'Fill in this exact response template: ' + json.dumps(shape) + '\nMethods: ' +
        json.dumps([{"qualified_name": m.qualified_name, "signature": m.parameters, "docstring": m.docstring,
                     "effects": m.effects, "source": m.source[:12000]} for m in methods]))


def parse_batch(reply, methods):
    try: value = decode_reply(reply)
    except ValueError as error: raise EnrichmentResponseError("Copilot did not return batch JSON") from error
    names = {m.qualified_name for m in methods}
    # Accept explicit wrapper formats while matching only exact supplied identities.
    if isinstance(value, dict) and not names.intersection(value):
        for wrapper in ("results", "methods"):
            if wrapper in value and isinstance(value[wrapper], (dict, list)):
                value = value[wrapper]
                break
    if isinstance(value, list):
        mapped = {}
        duplicates = set()
        for item in value:
            if not isinstance(item, dict): continue
            name = item.get("qualified_name", item.get("method"))
            if not isinstance(name, str) or name not in names: continue
            if name in mapped: duplicates.add(name)
            mapped[name] = item.get("enrichment", item)
        value = mapped
        for name in duplicates:
            value[name] = None
    if not isinstance(value, dict) or not names.intersection(value):
        raise EnrichmentResponseError("Copilot batch contains no exact requested method identities; expected the fully qualified names as JSON keys")
    results = {}
    for name in names:
        try:
            if name not in value: raise EnrichmentResponseError("Copilot omitted this method from the batch")
            results[name] = parse_enrichment(json.dumps(value[name]))
        except EnrichmentResponseError as error:
            results[name] = error
    return results


def enter_prompt(page, box, prompt, *, attempts=3):
    """Insert text as one editor input, without Enter key submission or clipboard use."""
    def normalized(text):
        # Copilot's rich editor appends zero-width sentinel characters.
        # Ignore only trailing markers, preserving characters inside the prompt.
        return " ".join(text.rstrip(" \t\r\n\u200b\u200c\u200d\ufeff").split())
    expected = normalized(prompt)
    for attempt in range(attempts):
        try:
            # Locators resolve the current editor after a theme/session rerender.
            # Target the editable element directly. Page-keyboard input can lose
            # its target when Copilot replaces/focuses controls during a rerender.
            box.fill(prompt, timeout=30000)
            # Allow controlled-editor state and deferred theme rendering to settle.
            page.wait_for_timeout(750)
            if normalized(box.inner_text()) == expected:
                page.wait_for_timeout(750)
                if normalized(box.inner_text()) == expected:
                    return
        except Exception:
            if attempt == attempts - 1:
                raise
        if attempt < attempts - 1:
            page.wait_for_timeout(1000)
    raise EnrichmentUnavailable("Copilot editor changed repeatedly while preparing the prompt. Build paused before sending after 3 attempts.")

# TODO: fixes from AgentControlCenter (bridge/ask.py, bridge/history.py, bridge/lifecycle.py) that
# were not ported because this client always starts a fresh chat. Apply them if that ever changes:
# - Reopening a saved chat by URL: wait for its earlier answers to render before counting `before`
#   in _enrich. Right after a reload the count is 0, so an old answer is taken as the new reply.
# - Never trust a saved URL without a /chat/conversation/<id> path: it opens a new empty chat.
# - A deleted chat still opens by URL but shows a "This chat is no longer available" dialog;
#   treat that dialog as "chat removed".
# - decode_reply: Copilot sometimes leaves raw line breaks or unescaped double quotes inside JSON
#   strings. AgentControlCenter parses with json.loads(strict=False), falls back to a regex for
#   {"summary": "...", "complete": true}, and ignores prose or a "json" label around the object.
#   Only needed if the current format-retry and Try Again handling starts failing on bad JSON.
class CopilotClient:
    def __init__(self, profile: Path | None = None): self.profile = profile or DATA_DIR / "copilot-profile"; self.page = None; self._pw = None; self.conversation_url = None; self.interrupted = False
    def start(self):
        with _BROWSER_START_LOCK:
            self._start()
    def _start(self):
        try:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start(); self.profile.mkdir(parents=True, exist_ok=True)
            endpoint = "http://127.0.0.1:9334"
            def running():
                try:
                    with urllib.request.urlopen(endpoint + "/json/version", timeout=1): return True
                except OSError: return False
            if not running():
                edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
                subprocess.Popen([str(edge), "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=9334", f"--user-data-dir={self.profile.resolve()}", "--no-first-run", M365_URL])
                deadline = time.monotonic() + 20
                while not running():
                    if time.monotonic() >= deadline: raise EnrichmentUnavailable("Dedicated Edge did not start.")
                    time.sleep(0.5)
            browser = self._pw.chromium.connect_over_cdp(endpoint)
            self.page = browser.contexts[0].new_page()
            self.page.emulate_media(color_scheme="light")
            self.page.goto(M365_URL, wait_until="domcontentloaded")
        except Exception as exc: raise EnrichmentUnavailable("Could not open Microsoft 365 Copilot; sign in to the dedicated Edge window and retry.") from exc
    def enrich(self, method):
        self._stage = "opening Copilot"
        self._check_cancel()
        try:
            try:
                return self._enrich(method)
            except EnrichmentResponseError as error:
                if isinstance(error, EnrichmentTimeout): raise
                regenerated = self._regenerate_reply()
                if regenerated is not None:
                    return self._parse_reply(regenerated)
                # One bounded correction request; never retry an uncertain send.
                self._format_retry = True
                try:
                    return self._enrich(method)
                finally:
                    self._format_retry = False
        except (EnrichmentResponseError, EnrichmentUnavailable):
            raise
        except Exception as error:
            # A partially typed or ambiguously sent prompt must never be followed
            # by another method in the same editor. Retain the tab for inspection.
            self.interrupted = True
            raise EnrichmentUnavailable(f"Copilot browser step failed while {self._stage} ({type(error).__name__}). {browser_failure_reason(error)} Build paused; inspect the retained Copilot window and rebuild to retry.") from error

    def enrich_batch(self, methods):
        self._batch_methods = tuple(methods)
        try:
            return self.enrich(self._batch_methods)
        finally:
            self._batch_methods = None

    def _parse_reply(self, reply):
        methods = getattr(self, "_batch_methods", None)
        return parse_batch(reply, methods) if methods else parse_enrichment(reply)

    def _regenerate_reply(self):
        self._check_cancel()
        if self.page is None: return None
        self._stage = "finding Try Again for the latest reply"
        answers = self.page.locator('[data-testid="markdown-reply"]')
        done = self.page.locator('[data-testid="CopyButtonTestId"]')
        # Use only a unique visible regeneration control; never choose a random
        # historical reply when Copilot displays several retry buttons.
        retry = self.page.get_by_role("button", name=re.compile(r"^(Try Again|Regenerate response)$", re.I)).and_(self.page.locator("button:visible"))
        if retry.count() != 1: return None
        before, done_before = answers.count(), done.count()
        old_text = answers.last.inner_text().strip() if before else ""
        self._stage = "pressing Try Again"
        self._response_pending = True
        try:
            retry.click(timeout=10000)
            self._stage = "waiting for the regenerated reply"
            def read():
                count = answers.count()
                return count, done.count(), answers.last.inner_text().strip() if count else ""
            reply = wait_for_reply(read, before, done_before, previous_text=old_text,
                should_cancel=self._cancelled,
                is_busy=lambda: is_generating(self.page))
            self._response_pending = False
            return reply
        except Exception:
            self.interrupted = True
            raise

    def _enrich(self, method):
        self._check_cancel()
        if not self.page: self.start()
        if getattr(self, "_response_pending", False):
            raise EnrichmentUnavailable("Previous Copilot reply did not finish; rebuild to retry.")
        box = self.page.locator('[role="textbox"][aria-label="Message Copilot"]')
        self._stage = "waiting for the message box"
        box.wait_for(state="visible", timeout=30000)
        answers = self.page.locator('[data-testid="markdown-reply"]')
        done = self.page.locator('[data-testid="CopyButtonTestId"]')
        before, done_before = answers.count(), done.count()
        self._stage = "reading the previous reply"
        previous_text = answers.last.inner_text().strip() if before else ""
        self._stage = "inserting and verifying the method prompt"
        try:
            prompt = build_prompt(method)
            if getattr(self, "_format_retry", False):
                prompt = "Your previous reply could not be parsed into the requested JSON schema. Analyze this method again and return only the complete JSON object, without introductory text or commentary.\n" + prompt
            enter_prompt(self.page, box, prompt)
        except Exception:
            self.interrupted = True
            raise
        self._stage = "pressing Send"
        self._check_cancel()
        try:
            send_prompt(self.page)
        except Exception:
            self.interrupted = True
            raise
        self._response_pending = True
        def read():
            self._stage = "reading the new reply"
            self.conversation_url = self.page.url
            count = answers.count()
            return count, done.count(), answers.last.inner_text().strip() if count else ""
        try:
            reply = wait_for_reply(read, before, done_before, previous_text=previous_text,
                should_cancel=self._cancelled,
                is_busy=lambda: is_generating(self.page))
        except Exception:
            self.interrupted = True
            raise
        self._response_pending = False
        self._await_conversation_url()
        return self._parse_reply(reply)

    def _await_conversation_url(self, timeout_s=20):
        # Copilot adds the chat ID to the URL a few seconds after the reply ends. Without it the
        # chat cannot be found later, so delete_current_conversation would retain it.
        url = getattr(self.page, "url", None)
        if not isinstance(url, str) or urlparse(url).hostname != urlparse(M365_URL).hostname: return
        deadline = time.monotonic() + timeout_s
        while "/chat/conversation/" not in urlparse(self.page.url).path and time.monotonic() < deadline:
            time.sleep(0.25)
        self.conversation_url = self.page.url

    def _cancelled(self):
        event = getattr(self, "cancel_event", None)
        return event is not None and event.is_set()

    def _check_cancel(self):
        if self._cancelled():
            self.interrupted = True
            raise EnrichmentCancelled("Catalog build cancelled.")

    def delete_current_conversation(self) -> bool:
        # Deletion selectors are deliberately conservative; ambiguity means retain and report.
        if not self.page or not self.conversation_url or "/chat/conversation/" not in urlparse(self.conversation_url).path: return False
        try:
            self.page.goto(M365_URL, wait_until="domcontentloaded")
            toggle = self.page.get_by_test_id("nav-toggle-button")
            if toggle.count() == 1 and attribute_or_none(toggle, "aria-label", timeout=5000) == "Expand sidebar": toggle.click()
            links = self.page.locator(f'a[href="{urlparse(self.conversation_url).path}"]:visible')
            if links.count() != 1: return False
            row = links.first
            for _ in range(4):
                row = row.locator("xpath=..")
                menu = row.get_by_role("button", name="More options")
                if menu.count() == 1:
                    row.hover(); menu.click(); break
            else: return False
            self.page.get_by_role("menuitem", name="Delete", exact=False).click()
            dialog = self.page.get_by_role("dialog").or_(self.page.get_by_role("alertdialog")); dialog.wait_for(state="visible", timeout=5000)
            dialog.get_by_role("button", name="Delete", exact=False).click(); dialog.wait_for(state="hidden", timeout=10000)
            try: links.first.wait_for(state="hidden", timeout=10000)
            except Exception:
                # Copilot can keep a stale sidebar row after a successful delete. Reload for the real list.
                self.page.goto(M365_URL, wait_until="domcontentloaded")
                toggle = self.page.get_by_test_id("nav-toggle-button")
                if toggle.count() == 1 and attribute_or_none(toggle, "aria-label", timeout=5000) == "Expand sidebar": toggle.click()
                try: self.page.locator('a[href*="/chat/conversation/"]:visible').first.wait_for(state="visible", timeout=10000)
                except Exception: pass  # no other chats to list
                for _ in range(10):
                    if self.page.locator(f'a[href="{urlparse(self.conversation_url).path}"]:visible').count() == 0: return True
                    self.page.wait_for_timeout(1000)
                return False
            return True
        except Exception: return False
    def close(self) -> bool:
        if self.interrupted or self._cancelled():
            # Disconnect only: the independently launched Edge retains the reply for inspection.
            if self._pw: self._pw.stop()
            self.page = self._pw = None
            return False
        deleted = self.delete_current_conversation()
        try:
            if self.page: self.page.close()
            if self._pw: self._pw.stop()
        finally: self.page = self._pw = None
        return deleted
