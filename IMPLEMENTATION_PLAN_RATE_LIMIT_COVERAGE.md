# YouTube Rate-Limit and Coverage Implementation Plan

## Objective

Improve recoverability when YouTube returns HTTP 429 without increasing request
pressure, and make the reported capture coverage represent decoded audio actually
delivered to the STT pipeline.

Baseline: local, `origin/main`, and the production server are all at
`ad36ae57194e0ea251538aa745293ca4820d7917`. `python -m pytest -q` completed with
387 tests and 3 subtests passing in 13.88 seconds.

## Scope guardrails

- Keep one Streamlink/FFmpeg capture pipeline per channel; never overlap capture
  processes for the same channel.
- Do not call `yt-dlp` or another live-status resolver while that pipeline is active.
- Preserve Streamlink as the primary transport and cached direct FFmpeg as the
  single in-session fallback.
- Do not add cookies, proxies, PO-token bypasses, account automation, or live
  provider calls to tests.
- Preserve trading, transcription, Gemini, Telegram, and screenshot-disable
  semantics.
- Do not stage, commit, push, deploy, install packages, or edit pre-existing
  untracked files outside this plan.
- Keep provider URLs, query strings, and secrets out of diagnostics and persisted
  session errors.

## 1. Implement the bounded capture-resilience slice

### 1.1 Eliminate resolver amplification during active capture

- Remove the in-session `yt-dlp` refresh callback from the audio pipeline.
- Use the direct media URL resolved before capture as the sole direct-FFmpeg
  fallback URL for that session.
- Resolve live status at most once at a session boundary. Never run resolver work
  concurrently with Streamlink/FFmpeg capture.
- Add tests proving the resolver is not called between capture start and pipeline
  shutdown, including Streamlink EOF/fallback.

### 1.2 Detect provider rate limiting and apply a circuit-breaker cooldown

- Classify sanitized Streamlink/FFmpeg diagnostics containing an HTTP 429 / Too
  Many Requests response as a provider-rate-limit failure.
- Expose a typed/structured failure reason from `AudioStreamManager`; orchestration
  must not parse log strings.
- Add a validated `capture.provider_rate_limit_cooldown_seconds` setting. Use a
  conservative production default of 120 seconds.
- On a classified 429, skip immediate `yt-dlp` re-resolution, wait for the larger
  of normal restart backoff and the provider cooldown, and remain promptly
  interruptible by service shutdown.
- After that cooldown, allow exactly one session-boundary status resolution before
  retrying capture. Non-429 failures retain the existing bounded restart behavior.
- Tests must inject stderr diagnostics, assert exact resolver call counts, assert
  the cooldown path, and prove shutdown cancels the wait.

### 1.3 Replace nominal chunk coverage with exact decoded-audio coverage

- Record the actual PCM byte length delivered by every yielded chunk using the
  manager's sample rate, channel count, and 16-bit sample width.
- Persist `audio_seconds_captured` and `coverage_percent` on each session. Compute
  coverage against session wall-clock duration, clamp it to 0–100%, and retain
  backward-compatible loading of older JSON records.
- Add aggregate captured-audio seconds and duration-weighted coverage to the
  summary/report.
- Do not infer coverage from nominal chunk duration or chunk count. Include final
  partial chunks.
- Tests must cover partial chunks, interrupted sessions, zero-duration sessions,
  old records, and aggregate coverage.

## 2. Independent validation and operational follow-up

- Run focused tests for audio capture, orchestration, configuration, and session
  telemetry with warnings treated as errors.
- Run the complete test suite, Ruff, and formatting checks available in the local
  environment.
- Inspect every changed file for scope, concurrency cleanup, URL/secret leakage,
  placeholders, and inaccurate coverage claims.
- Do not deploy until the independent audit passes.
- Upgrade production FFmpeg 4.4.2 to a reviewed 7.x package/binary as a separate
  rollback-capable server operation; verify provenance and checksum before
  installation, then perform an isolated coverage probe before production rollout.

## Acceptance criteria

- Only one capture pipeline is active per channel and no resolver executes while
  it is active.
- HTTP 429 is represented by structured state, causes no immediate resolver call,
  and triggers a shutdown-aware minimum 120-second production cooldown.
- Normal EOF/offline detection and non-429 fallback remain functional.
- Session and aggregate coverage are derived from exact delivered PCM bytes and
  never exceed 100%.
- All focused and full offline checks pass with no new warnings.

## Implementation and audit status

Status: implemented locally and independently audited on 2026-08-26. Not deployed.

- Antigravity implemented the bounded repository slice. Its first run timed out
  after partial edits; one targeted continuation completed the integration and
  tests.
- Independent review corrected capture/resolver ordering, excluded post-capture
  resolution latency from the coverage denominator, tightened HTTP 429 matching,
  prevented an immediate duplicate offline resolution, hardened legacy metric
  parsing, and added negative regression tests for those guarantees.
- Focused validation: 114 tests passed with warnings treated as errors.
- Full validation: 400 tests and 3 subtests passed in 10.92 seconds.
- Ruff lint passed; Ruff formatting reported all 8 modified Python files formatted;
  `git diff --check` passed.
- Production FFmpeg upgrade, live coverage probe, deployment, and screenshot
  re-enablement remain separate operational steps.
