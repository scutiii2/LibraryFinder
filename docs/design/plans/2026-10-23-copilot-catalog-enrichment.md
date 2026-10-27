> superseded: Copilot backend removed

# Copilot Catalog Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich every new or changed AST method record with a structured Microsoft 365 Copilot explanation while retaining unchanged enrichments and deleting the dedicated Copilot chat after each catalog run.

**Architecture:** Keep AST-derived data authoritative and add optional enrichment data to each immutable method record. A focused Playwright client owns the dedicated Edge/CDP session, structured prompt, response parsing, and narrowly scoped conversation deletion. A catalog-enrichment service compares per-method fingerprints against the prior catalog, asks Copilot only when necessary, and leaves an AST-only catalog usable on any individual browser or model failure.

**Tech Stack:** Python 3.11, dataclasses, AST, JSON, Playwright synchronous API, pytest.

**Spec:** `docs/superpowers/specs/2026-10-23-copilot-catalog-enrichment-design.md`

## Global Constraints

- Never import or execute a library module; AST data remains the source of truth.
- Initial enrichment includes public and private methods; later runs submit only new, changed, or non-successfully-enriched methods.
- Use a dedicated project-local, gitignored Edge profile and localhost-only CDP; never touch the user's normal browser profile or secrets.
- Submit only bounded source snippets and AST metadata to the signed-in Microsoft 365 Copilot account, after printing a disclosure.
- Delete only the conversation created for the current catalog run, in `finally`; report an unconfirmed deletion without deleting unrelated history.
- Automated tests use fakes only and must not start Edge, authenticate, or mutate an external Copilot conversation.
- The project is not a Git repository; do not stage or commit.

## Review Focus

- An unchanged method must reuse its previous successful explanation even if unrelated methods or file hashes change; test in Task 2.
- A method whose snippet changes but whose qualified name stays the same must be re-enriched; test in Task 2.
- A failed or malformed enrichment must retry on the next run rather than becoming permanent; test in Task 2.
- Browser cleanup must not delete a chat when the client never established the current run's conversation identity; test in Task 3.
- A Copilot reply with prose around JSON or unexpected fields must not become searchable semantic content; test in Task 1.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/library_finder/models.py` | JSON-safe enrichment and fingerprint fields on `MethodRecord`; catalog schema migration. |
| `src/library_finder/scanner.py` | Capture each method's bounded source segment and deterministic AST-based fingerprint inputs. |
| `src/library_finder/copilot.py` | Dedicated Edge/CDP lifecycle, M365 prompt/response transport, JSON validation, and conservative deletion of the current run's conversation. |
| `src/library_finder/enrichment.py` | Compare prior/current methods, reuse valid enrichments, orchestrate requests, and return counts/statuses. |
| `src/library_finder/cli.py` | Parse catalog options, print disclosure/progress/summary, invoke enrichment, and guarantee cleanup. |
| `src/library_finder/search.py` | Include only successful Copilot fields in deterministic search text. |
| `src/library_finder/service.py` | Pass successful Copilot fields to Laya's bounded candidate metadata. |
| `pyproject.toml` | Add a Playwright optional dependency used by the Copilot feature. |
| `README.md` | Document login, data disclosure, incremental behavior, and deletion guarantee/limitations. |
| `tests/test_models.py`, `tests/test_scanner.py`, `tests/test_enrichment.py`, `tests/test_copilot.py`, `tests/test_cli.py`, `tests/test_search.py`, `tests/test_service.py` | Mocked coverage for all behavior above. |

### Task 1: Catalog record schema and deterministic method fingerprints

**Files:**
- Modify: `src/library_finder/models.py`
- Modify: `src/library_finder/scanner.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_scanner.py`

**Interfaces:**
- Produces: `CopilotEnrichment(summary: str, purpose: str, inputs: tuple[str, ...], outputs: tuple[str, ...], side_effects: tuple[str, ...], automation_context: str, keywords: tuple[str, ...], caveats: tuple[str, ...])`.
- Produces: `MethodRecord.source: str`, `MethodRecord.fingerprint: str`, `MethodRecord.enrichment: CopilotEnrichment | None`, and `MethodRecord.enrichment_status: str` where status is `pending`, `success`, or `error`.
- Produces: `method_fingerprint(record: MethodRecord) -> str` based only on the AST semantic inputs and source segment specified in the spec.

- [ ] **Step 1: Write failing serialization and scanner tests**

```python
def test_catalog_round_trip_preserves_successful_enrichment():
    record = replace(sample_method, enrichment=sample_enrichment, enrichment_status="success")
    assert Catalog.from_dict(Catalog(..., methods=(record,), ...).to_dict()).methods == (record,)

def test_scanner_changes_method_fingerprint_when_method_body_changes(library_root):
    first = scan_library(library_root).method("libs.drivers.sample.SampleDriver.click")
    source = library_root / "drivers" / "sample.py"
    source.write_text(source.read_text().replace("find_element", "find_elements"))
    second = scan_library(library_root).method("libs.drivers.sample.SampleDriver.click")
    assert first.fingerprint != second.fingerprint
```

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `python -m pytest tests/test_models.py tests/test_scanner.py -v`

Expected: FAIL because enrichment fields and method fingerprint/source capture do not exist.

- [ ] **Step 3: Implement schema-compatible records and AST source capture**

Add the enrichment dataclass and backward-compatible `Catalog.from_dict` handling for existing `catalog.json` files: absent enrichment fields load as `None`/`pending`. In `scanner.py`, use `ast.get_source_segment(content, child)` for the exact function source, truncate to the decided bounded maximum before storage/prompt use, and calculate SHA-256 over a canonical JSON payload of the specified semantic inputs. Increase `Catalog.CURRENT_VERSION`.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `python -m pytest tests/test_models.py tests/test_scanner.py -v`

Expected: PASS.

### Task 2: Incremental enrichment orchestration

**Files:**
- Create: `src/library_finder/enrichment.py`
- Modify: `tests/test_catalog.py`
- Create: `tests/test_enrichment.py`

**Interfaces:**
- Consumes: `MethodRecord` fields from Task 1 and a `CopilotClient` protocol exposing `enrich(record: MethodRecord) -> CopilotEnrichment`.
- Produces: `enrich_catalog(scanned: Catalog, previous: Catalog | None, client: CopilotClient) -> tuple[Catalog, EnrichmentSummary]`.
- Produces: `EnrichmentSummary(scanned: int, reused: int, enriched: int, failed: int, pending: int)`.

- [ ] **Step 1: Write failing incremental-behavior tests**

```python
def test_first_run_enriches_every_method(sample_catalog, fake_client):
    catalog, summary = enrich_catalog(sample_catalog, None, fake_client)
    assert fake_client.requested == list(sample_catalog.methods)
    assert summary.enriched == len(sample_catalog.methods)

def test_unchanged_successful_method_is_reused(sample_catalog, fake_client):
    previous = catalog_with_successful_enrichments(sample_catalog)
    catalog, summary = enrich_catalog(sample_catalog, previous, fake_client)
    assert fake_client.requested == []
    assert summary.reused == len(sample_catalog.methods)

def test_changed_and_prior_failed_methods_are_requested_again(sample_catalog, fake_client):
    previous = catalog_with_one_changed_and_one_error(sample_catalog)
    _, summary = enrich_catalog(sample_catalog, previous, fake_client)
    assert summary.enriched == 2

def test_one_client_failure_keeps_ast_record_and_continues(sample_catalog, fake_client):
    catalog, summary = enrich_catalog(sample_catalog, None, fake_client.failing_on(1))
    assert catalog.methods[1].enrichment_status == "error"
    assert summary.failed == 1
```

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `python -m pytest tests/test_enrichment.py tests/test_catalog.py -v`

Expected: FAIL because the orchestration module does not exist.

- [ ] **Step 3: Implement `enrich_catalog` and prior-record matching**

Use `(file, qualified_name)` as the stable key. Reuse only a prior `success` record with equal fingerprint; request every other record independently. Catch ordinary client exceptions per method, preserve its AST fields, set `enrichment=None`/`enrichment_status="error"`, and continue. Return a new catalog with the scanned methods only so removed methods are pruned.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `python -m pytest tests/test_enrichment.py tests/test_catalog.py -v`

Expected: PASS.

### Task 3: Playwright Copilot client and scoped conversation deletion

**Files:**
- Create: `src/library_finder/copilot.py`
- Modify: `pyproject.toml`
- Create: `tests/test_copilot.py`

**Interfaces:**
- Produces: `CopilotClient` with `start() -> None`, `enrich(record: MethodRecord) -> CopilotEnrichment`, `close() -> None`, and `delete_current_conversation() -> bool`.
- Produces: `CopilotUnavailable`, `CopilotResponseError`, and `CopilotDeletionError` for actionable caller reporting.
- Produces: `build_prompt(record: MethodRecord) -> str` and `parse_enrichment(reply: str) -> CopilotEnrichment`.

- [ ] **Step 1: Write failing prompt, response, and lifecycle tests with fake Playwright pages**

```python
def test_parse_enrichment_rejects_non_json_and_unknown_fields():
    with pytest.raises(CopilotResponseError):
        parse_enrichment("Here is the JSON: {...}")

def test_close_attempts_delete_only_for_current_run_conversation(fake_page):
    client = CopilotClient(page_factory=lambda: fake_page)
    client.start()
    client.enrich(sample_method)
    client.close()
    assert fake_page.deleted_urls == [client.conversation_url]

def test_close_does_not_delete_when_a_conversation_was_never_identified(fake_page):
    client = CopilotClient(page_factory=lambda: fake_page)
    client.start()
    client.close()
    assert fake_page.deleted_urls == []
```

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `python -m pytest tests/test_copilot.py -v`

Expected: FAIL because the Copilot client does not exist.

- [ ] **Step 3: Implement the minimal dedicated-browser client**

Add the Playwright optional dependency. Follow the established local bridge pattern: profile under `Library-Finder/.profiles/copilot`, fixed localhost CDP port configurable via constructor, Edge launch with remote debugging, attach with `connect_over_cdp`, open one M365 chat tab, use typed interaction with the established M365 selectors, and wait for the current final reply. Capture the provider conversation URL after the first request. Parse only a strict JSON object with the eight defined fields and expected value types. Implement deletion with a current-URL identity check, unique sidebar/history lookup, explicit Delete confirmation, and disappearance verification; refuse ambiguity. `close()` calls deletion in `finally`, then closes the tab and Playwright connection. Add `.profiles/` to `.gitignore` only if a project `.gitignore` exists or create a narrow one containing that directory.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `python -m pytest tests/test_copilot.py -v`

Expected: PASS without opening Edge or contacting Copilot.

### Task 4: Catalog CLI integration, disclosure, cleanup, and strict result policy

**Files:**
- Modify: `src/library_finder/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `enrich_catalog` from Task 2 and `CopilotClient` from Task 3.
- Produces: `library-finder catalog [--no-copilot] [--strict]` that writes a catalog and prints `scanned`, `reused`, `enriched`, `failed`, and `skipped` counts.

- [ ] **Step 1: Write failing CLI tests with a monkeypatched Copilot client**

```python
def test_catalog_discloses_copilot_submission_prints_counts_and_closes(capsys, ...):
    assert main(["catalog", "--source", str(library_root), "--catalog", str(destination)]) == 0
    assert "Microsoft 365 Copilot" in capsys.readouterr().out
    assert fake_client.closed is True

def test_catalog_strict_returns_nonzero_for_enrichment_failure(...):
    assert main(["catalog", "--strict", ...]) == 1

def test_catalog_no_copilot_writes_pending_ast_catalog(...):
    assert main(["catalog", "--no-copilot", ...]) == 0
```

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -v`

Expected: FAIL because catalog does not yet orchestrate Copilot enrichment or cleanup.

- [ ] **Step 3: Integrate enrichment and user-facing failure reporting**

Load the prior catalog only when its source root matches the requested source. Print the source-submission disclosure before `client.start()`. Run scanning and enrichment, save the resulting catalog even if individual enrichments fail, and call `client.close()` from `finally`. In normal mode return zero for individual enrichment failures and surface a retry instruction plus deletion result; in strict mode return one for source scan errors, enrichment failures, or unconfirmed conversation deletion. Document the login requirement, data disclosure, one-time/all-method first run, incremental behavior, and deletion limitation.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`

Expected: PASS.

### Task 5: Make semantic enrichment available to retrieval and Laya

**Files:**
- Modify: `src/library_finder/search.py`
- Modify: `src/library_finder/service.py`
- Modify: `tests/test_search.py`
- Modify: `tests/test_service.py`

**Interfaces:**
- Consumes: successful `MethodRecord.enrichment` values from Task 1.
- Produces: deterministic retrieval that scores summary, purpose, context, keywords, and caveats only when `enrichment_status == "success"`.
- Produces: Laya candidate payloads containing successful Copilot enrichment clearly labeled as generated analysis rather than source fact.

- [ ] **Step 1: Write failing retrieval and ranker-input tests**

```python
def test_retrieve_matches_successful_copilot_keywords(sample_catalog):
    assert retrieve("find Jaggaer ticket", catalog_with_ticket_enrichment)[0].qualified_name == target_name

def test_failed_enrichment_is_not_included_in_laya_candidate_text(sample_catalog):
    ranker = CapturingRanker()
    find_methods("ticket", catalog_with_failed_enrichment, ranker)
    assert "failed summary" not in ranker.candidates_text
```

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `python -m pytest tests/test_search.py tests/test_service.py -v`

Expected: FAIL because enrichment is not part of retrieval or ranking metadata.

- [ ] **Step 3: Add successful enrichment fields to bounded candidate metadata**

Create one helper that converts a successful enrichment into a bounded text representation. Reuse it in retrieval and the Laya input path. Preserve the present fallback behavior when Laya cannot load, returns invalid output, or no enrichment exists.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `python -m pytest tests/test_search.py tests/test_service.py -v`

Expected: PASS.

### Task 6: Full regression verification and manual opt-in smoke procedure

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: completed catalog command from Task 4.
- Produces: documented manual commands for the user to sign in, run a small controlled catalog, confirm the summary, and confirm the dedicated conversation disappears.

- [ ] **Step 1: Add an explicit manual smoke-test checklist**

Document how to start/sign in to the dedicated Edge profile, run a catalog against a disposable fixture or user-approved source, verify summary counts, and verify the catalog conversation has disappeared from Copilot history. Include the instruction to stop and report rather than manually deleting uncertain history if the CLI says deletion was unconfirmed.

- [ ] **Step 2: Run the full automated test suite**

Run: `python -m pytest -v`

Expected: PASS; all tests are mocked and no browser or Copilot account interaction occurs.

- [ ] **Step 3: Review the change set against the spec**

Confirm AST-only scanning, all-method initial submission, incremental reuse/retry, error persistence, structured semantic data, local-profile boundary, disclosure, and scoped conversation deletion are covered. Do not perform the manual authenticated smoke test without the user's explicit authorization.
