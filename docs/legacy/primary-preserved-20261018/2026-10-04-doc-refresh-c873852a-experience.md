# Telegram Alert Bot Engineering Experience

This file contains durable engineering rules, architectural constraints, and operational lessons for the **Telegram Alert Bot** and its multi-alert verification pipeline.
For archived historical experience on the underlying live-stream audio capture and transport pipeline, see `archive.md` (read only when explicitly requested).

---

## 1. Telegram Multi-Alert Engine & Notification Constraints

The bot dispatches six distinct categories of alerts to Telegram. Each alert category has specific formatting, deduplication, and fail-closed safety requirements.

### Alert Categories & Payloads

1. **Fast Live Trade Signals (`ENTRY`)**:
   - **Triggers**: Verified spoken entry trigger and/or chart position marker.
   - **Payload**: Action badge (`📈 BUY (Long)`, `📉 SELL (Short)`, `🟢 CALL (CE)`, `🔴 PUT (PE)`), validated instrument code, entry price, SL/TGT levels, verbatim spoken trigger quote, AI confidence percentage, localized timestamp, and a deep-link with broadcast-relative timestamp (`?t=XhYmZs`).
   - **Attachment**: Attached live chart screenshot (`sendPhoto`) when screenshots are enabled.
   - **Fail-Closed Rule**: NEVER dispatch an entry alert if the instrument is `"UNKNOWN"`, empty, or missing. The verifier must resolve a concrete tradable ticker (`BTCUSDT`, `XAUUSD`, `NIFTY`) before dispatch.

2. **Trade Position Management Alerts (`PARTIAL_EXIT`, `MOVE_STOP`)**:
   - **Triggers**: Streamer executes partial profit booking (`PARTIAL_EXIT`) or moves stop loss to cost-to-cost / breakeven / trailing (`MOVE_STOP`).
   - **Payload**: Dedicated action badges (`💰 PARTIAL PROFIT BOOKED`, `🛡️ STOP MOVED / TRAILING (CTC)`), updated SL/TGT levels, spoken evidence, and confidence.
   - **State Preservation**: Updates the active trade state without creating duplicate trade records. Can alert even if the initial entry was missed, but must never fabricate an unverified active position.

3. **Trade Exit / Closed Alerts (`EXIT` / `TRADE_CLOSED`)**:
   - **Triggers**: Streamer confirms position exit/cut, or target/stop hit.
   - **Payload**: Closing timestamp, total trade duration, direction, instrument, and stream link.
   - **Suppression Rule (IDLE State)**: If the trade tracker state is `TradeState.IDLE`, an `EXIT` signal must evaluate to `StateTransition.NONE`. Never dispatch an exit alert when no trade is active (prevents false alerts on streamer comments like *"candle closed"*).

4. **Channel Live-Status Alerts (`STREAM IS LIVE` / `STREAM ENDED`)**:
   - **Triggers**: `LiveChannelListener` detects channel live status transition.
   - **Exactly-Once Deduplication**: Every broadcast is identified by its deterministic YouTube broadcast ID. The alert claim is atomically written to `data/live_alert_state.json` *before* dispatching to Telegram. Process restarts or repeated live checks never re-alert the same broadcast ID.
   - **Hysteresis**: Requires two consecutive confirmed `OFFLINE` observations before resetting channel online state, preventing false flap on transient `yt-dlp` errors.

5. **Scheduled High-Impact Economic News Briefs**:
   - **Triggers**: Daily scheduled briefs at 08:00 and 14:00 IST via Forex Factory calendar feed.
   - **Payload**: High-impact scheduled events grouped by currency flag and release time.
   - **Formatting**: Clean semantic whitespace, no unnecessary decorative dividers, full HTML escaping.

6. **Pre-Event Economic News Countdown Alerts**:
   - **Triggers**: Proximity monitor running every 30 seconds identifies a High-impact economic release exactly 30 minutes away.
   - **Payload**: 30-minute warning alert with event name, currency flag, impact level, previous value, and forecast.
   - **Deduplication**: In-memory event tracking prevents duplicate countdown alerts for the same calendar event ID.

---

## 2. Telegram API & Formatting Invariants

- **Photo Caption 1024-Character Limit**:
  - Telegram `sendPhoto` captions are strictly capped at 1024 characters.
  - If formatted text (including HTML tags and dynamic quotes) exceeds 1024 characters, the dispatcher must fall back to `sendMessage` rather than allowing delivery to fail.
- **Strict HTML Escaping**:
  - Every dynamic field interpolated into HTML templates MUST be passed through `html.escape()`: streamer names, instrument codes, trigger quotes, reasoning text, news event titles, previous/forecast numbers, and URLs inside `href` attributes (`html.escape(url, quote=True)`).
  - An unescaped `<` or `&` from a streamer quote or market expression will cause Telegram to reject the payload with `HTTP 400 Bad Request: can't parse entities`.
- **Formatting Design**:
  - Prefer clean semantic whitespace over excessive decorative divider characters (`━━━━━━━━━━━━━━━━━━━━`).
  - Use code tags (`<code>...</code>`) for tickers and prices to enable one-tap mobile copying.
- **API Retry & Network Boundaries**:
  - Retry transient network timeouts, HTTP 429 (rate limits), and HTTP 5xx responses using exponential backoff.
  - Suppress retries immediately on non-retryable 4xx errors (HTTP 400, 401, 403, 404) to avoid log spam and redundant traffic when credentials or chat IDs are invalid.

---

## 3. Multimodal Verification & Provider Failover Rules

- **Multi-Provider Fallback Waterfall**:
  - Primary route: Gemini (supports live screenshots).
  - Fallback routes: Cerebras -> Groq -> SambaNova -> OpenRouter (text/transcript only).
  - Explicit provider order is tried sequentially on failure or rate-limiting.
- **Circuit Breaker Cooldowns**:
  - `HTTP 429` advances immediately to the next provider.
  - Model cooldown defaults to 60s (or honors `Retry-After`).
  - Daily quota exhaustion enforces an 86,400-second (24-hour) cooldown.
  - Missing API keys or auth/billing failures cool down that provider for 24 hours.
- **Grounding & First-Person Confirmation**:
  - Verifier prompts must demand direct first-person, present-tense execution confirmation (*"I entered"*, *"Bought 1 lot"*).
  - Reject passive chat reading (*"Viewer asks if he should buy"*), hypothetical setups, retrospective analysis, and conditional statements (*"If it breaks 37, we will enter"*).
- **Text-Only Fallback Prompting**:
  - Non-Gemini fallback models receive transcript text only. Prompts must explicitly instruct the model not to hallucinate or invent visual chart evidence.

---

## 4. Contract Design & AGY/Codex Execution

- **Small, Complete Contracts**:
  - Allow only the exact files needed for an edit. Do not expose unnecessary tests or config files.
  - State observable output expectations exactly, including empty, partial-data, and boundary conditions.
- **Treat Worker Status and Repository State Separately**:
  - The deterministic verifier and test suite own `PASS`, not prose or agent summaries.
  - Codex/maintainer must review semantics after verification. Classify escalations explicitly.
- **Adversarial Acceptance**:
  - Test failure boundaries, not just happy paths: malformed LLM responses, empty transcripts, unknown instruments, Telegram 400 rejections, network disconnects, and process cancellations.
  - Fail closed on ambiguity: explicit `NONE` must take no action; an unknown trade direction must never default to `BUY`.

---

## 5. Audit Evidence & Production VM Diagnostics

- **Target VM**: Production runs on Google Compute Engine VM `trade-bot-vm` in `us-central1-a` as systemd unit `xauusd-bot.service` (working directory `/opt/xauusd_telegram_bot`).
- **Authoritative Logs**: Query production telemetry via `journalctl -u xauusd-bot -n 100 --no-pager` rather than assuming local development states.
- **Safe Remote Commands**: Pass non-interactive flags (`gcloud --quiet`, `ssh -o BatchMode=yes`). On Windows executing via `gcloud compute ssh`, Base64-encode payloads to prevent quote/escape stripping.
- **Test Integrity**: Default test discovery must be bounded to `tests/`. Do not delete tests for active code paths. Keep all unit tests fully offline with mocks and synthetic buffers.

---

## 6. Codex CLI, Computer Use Agent (CUA) & Chrome Extension Invariants

- **Desktop App vs. CLI Pipe Lock Contention**:
  - The ChatGPT / Codex Desktop App (`ChatGPT.exe`) runs an internal embedded app-server (`codex.exe -c features.code_mode_host=true app-server`), `codex-computer-use-swift.exe`, and a `cua-repl` Node process bound to a Windows named pipe (`\\.\pipe\codex-computer-use-*`).
  - Running background CLI invocations (`codex exec`) requesting computer-use or `@Chrome` attempts to connect or bind to the same native pipe. This creates a resource conflict and spawns secondary `node_repl.exe` instances that linger in the background.
  - While these conflicting processes are alive, the interactive Desktop app fails to initiate browser tasks (erroring with "session busy" or hanging indefinitely).
  - **Rule**: Never run headless background `codex exec` computer-use tasks while the ChatGPT/Codex Desktop app is active. If contention occurs, terminate the orphaned `node_repl.exe` and `codex-computer-use-swift.exe` processes or restart `ChatGPT.exe`.

- **Chrome Extension Security Gate on Sensitive Domains**:
  - Codex's `@Chrome` connector communicates through the official Codex Chrome Extension.
  - When accessing web domains like `github.com`, Chrome enforces an interactive security permission prompt in the browser toolbar: *"Codex wants to access github.com - Allow / Deny"*.
  - Because headless CLI execution cannot interact with Chrome's native security prompts, Chrome defaults to fail-closed rejection: `Chrome's browser security policy blocked access to GitHub because permission was denied.`
  - **Rule**: User-session browser automations (like checking GitHub login state) should be triggered directly within the interactive Codex Desktop UI where the user can click "Allow" on the Chrome toolbar extension prompt.

---

## 7. Definition of Done

- Task requirements and alert contracts verified against `README.md`.
- No credential leakage or unmasked secrets in logs, payloads, or commits.
- All dynamic Telegram text fields properly escaped and within length bounds.
- Fail-closed validation verified for new alert types and unknown instruments.
- Focused unit tests pass with zero warnings, followed by the complete offline test suite.
