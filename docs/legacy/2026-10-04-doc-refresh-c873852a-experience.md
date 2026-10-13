# Repository Engineering Experience

This file contains durable rules for future Codex/AGY work. Keep it generalized: incident timelines,
dated test totals, and one-off implementation narratives belong in Git history, plans, or audit
artifacts. Add a lesson only when it changes routing, contract design, implementation, or acceptance.

## 1. Decide Whether to Delegate

- Account for the fixed cost of an AGY run: preflight, contract construction, snapshot, worker startup,
  verification, semantic review, and full-suite validation.
- For a tiny single-function edit, direct Codex implementation is usually cheaper in latency and total
  model work. Delegate when isolation, a meaningful implementation body, parallel capacity, or an
  auditable write boundary justifies that fixed cost.
- Skip a separate planning call for local mechanical work. Use planning only for cross-file changes
  with unresolved architecture or runtime assumptions.
- Use the smallest capable model and one semantic attempt by default. Spend a repair attempt only when
  the verifier shows useful progress and the remaining work still exceeds controller-edit overhead.
- Do not assume a lower reasoning tier is cheaper or faster for tool-using edits. Benchmark the full
  worker loop; a low-effort model that fails to edit is strictly worse than a reliable medium run.

## 2. Build Small, Complete Contracts

- Allow only files the worker genuinely needs. Do not expose notifier, test, or configuration files
  when verified facts and explicit acceptance behavior are sufficient.
- State observable output exactly for format-sensitive work. Include empty, partial-data, and repeated
  item cases rather than relying on phrases such as "cleaner" or "minimal."
- Preserve hidden invariants explicitly: HTML escaping, parse mode, payload or caption limits,
  timezone behavior, secret redaction, resource cleanup, and fail-closed defaults.
- When replacing an existing formatter or transport, identify behavior that must disappear and behavior
  that must survive. A positive happy-path example alone is not a complete contract.
- Author adversarial acceptance tests before the worker snapshot. For Telegram HTML, include `<`, `>`,
  `&`, long escaped text, missing forecast/previous values, and the no-event state.
- Protect tests, `.git`, CI, dependency manifests, configuration, and documentation unless the user
  explicitly places one of them in scope.
- `build_prompt.py --validation-json` expects a JSON file path. Prepare that file instead of passing an
  inline JSON array.

## 3. Treat Worker Status and Repository State Separately

- AGY may complete an edit yet return fenced or otherwise non-strict JSON even when a strict schema was
  requested. Treat this as an output-envelope/infrastructure failure, not automatically as a semantic
  implementation failure.
- If AGY exits successfully but result parsing fails, inspect the changed paths and raw transcript, then
  run the deterministic verifier. Do not blindly rerun the worker and risk a duplicate patch.
- The verifier, not AGY prose, owns PASS. It must confirm allowed paths, protected digests, preservation
  of pre-existing changes, focused tests, and normalized diff identity.
- Codex must still review semantics after PASS. If a small omitted invariant remains, classify the
  escalation explicitly and repair it directly when another worker call costs more than the fix.
- Capture AGY duration, model, conversation ID, token/cache usage, and parser status independently of
  the worker's semantic result. If the provider does not expose usage, report it as unavailable; never
  claim token savings without a comparable direct-run baseline.
- Keep the immutable worker record and verifier result at separate paths. The verifier may summarize
  worker telemetry, but must never overwrite the source usage record.

## 4. Acceptance Must Be Adversarial

- A green worker-reported suite is evidence, not acceptance. Inspect every changed file and run focused
  tests through the harness, then the full relevant suite at the same revision.
- Test failure boundaries, not only startup and happy paths: EOF, stall, cancellation, partial startup,
  fallback success/failure, malformed provider output, cleanup failure, and repeated retries.
- Fail closed when legacy and new fields disagree. Explicit `NONE` must remain no-action; unknown trade
  direction must not become `BUY`; management events must not fabricate an open position.
- Model lifecycle actions explicitly (`ENTRY`, `PARTIAL_EXIT`, `MOVE_STOP`, `EXIT`, `NONE`) and apply
  cooldowns per action class so one action does not suppress a different urgent action.
- Validate structured LLM output against source evidence before persistence. Prompt instructions and
  schema validation do not prove that keywords or trading claims are grounded.
- After validation, confirm no files changed during tests and preserve all unrelated user files.

## 5. Provider and Configuration Changes

- Search the full contract surface when changing a provider, language, model, or schema: runtime code,
  scripts, tests, evaluators, configuration defaults, and documentation.
- Keep model defaults and field descriptions in code synchronized with `config.yaml`.
- Provider readiness checks should verify credentials, configured-model availability, and the exact
  endpoint contract with a minimal probe. A successful model-list request is insufficient.
- When provider documentation conflicts, choose the conservative verified capability and fail closed.
- Keep automated tests offline with mocks and synthetic buffers. Document that intentional live probes
  may create billable requests.
- Normalize pooled credentials exactly as production does: split, trim, deduplicate, and never print
  raw values.
- Close HTTP and model clients on success and failure. Use async context managers or `try/finally`.
- Persist configuration through same-directory temporary files and atomic replacement. Serialize
  Pydantic models in JSON mode before safe YAML output.

## 6. Live-Stream Transport Rules

- Treat transport startup and sustained survival as different states. Measure delivered coverage,
  complete chunks, stalls, EOF, and provider error classes.
- Use bounded isolated trials before production transport changes. Compare concrete duration, coverage,
  chunk integrity, and HTTP error distributions rather than relying on architectural intuition.
- Drain subprocess stderr concurrently. Bound retained line count and line size, sanitize signed query
  strings and registered secrets, and log only a short diagnostic tail.
- Size retry-reset windows from observed failure duration; a reset shorter than typical failure time
  erases backoff and creates restart storms.
- Preserve broadcast-relative clocks across transport restarts. Reset only on confirmed offline state
  or changed broadcast identity, and release stale captures when the media source changes.
- Classify providers using parsed hostnames and explicit allow-lists, never substring matching.

### YouTube-specific ownership

- Pass the canonical public YouTube watch URL directly to Streamlink's YouTube plugin. The plugin must
  own playback resolution, signatures, session identity, and playlist reloads.
- Never pass a pre-resolved signed Googlevideo URL into the specialized plugin or treat it as durable.
- For YouTube startup errors, runtime exits, stalls, or EOF, return to the supervised outer resolver;
  do not launch direct FFmpeg against a stale signed media URL.
- Keep one-shot direct FFmpeg fallback only for non-YouTube providers with stable media URLs.
- When FFmpeg decodes live input from Streamlink over stdin, omit `-re` and `-readrate`; the upstream
  live transport already controls delivery rate.
- Classify provider rate limits separately and apply provider cooldown instead of immediate restart.
- Guarantee subprocess termination, pipe closure, and thread joining during every stop or partial-start
  path.

## 7. Notification Constraints

- Telegram photo captions are limited to 1024 characters. If escaped content exceeds the limit, send a
  regular message rather than failing the alert.
- For HTML messages, escape every dynamic field, including headings, labels, titles, currency/region,
  timezone text, and forecast/previous values.
- Prefer semantic whitespace over decorative divider characters. Keep repeated icons only when they
  encode information, such as currency flags.

## 8. Audit Evidence and Infrastructure Diagnostics

- Define "broadcasted," "recorded," and "captured" before checking local, deployment, and provider evidence.
- Discover running resources from live compute instances (`trade-bot-vm` in `us-central1-a` running
  `trade-bot.service`); never conflate colloquial "cloud run" phrasing with Google Cloud Run (`run.googleapis.com`).
- Always pass non-interactive batch flags (`gcloud --quiet`, `plink -batch`) and ensure SSH keys are cached;
  never execute headless commands that can hang on interactive `y/N` prompts or host-key confirmations.
- Query authoritative production logs via `journalctl -u trade-bot -o short-iso` on the VM rather than
  inspecting local development logs or unmanaged scratch files.
- For PowerShell-to-Linux diagnostics, avoid nested shell substitutions and long quoted scripts. Run
  small read-only remote commands and parse their structured output locally.
- Keep default test discovery bounded to the canonical `tests/` directory. Scratch trials must not be
  collected by normal CI or used to judge suite health.
- Test count alone is not a pruning signal. Remove only superseded contracts, proven duplicates, or
  tests whose production behavior is removed in the same change.
- A failing test is not obsolete. Isolate and repeat it to distinguish a deterministic regression from
  a timing flake; repair lifecycle synchronization instead of deleting the assertion.
- Do not delete tests for code that still ships. Deprecate implementation and tests together while
  retaining coverage for compatibility DTOs or imports that remain active.

## 9. Definition of Done

- Repository facts and task routing were verified before delegation.
- The contract used minimal read/write authority and complete acceptance behavior.
- The worker changed only authorized files; protected digests and user changes were preserved.
- Focused harness verification passed, followed by the full relevant suite and diff checks.
- Codex completed an independent semantic review, including adversarial and boundary conditions.
- Feature status, validation status, provider/live-call activity, unresolved uncertainty, and AGY
  telemetry availability were reported separately.

## Maintenance Rule

When this file grows beyond roughly 150 lines or repeats a lesson, merge related rules and remove dated
evidence. Preserve only the strongest generalized rule; Git history remains the detailed audit log.
