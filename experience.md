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

---

## 8. August 25, 2026: Live-Capture and Signal-Coverage Remediation

### Production evidence that triggered the work

- The audited broadcast produced 450 capture sessions, 449 marked interrupted, and only about 55.6% transcript coverage. A process merely starting was therefore not evidence that the transport remained healthy.
- Streamlink runtime EOF/exit was not routed to the already available direct FFmpeg path.
- Subprocess stderr was discarded, so the reason for repeated capture failure was unavailable.
- A fixed five-second outer retry created a restart storm, and alert timestamps restarted with each capture session.
- The English keyword set missed actual execution wording (`enter`, `scalp`) and did not trigger on partial-profit, CTC/breakeven, stop-management, or explicit exit language.

### Reusable implementation lessons

- Treat transport startup and transport survival as separate states. If Streamlink exits, stalls, or reaches EOF after startup, perform one bounded direct-FFmpeg attempt with the freshly resolved media URL before returning to outer resolution.
- Drain every subprocess stderr concurrently. Retain both a bounded line count and bounded line size, then sanitize signed query strings and registered secrets before logging only a short diagnostic tail.
- Size a retry reset window from observed failure durations. The initial 60-second reset proposed by AGY was shorter than the 120–140-second failing sessions and would have repeatedly erased backoff; the accepted default is 300 seconds.
- Preserve a broadcast-relative timestamp across transport restarts. Reset it only on confirmed offline state or a changed broadcast ID, and release stale video capture when the resolved media URL changes.
- Build trigger coverage from the audited transcript corpus, not intuition. Keyword detection is only a low-cost wake-up gate; Gemini must still reject conditional, negated, educational, retrospective-only, viewer-request, and general commentary.
- Model lifecycle actions explicitly (`ENTRY`, `PARTIAL_EXIT`, `MOVE_STOP`, `EXIT`, `NONE`). Management signals must not fabricate an open position, and explicit exits should close state immediately.
- Apply cooldowns per action class. One global trigger cooldown can suppress a legitimate stop move or exit immediately following an entry or partial booking.
- Preserve fail-closed precedence when adding backward compatibility. An explicitly returned `NONE` must not become `ENTRY` merely because a provider also returns an inconsistent legacy boolean.
- Telegram photo captions have a 1024-character limit. If an escaped management alert exceeds it, send the full alert as a regular message rather than failing delivery.

### AGY acceptance findings

AGY's delegated changes passed its reported 323-test suite, but independent review still found and corrected:

- explicit `NONE` responses being reopened as entries;
- unknown management direction being fabricated as `BUY`;
- one global trigger cooldown suppressing different action types;
- explicit active-trade exits using a non-timestamped legacy alert;
- missing cross-field validation for restart delay bounds;
- cleanup not being guaranteed when reader-thread startup failed;
- stale broadcast clocks and video captures when a live broadcast or resolved URL changed;
- unbounded management photo captions causing Telegram delivery failure.

### Regression checklist for the next Antigravity run

- [ ] Reproduce runtime failures, not only subprocess startup failures.
- [ ] Test fallback success, fallback failure, EOF, stall, cancellation, and partial-start cleanup.
- [ ] Test diagnostic redaction, maximum retained lines, and maximum line length.
- [ ] Test backoff growth, cap, sustained-success reset, and interruptible shutdown against production-like session durations.
- [ ] Test stable clocks across transport restarts and clock reset on a changed broadcast ID.
- [ ] Use exact audited phrases for entry, combined partial/CTC management, and explicit close/cut.
- [ ] Prove explicit `NONE` remains fail-closed even with inconsistent legacy fields.
- [ ] Prove repeated synonyms are suppressed while different management actions are not.
- [ ] Test escaped Telegram content and oversized photo-caption fallback.
- [ ] Never accept an AGY-reported green suite without inspecting every changed file and adding adversarial acceptance tests.

### Final independent validation

- `pytest tests -q`: 333 passed, 3 subtests passed in 7.74 seconds.
- `ruff check src tests scripts main.py`: all checks passed.
- `uv pip check --python .\.venv\Scripts\python.exe`: 80 packages checked and compatible.
- `python -m compileall -q src scripts main.py benchmark.py`: passed.
- `git diff --check`: passed.
- No live provider calls were made during implementation or testing.

---

## 9. August 27, 2026: Streamlink-Owned YouTube Resolution and Provider-Aware Transports

### Production evidence that triggered the work

- Prior trials attempting to route YouTube live audio through pre-resolved signed Google Video media URLs (`hls://<signed-googlevideo-url>`) failed at approximately 55 seconds with four consecutive HTTP 403 segment errors and delivered only ~30.5% audio coverage.
- Signed playback URLs are bound to client/session/IP context and are not durable application interfaces. In this incident the manifest advertised roughly six hours of remaining validity, yet newly referenced segments still returned HTTP 403 after about 55 seconds; the evidence does not support a simple one-minute URL-expiry explanation.
- The isolated empirical trial (`scratch/streamlink_plugin_ownership_trial`) proved the plugin ownership model: passing the canonical public YouTube watch URL directly to Streamlink's dedicated YouTube plugin delivered exactly 180 seconds, 100% audio coverage (18 complete 10-second WAV chunks), zero HTTP 403 errors, and zero HTTP 429 rate limits.

### Generalizable architecture and engineering lessons

1. **URL Ownership Across Tool Boundaries**:
   - Decouple initial liveness probes from transport resolution. `yt-dlp`/`StreamManager` owns broadcast metadata (liveness state, video ID, broadcast start time), while the streaming engine (Streamlink's YouTube plugin) must own playback resolution, token acquisition, and playlist lifecycle management.
   - Never pass a pre-resolved CDN media URL into a specialized transport plugin. Passing the canonical public URL (`https://www.youtube.com/watch?v=...`) allows the plugin's internal session management, signature extraction, and playlist reload loop to function as engineered.

2. **The Hazard of Ephemeral Signed URLs**:
   - Cloud media CDNs (Googlevideo, Cloudflare Stream, Akamai) sign manifests and segments with ephemeral parameters (`expire`, `signature`, `sparams`, client IP bindings).
   - Treating a signed URL as a durable endpoint can lead to systematic mid-stream failures (HTTP 403 Forbidden). Downstream generic HLS tools do not own the provider-specific client identity and playback-resolution lifecycle needed when subsequent segment access changes.

3. **Failure-Boundary Live Probes as Decision Evidence**:
   - Complex transport transitions should be validated through bounded, isolated scratch runners with concrete metrics (duration, coverage percentage, chunk integrity, HTTP error distributions) before touching production services.
   - Quantitative baseline evidence (180s capture, 18 complete chunks, zero 403s vs 55s, 4 403s, 30.5% coverage) provides an indisputable empirical basis for architectural decisions and protects against speculative refactoring.

4. **Provider-Aware Fallback Hierarchies**:
   - Fallback strategies must not be applied blindly across heterogeneous providers.
   - For generic HLS streams with stable media URLs, a one-shot direct FFmpeg fallback provides valuable resiliency against local Streamlink crashes or environment glitches.
   - For YouTube, however, direct FFmpeg against an expired or signed Googlevideo URL guarantees an immediate HTTP 403 loop. The capture layer must detect YouTube inputs via robust parsed-hostname classification and fail back directly to the outer supervised monitoring loop (exponential backoff, status re-resolution, fresh stream acquisition) rather than invoking direct FFmpeg.

5. **FFmpeg Live-Input Throttling Antipattern**:
   - When decoding live streaming media delivered over standard input (`pipe:0`) from a live transport (Streamlink), never configure `-re` or `-readrate`.
   - The upstream provider and transport already throttle data arrival to real-time broadcast speed. Introducing FFmpeg input rate-limiting creates an artificial bottleneck, accumulates unconsumed data in the stdin OS pipe buffer, introduces latency drift, and induces synthetic timeout stalls in downstream chunk readers.

### Regression checklist for future transport work

- [ ] Use parsed hostnames (`urlparse(url).hostname`) and explicit domain allow-lists rather than substring checks (`"youtube.com" in url`) to classify stream providers.
- [ ] Ensure public canonical URLs are forwarded to transport plugins; do not inject `hls://` prefixes onto URLs that have native plugin handlers.
- [ ] Configure Streamlink HLS live edge to the empirically proven 3 segments (`--hls-live-edge 3`) for minimal latency with buffer stability.
- [ ] Verify that FFmpeg stdin decoding omits `-re` and `-readrate`.
- [ ] For YouTube inputs, verify negative fallback guarantees: Streamlink startup errors, runtime exits, stalls, and EOF must fail back to the supervisor without spawning direct FFmpeg.
- [ ] For non-YouTube inputs, verify that one-shot direct FFmpeg fallback remains functional at startup and runtime.
- [ ] Verify that HTTP 429 errors from stderr are classified as `PROVIDER_RATE_LIMIT` and trigger provider cooldown rather than immediate restart loops.
- [ ] Maintain deterministic subprocess termination, pipe closure, and thread joining in `stop()`.
