# Experience Log: English-Translation Migration Remediation

## Metadata
- **Date**: 2026-08-24
- **Original Task**: English-Translation Migration — Switch live stream audio processing from Hindi transcription (`/audio/transcriptions` with `language: hi` and 133+ bilingual regex patterns) to Whisper English translation (`/audio/translations` with ~10 simple English trigger words).
- **Scope**: Remediation of 4 independent architecture and security review audit findings across preflight probes, profiling scripts, configuration defaults, documentation, evaluator corpora, and deterministic test coverage.

---

## 1. What Antigravity Missed (The 4 Audit Findings)

1. **Preflight Translation Readiness False Positives & Fragile Default Model**:
   - `AudioTranscriber.check_connection` only called `GET https://api.groq.com/openai/v1/models`, checking HTTP status 200 without verifying if the configured model is present in the returned model list and without verifying that the `/audio/translations` endpoint path/contract actually functions.
   - The default model was set to `whisper-large-v3-turbo` despite conflicting provider documentation regarding whether the turbo variant supports the `/audio/translations` endpoint.
2. **Broken Legacy `scripts/profile_trader.py`**:
   - Script passed obsolete and invalid `AudioSTTConfig` fields (`model_size`, `device`, `download_root`) from legacy Faster-Whisper.
   - Did not check or safely pass Groq API credentials.
   - Called `transcribe_file` synchronously without `await`.
   - Never closed the transcriber HTTP session.
   - Prompt and schema extracted Hindi/Hinglish keywords (e.g., *'enter kiya'*, *'le liya'*), which could overwrite `config.yaml` with non-English keywords and break trigger matching.
3. **`AudioSTTConfig` Default Drift & Inaccurate Description**:
   - Default `initial_prompt` in `src/config.py` remained the old Indian index prompt (`"Nifty, BankNifty, Sensex, Call, Put..."`) rather than matching `config.yaml` (`"Trade active. Entry taken. Buy executed..."`).
   - Field descriptions described transcription rather than English translation.
4. **Stale Documentation & Evaluator Corpus**:
   - `README.md` retained `language: "hi"`, old bilingual trigger keywords, and references to `whisper-large-v3-turbo`.
   - `src/offline_evaluator.py` evaluated audio trigger detection against a Hindi/Hinglish corpus rather than the English-translated stream output.

---

## 2. Root Cause Analysis

- **Incomplete Migration Scope**: The initial migration focused on core runtime modules (`src/audio_transcriber.py`, `src/audio_trigger.py`, `config.yaml`) but overlooked auxiliary calibration tools (`scripts/profile_trader.py`), offline evaluator corpora, and documentation samples.
- **Superficial Preflight Verification**: Probing only `GET /models` provided authentication validation but zero assurance of endpoint availability or model compatibility for translation.
- **Config & Default Divergence**: Modifying `config.yaml` without immediately synchronizing dataclass defaults in `src/config.py` caused default configuration drift.
- **Provider Documentation Ambiguity**: Groq documentation presents contradictory tables regarding `whisper-large-v3-turbo` translation support. Relying on turbo without fail-closed model selection introduced production risk.

---

## 3. Provider-Documentation Conflict Analysis

- **Conflict**: Groq's API documentation lists `whisper-large-v3` as universally supported for both Speech-to-Text Transcription (`/audio/transcriptions`) and Speech-to-Text Translation (`/audio/translations`). However, `whisper-large-v3-turbo` documentation contains conflicting references: some Groq endpoint matrices list turbo under transcription only, while others leave its translation capability unspecified or unverified.
- **Fail-Closed Decision**: In the absence of guaranteed translation support for `whisper-large-v3-turbo`, `whisper-large-v3` was selected as the production-safe, fail-closed default across `src/config.py`, `config.yaml`, `scripts/profile_trader.py`, tests, and documentation.

---

## 4. Corrections Implemented

### (1) `AudioTranscriber.check_connection` Validation & Fail-Closed Model
- Refactored `check_connection` to perform a 2-step verification:
  1. `GET /models` — Validates credentials and verifies that `self.config.model` exists in the returned model catalog.
  2. `POST /audio/translations` — Probes the translation endpoint contract with a minimal in-memory WAV payload (0.1s PCM) verifying status 200 and schema validity (`"text"` field present).
- Added deterministic offline tests for success, missing configured model, models endpoint failure/invalid JSON, translation probe HTTP failure, translation probe malformed response/invalid JSON, network exceptions, and multi-key fallback.
- Updated default model from `whisper-large-v3-turbo` to `whisper-large-v3`.

### (2) End-to-End Repair of `scripts/profile_trader.py`
- Migrated script to fully async execution (`async_main`).
- Validated `GROQ_API_KEY`/`GROQ_API_KEYS` and `GEMINI_API_KEY` at startup without leaking secrets.
- Used valid `AudioSTTConfig(model=args.model)`.
- Awaited `transcriber.transcribe_file()` and guaranteed session cleanup via `async with AudioTranscriber(...) as transcriber:`.
- Updated prompt and `ExtractedTraderProfile` schema to extract exclusively English execution trigger keywords and explicitly forbid Hindi/Hinglish transliterations.
- Added comprehensive offline test suite in `tests/test_profile_trader.py`.

### (3) Synchronized `AudioSTTConfig` Defaults
- Updated `AudioSTTConfig.initial_prompt` default to match `config.yaml`:
  `"Trade active. Entry taken. Buy executed. Sell executed. Short position. Long position. Stop loss. Target hit. Order placed. Gold, XAUUSD, BTC, Bitcoin, Forex."`
- Updated docstrings and field descriptions to describe English translation.

### (4) Synchronized Documentation & Evaluator Corpus
- Updated `README.md` architecture overview, channel modes, sample config, and hot-path descriptions to reflect English translation and `whisper-large-v3`.
- Updated `src/offline_evaluator.py` to use an English-translated 10-utterance test corpus.
- Normalized YAML formatting and ensured LF line endings.

---

## 5. Exact Validation Evidence

1. **Focused Test Suite Execution**:
   - Command: `.\.venv\Scripts\python.exe -m pytest tests/test_audio_transcriber.py tests/test_config.py tests/test_profile_trader.py tests/test_offline_evaluator.py -v`
   - Result: `85 passed in 1.84s`
2. **Full Repository Test Suite Execution**:
   - Command: `.\.venv\Scripts\python.exe -m pytest tests -q`
   - Result: `280 passed, 3 subtests passed in 6.28s`
3. **Static Analysis & Linting**:
   - Command: `.\.venv\Scripts\ruff.exe check src tests scripts`
   - Result: `All checks passed!`
4. **Dependency Compatibility**:
   - Command: `uv pip check --python .\.venv\Scripts\python.exe`
   - Result: `80 packages checked, all compatible.`

---

## 6. Reusable Checklist for Future AGY Work on this Repository

- [ ] **Contract Verification Over Surface Checks**: When testing provider readiness (e.g. `check_connection`), validate both credentials and the exact endpoint/model capability with a minimal synthetic probe.
- [ ] **Fail-Closed Provider Defaults**: If provider documentation is conflicting or volatile regarding a model variant or feature, select the most proven, conservative, and verified model.
- [ ] **Full Repository Scope Review**: When modifying a data contract (e.g., Hindi -> English), search across `src/`, `scripts/`, `tests/`, and `README.md` to prevent stale utilities from surviving.
- [ ] **Async & Resource Safety**: Always manage HTTP sessions and network resources with async context managers (`async with`) or explicit `try...finally` blocks with `await client.close()`.
- [ ] **Config & Model Schema Parity**: Keep default values and field descriptions in `src/config.py` dataclasses in exact parity with `config.yaml`.
- [ ] **Strict Secret Isolation**: Never log, print, commit, or pass secrets in unredacted structures; use `SecretStr` and test with mock keys (`gsk_...`).
- [ ] **Deterministic Offline Testing**: Ensure all unit and integration tests use injected mocks, synthetic in-memory buffers, and never make live external API calls.

---

## 7. Post-Delegation Independent Acceptance Corrections

The second Antigravity remediation passed its own suite but still required an independent acceptance pass. The following issues were found and corrected before handoff:

- `GROQ_API_KEYS` was passed to `AudioTranscriber` as one comma-containing credential. The profiler now splits, trims, and deduplicates the pool consistently with the main configuration loader.
- Prompt instructions alone did not enforce English-only LLM output. Trigger keywords are now normalized, restricted to concise ASCII words/phrases, and retained only when they are grounded in the English-translated source transcript.
- The Gemini profiler client was not closed. It is now closed in a `finally` block on both success and failure.
- A new channel could silently inherit a VOD or fake placeholder URL. New channels now require an explicit `--live-url`, URLs are validated through `ChannelConfig`, and missing/invalid config updates return failure.
- Config persistence used a direct overwrite. It now writes a same-directory temporary file and atomically replaces the destination; a fault-injection test proves replace failure preserves the original and cleans the temporary file.
- Pydantic's `ChannelMode` enum initially serialized to a Python-specific YAML tag. Using `model_dump(mode="json")` produces portable safe YAML; the focused test caught this before final acceptance.

### Final independent validation

- `.\.venv\Scripts\python.exe -m pytest tests -q`: `286 passed, 3 subtests passed in 6.51s`.
- `.\.venv\Scripts\ruff.exe check src tests scripts`: `All checks passed!`.
- `uv pip check --python .\.venv\Scripts\python.exe`: `80 packages checked; all compatible`.
- `python -m compileall -q src scripts main.py benchmark.py`: passed.
- No live Groq, Gemini, YouTube, or Telegram calls were made during implementation or testing.

### Additional checklist items

- [ ] Parse pooled credentials through the same normalization rules used by the production config loader; test comma separation and deduplication without printing values.
- [ ] Validate and ground structured LLM output in source data before persisting it; schema validation and prompt instructions are not sufficient by themselves.
- [ ] For config/file mutations, use atomic replacement and inject replacement failure to prove the original remains recoverable.
- [ ] Serialize Pydantic models in JSON mode before YAML output so enums and other rich types remain safe-loadable.
- [ ] Document that exact provider endpoint probes create real billable requests at runtime; keep automated tests mocked and invoke live preflight intentionally.
