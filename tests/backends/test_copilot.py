import pytest
from backends.errors import EnrichmentResponseError
from enrichment.parsing import parse_enrichment
from backends.copilot import wait_for_reply


def test_wait_ignores_previous_reply_and_waits_for_new_completion():
    states = iter([(1, 1, "old"), (2, 1, "partial"), (2, 1, "partial"), (2, 2, "complete"), (2, 2, "complete"), (2, 2, "complete")])
    clock = [0.0]
    def sleep(seconds): clock[0] += seconds
    result = wait_for_reply(lambda: next(states), 1, 1, timeout_s=10,
                            quiet_s=1, now=lambda: clock[0], sleep=sleep)
    assert result == "complete"


def test_wait_times_out_instead_of_returning_old_reply():
    clock = [0.0]
    def sleep(seconds): clock[0] += seconds
    with pytest.raises(EnrichmentResponseError, match="timed out"):
        wait_for_reply(lambda: (1, 1, "old"), 1, 1, timeout_s=2,
                       now=lambda: clock[0], sleep=sleep)


def test_complete_json_reply_finishes_when_copilot_reuses_dom_controls():
    clock = [0.0]
    def sleep(seconds): clock[0] += seconds
    def read():
        return (1, 1, '{"summary":"old"}' if clock[0] < 1 else '{"summary":"new"}')
    reply = wait_for_reply(read, 1, 1, previous_text='{"summary":"old"}',
        now=lambda: clock[0], sleep=sleep, is_busy=lambda: clock[0] < 5)
    assert reply == '{"summary":"new"}'
    assert clock[0] >= 5


def test_stuck_stop_button_does_not_block_stable_complete_json():
    clock = [0.0]
    def sleep(seconds): clock[0] += seconds
    reply = wait_for_reply(lambda: (1, 1, '{"summary":"new"}'), 0, 0,
        now=lambda: clock[0], sleep=sleep, is_busy=lambda: True)
    assert reply == '{"summary":"new"}'
    assert clock[0] >= 10










def test_interrupted_close_does_not_delete_or_close_copilot_tab():
    from backends.copilot import CopilotClient
    class Page:
        def close(self): raise AssertionError("Must retain interrupted tab")
    class Connection:
        stopped = False
        def stop(self): self.stopped = True
    client = CopilotClient()
    client.interrupted = True
    client.page = Page()
    connection = Connection()
    client._pw = connection
    client.delete_current_conversation = lambda: pytest.fail("Must preserve conversation")
    assert client.close() is False
    assert connection.stopped


def test_browser_step_failure_pauses_build_and_reports_stage_without_prompt(monkeypatch):
    from backends.errors import EnrichmentUnavailable
    from backends.copilot import CopilotClient
    client = CopilotClient()
    def failing(method):
        client._stage = "pressing Send"
        raise TimeoutError("private prompt content")
    monkeypatch.setattr(client, "_enrich", failing)
    with pytest.raises(EnrichmentUnavailable, match="pressing Send") as error:
        client.enrich(None)
    assert client.interrupted is True
    assert "private prompt content" not in str(error.value)


def test_prompt_insertion_preserves_long_multiline_text_and_checks_editor():
    from backends.errors import EnrichmentUnavailable
    from backends.copilot import enter_prompt
    from types import SimpleNamespace
    class Editor:
        text = "old draft"
        def fill(self, text, **kwargs):
            self.text = ""
            page.keyboard.insert_text(text)
        def click(self): pass
        def press(self, key):
            if key == "Backspace": self.text = ""
        def inner_text(self): return self.text
    box = Editor()
    prompt = 'Analyze this method\n' + 'source text ' * 1200
    def insert(text): box.text += text
    page = SimpleNamespace(keyboard=SimpleNamespace(insert_text=insert), wait_for_timeout=lambda milliseconds: None)
    enter_prompt(page, box, prompt)
    assert box.text == prompt
    page.keyboard.insert_text = lambda text: None
    with pytest.raises(EnrichmentUnavailable, match="3 attempts"):
        enter_prompt(page, box, prompt)


def test_prompt_insertion_recovers_when_theme_rerender_clears_editor():
    from backends.copilot import enter_prompt
    from types import SimpleNamespace
    class Editor:
        text = ""
        def fill(self, text, **kwargs): insert(text)
        def click(self): pass
        def press(self, key):
            if key == "Backspace": self.text = ""
        def inner_text(self): return self.text
    box = Editor()
    insertions = []
    def insert(text):
        insertions.append(text)
        box.text = text
    rerendered = [False]
    def wait(milliseconds):
        if not rerendered[0]:
            box.text = ""
            rerendered[0] = True
    page = SimpleNamespace(keyboard=SimpleNamespace(insert_text=insert), wait_for_timeout=wait)
    enter_prompt(page, box, "Analyze this method")
    assert box.text == "Analyze this method"
    assert len(insertions) == 2


def test_prompt_verification_ignores_observed_trailing_copilot_sentinels():
    from backends.copilot import enter_prompt
    from types import SimpleNamespace
    class Editor:
        text = ""
        def fill(self, text, **kwargs): insert(text)
        def click(self): pass
        def press(self, key):
            if key == "Backspace": self.text = ""
        def inner_text(self): return self.text + "\u200b\u200c"
    box = Editor()
    def insert(text): box.text = text
    page = SimpleNamespace(keyboard=SimpleNamespace(insert_text=insert), wait_for_timeout=lambda milliseconds: None)
    enter_prompt(page, box, "Complete method prompt")
    assert box.text == "Complete method prompt"


def test_send_refuses_ambiguous_visible_controls():
    from backends.errors import EnrichmentUnavailable
    from backends.copilot import send_prompt
    from types import SimpleNamespace
    class Controls:
        @property
        def first(self): return self
        def wait_for(self, **kwargs): pass
        def count(self): return 2
        def click(self, **kwargs): pytest.fail("Must not send an ambiguous prompt")
    def locator(selector):
        assert selector == 'button[aria-label="Send"]:visible'
        return Controls()
    with pytest.raises(EnrichmentUnavailable, match="Multiple visible"):
        send_prompt(SimpleNamespace(locator=locator))


def test_browser_failure_classification_omits_private_error_content():
    from backends.copilot import browser_failure_reason
    assert browser_failure_reason(RuntimeError("strict mode violation private prompt")) == "The page matched multiple controls instead of one."
    assert browser_failure_reason(RuntimeError("Target page, context or browser has been closed")) == "The Copilot tab or browser connection was closed."






def test_invalid_format_gets_only_one_correction_request(monkeypatch):
    from backends.errors import EnrichmentResponseError
    from backends.copilot import CopilotClient
    client = CopilotClient()
    calls = []
    def invalid(method):
        calls.append(getattr(client, "_format_retry", False))
        raise EnrichmentResponseError("Copilot did not return JSON")
    monkeypatch.setattr(client, "_enrich", invalid)
    with pytest.raises(EnrichmentResponseError): client.enrich(None)
    assert calls == [False, True]


def test_timeout_does_not_trigger_format_retry(monkeypatch):
    from backends.errors import EnrichmentTimeout
    from backends.copilot import CopilotClient
    client = CopilotClient()
    calls = []
    def timeout(method):
        calls.append(method)
        raise EnrichmentTimeout("timed out")
    monkeypatch.setattr(client, "_enrich", timeout)
    with pytest.raises(EnrichmentTimeout): client.enrich(None)
    assert len(calls) == 1


def test_invalid_reply_uses_regeneration_before_sending_correction(monkeypatch):
    from backends.errors import EnrichmentResponseError
    from backends.copilot import CopilotClient
    client = CopilotClient()
    calls = []
    def invalid(method):
        calls.append(method)
        raise EnrichmentResponseError("Copilot did not return JSON")
    monkeypatch.setattr(client, "_enrich", invalid)
    monkeypatch.setattr(client, "_regenerate_reply", lambda: '{"summary":"s","purpose":"p","inputs":[],"outputs":[],"side_effects":[],"automation_context":"c","keywords":[],"caveats":[]}')
    assert client.enrich(None).summary == "s"
    assert len(calls) == 1


def test_failed_regeneration_is_not_repeated(monkeypatch):
    from backends.errors import EnrichmentResponseError
    from backends.copilot import CopilotClient
    client = CopilotClient()
    calls = []
    def invalid(method): raise EnrichmentResponseError("invalid JSON")
    def regenerate():
        calls.append(1)
        return "still not JSON"
    monkeypatch.setattr(client, "_enrich", invalid)
    monkeypatch.setattr(client, "_regenerate_reply", regenerate)
    with pytest.raises(EnrichmentResponseError): client.enrich(None)
    assert len(calls) == 1


def test_hidden_stop_controls_do_not_block_completed_reply():
    from backends.copilot import is_generating
    from types import SimpleNamespace
    hidden = SimpleNamespace(is_visible=lambda: False)
    visible = SimpleNamespace(is_visible=lambda: True)
    controls = [hidden]
    page = SimpleNamespace(locator=lambda selector: SimpleNamespace(all=lambda: controls))
    assert is_generating(page) is False
    controls.append(visible)
    assert is_generating(page) is True


def test_cancel_exits_reply_wait_before_reading_or_sleeping():
    from backends.errors import EnrichmentCancelled
    with pytest.raises(EnrichmentCancelled):
        wait_for_reply(lambda: pytest.fail("Must stop reading"), 0, 0,
                       should_cancel=lambda: True,
                       sleep=lambda seconds: pytest.fail("Must not wait"))


def test_attribute_or_none_returns_none_when_the_element_is_gone():
    from backends.copilot import attribute_or_none
    class Gone:
        def get_attribute(self, name, timeout=None): raise TimeoutError("detached")
    assert attribute_or_none(Gone(), "aria-label") is None


def test_conversation_url_waits_for_the_chat_id_after_the_reply():
    from backends.copilot import CopilotClient
    client = CopilotClient()
    urls = iter(["https://m365.cloud.microsoft/chat"] * 2 + ["https://m365.cloud.microsoft/chat/conversation/abc"] * 50)
    class Page:
        @property
        def url(self): return next(urls)
    client.page = Page()
    client._await_conversation_url(timeout_s=5)
    assert client.conversation_url.endswith("/chat/conversation/abc")
