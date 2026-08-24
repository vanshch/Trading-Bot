# Implementation Plan: Live Capture Reliability and Complete Signal Coverage

## Objective

Fix every defect confirmed during the August 24, 2026 Mehak Trades broadcast audit while preserving the existing fail-closed Gemini verification gate, Telegram secret handling, channel-mode behavior, and user-owned untracked broadcast artifacts.

## Acceptance criteria

- A Streamlink process that starts successfully but later exits or reaches EOF triggers one bounded direct-FFmpeg fallback using the freshly resolved media URL before the outer monitor restarts.
- Streamlink and FFmpeg diagnostics are drained without deadlock, retained only as a bounded tail, sanitized so signed URLs or credentials cannot reach logs, and emitted when a process fails.
- Repeated interrupted capture sessions use configurable exponential backoff and reset only after a sustained healthy capture window; shutdown remains immediately interruptible.
- Alert deep links use one broadcast-relative clock that survives audio-capture restarts.
- English trigger coverage includes the audited execution wording (`enter`, `scalp`) and position-management wording (partial booking, CTC/breakeven, stop changes, exit/close/cut).
- Gemini classifies entry versus management versus exit explicitly. Conditional, educational, retrospective, and viewer-request language remains rejected.
- Confirmed partial-profit, stop-management, and exit signals can produce distinct Telegram alerts without opening duplicate trades; an explicit exit closes an active trade immediately.
- Focused regression tests reproduce the audited failures. The complete offline suite, Ruff, dependency checks, and compile checks pass without live provider calls.
- `experience.md` records the failure pattern and reusable prevention checklist.

## Protected paths and state

- Do not read, print, edit, stage, or commit `.env` or credentials.
- Preserve all pre-existing untracked files under `data/`, including VOD audio fragments, captions, transcripts, and VM journals.
- Do not commit, push, deploy, or call live Groq, Gemini, YouTube, or Telegram APIs during delegated implementation.

## 1. Make audio transport self-recovering and observable

1. Extend `AudioStreamManager` to accept the freshly resolved direct media URL separately from the public YouTube watch URL.
2. If Streamlink starts but later exits, stalls, or produces EOF, stop its FFmpeg pipeline and attempt direct FFmpeg exactly once within the same capture session. If the direct fallback also fails, return control to the outer monitor for a fresh yt-dlp resolution.
3. Drain subprocess stderr concurrently into a bounded in-memory tail. Sanitize URLs/query strings and registered secret-like values before logging a concise failure diagnostic. Close threads, pipes, and subprocesses deterministically on fallback, cancellation, and shutdown.
4. Pass the resolved direct URL from `TradeMonitoringService.monitor_channel` to the audio manager.
5. Add configurable exponential restart backoff with an initial delay, maximum delay, and sustained-success reset window. Avoid jitter in unit tests; production jitter may be bounded and injectable.
6. Keep one broadcast start reference across audio restarts and use it for Telegram timestamp links. Prefer a validated YouTube broadcast timestamp when available, otherwise fall back to first live detection.
7. Add regression tests for runtime Streamlink exit, direct fallback success/failure, bounded sanitized diagnostics, no subprocess/thread leaks, backoff growth/reset, interruptible shutdown, direct-URL propagation, and a stable elapsed timestamp across restarts.

## 2. Detect and alert entry plus position-management signals

1. Expand English execution keywords to cover the audited phrases, including `enter`, `scalp`, `scalping`, and concise executed-entry variants.
2. Add management triggers for partial booking, profit booking, CTC/cost-to-cost/breakeven, stop movement/trailing, target/SL hits, close/closed, exit, and cut.
3. Extend the multimodal verification schema and prompt with an explicit fail-closed signal type: `ENTRY`, `PARTIAL_EXIT`, `MOVE_STOP`, `EXIT`, or `NONE`. Require evidence from actual execution/immediate instruction and reject conditional, educational, retrospective-only, and viewer-request statements.
4. Update trade-state handling so:
   - `ENTRY` retains existing new-trade/duplicate suppression behavior;
   - `PARTIAL_EXIT` and `MOVE_STOP` preserve an active trade and emit a management transition;
   - explicit `EXIT` closes an active trade immediately without waiting for generic miss-threshold frames;
   - a verified management instruction can still alert when the earlier entry was missed, without fabricating an active position.
5. Add a dedicated escaped Telegram management alert with action, instrument/direction when known, spoken evidence, levels, confidence, and the stable broadcast timestamp link.
6. Add bounded duplicate suppression for repeated management commentary without suppressing different actions.
7. Extend session metrics if needed so management detections and delivered alerts are distinguishable from new trades.
8. Add regression tests using the audited wording:
   - `You can enter with one lot. I am taking a small scalp.`
   - `Book some profit and move SL to CTC.`
   - `My remaining trade is closed/cut.`
   - negative conditional/educational examples around the 37 level and viewer requests.

## 3. Independent acceptance, documentation, and deployment

1. Review every changed file for concurrency safety, fail-closed classification, HTML escaping, secret hygiene, and backward-compatible session-state loading.
2. Run focused tests, then the complete test suite, Ruff, dependency validation, and `compileall`. No live provider tests.
3. Append the observed transport/trigger lessons and regression checklist to `experience.md`.
4. Commit only reviewed source, tests, configuration, documentation, the implementation plan, and `experience.md`; exclude all user-owned `data/` artifacts.
5. Push the accepted commit, update the GCE VM with a fast-forward pull, compile on the VM, restart `trade-bot.service`, and verify the deployed revision, PID, startup logs, and active state.
