# YouTube Live Stream Trade Monitoring Bot

An audio-first monitoring worker that polls YouTube trading channels, translates live audio to English with Groq Audio Translations (`whisper-large-v3`), detects configured entry and position-management phrases, verifies candidates with an ordered LLM provider chain (Gemini supports screenshots; other routes use transcripts), and dispatches Telegram alerts. The OpenCV filter is currently used by the offline video benchmark, not by the live orchestration loop.

---

## Architecture Overview

```
[YouTube Live Stream]
        ├──► [StreamManager (yt-dlp: liveness check & broadcast metadata)]
        │              │
        │              ▼ (canonical public YouTube watch URL)
        ├──► [AudioStreamManager (Streamlink YouTube plugin, hls-live-edge 3)]
        │              │ (stdout pipe: raw media)
        │              ▼
        │     [FFmpeg PCM Decoder (no -re input throttling)]
        │              │
        │              ▼
        │     [Groq Translations (whisper-large-v3)]
        │              │
        │              ▼
        │     [Spoken Trigger Detector]
        │              │
        └──► [Current Chart Screenshot via OpenCV VideoCapture]
                       │
                       ▼
          [Ordered LLM Verification + Cooldowns]
                       │
                       ▼
[TradeStateTracker (NEW_TRADE, MANAGEMENT_UPDATE, TRADE_CLOSED)]
                       │
                       ▼
       [Telegram Alert + Session Metrics]
```

### Audio Transport & Streamlink Plugin Ownership
- **Liveness & Broadcast Metadata**: `StreamManager` (via `yt-dlp`) validates whether the channel is currently live, extracts the broadcast ID, and validates broadcast start timestamps. It also provides frames to `cv2.VideoCapture` for chart screenshot verification.
- **Playback Resolution Ownership**: Rather than routing audio capture through a client- and session-bound signed Googlevideo media URL, `TradeMonitoringService` passes only the canonical public YouTube watch URL (`https://www.youtube.com/watch?v=VIDEO_ID`) to `AudioStreamManager` and Streamlink. In the measured failure, the manifest still advertised hours of validity while newly referenced media segments returned HTTP 403, so the failure must not be modeled as a simple one-minute expiry timer.
- **Streamlink YouTube Plugin**: Streamlink's dedicated YouTube plugin resolves playback manifests and maintains live HLS playlist updates. The transport is configured to a proven three-segment live edge (`--hls-live-edge 3`) with bounded ringbuffer buffering (`--ringbuffer-size 64M`) and segmented retry policies.
- **Unthrottled FFmpeg PCM Decoding**: FFmpeg decodes media piped from Streamlink's stdout directly into 16 kHz mono 16-bit PCM (`pipe:0` -> `pipe:1`). No input throttling (`-re` or `-readrate`) is applied on FFmpeg stdin, preventing desynchronization and synthetic buffer stalls against live transport feeds.
- **Provider-Aware Failure Fallbacks**:
  - **YouTube Streams**: When Streamlink fails at startup, exits, stalls, or reaches EOF, the audio capture session terminates cleanly and fails back to the outer supervised restart loop (with exponential backoff and live-status re-resolution). Direct FFmpeg fallback against signed Googlevideo URLs is explicitly disabled for YouTube to prevent HTTP 403 failure loops.
  - **Non-YouTube Streams**: Generic HLS streams (e.g., custom stream URLs) preserve one-shot direct FFmpeg fallback on Streamlink startup or runtime failures.

---

## Quickstart & Local Setup

### 1. Prerequisites
- **Python 3.12+**
- **FFmpeg** installed and on system `PATH`
- Groq API key (from [Groq Console](https://console.groq.com/))
- Google Gemini API key (from [Google AI Studio](https://aistudio.google.com/))
- Telegram Bot Token & Chat ID (from `@BotFather`)

### 2. Install Dependencies
```bash
# Create local virtual environment
python -m venv .venv

# Activate virtual environment
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install runtime and test requirements
pip install -r requirements-dev.txt
```

### 3. Configure Credentials
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

```env
GROQ_API_KEY=your_groq_api_key_here
GEMINI_API_KEY=your_gemini_api_key_here
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_or_group_id
LOG_LEVEL=INFO
```

### 4. Configure Channels & Monitoring Modes
Edit `config.yaml` to specify target YouTube channels and their assigned monitoring modes.

#### Supported Channel Modes
Each channel in `channels` can be assigned one of two monitoring modes via the `mode` field (defaults to `full_analysis` if omitted for backward compatibility):
- **`full_analysis`**: Full end-to-end pipeline. Receives stream live-status notifications and runs real-time audio extraction, Groq Whisper audio translation to English, keyword trigger detection, Gemini multimodal verification, and trade signal alerts.
- **`notifications_only`**: Live-status notifications only. Monitors channel online/offline state via `LiveChannelListener` and sends Telegram live stream announcements without spawning audio streaming, Groq translation, Gemini verification, or trade analysis pipelines.

```yaml
channels:
  - name: "Sample Trader 1"
    url: "https://www.youtube.com/@SampleTrader1/live"
    enabled: false
    mode: full_analysis
    trigger_keywords: []

  - name: "Sample Trader 2"
    url: "https://www.youtube.com/@SampleTrader2/live"
    enabled: false
    mode: full_analysis
    trigger_keywords: []

  - name: "Mehak Trades"
    url: "https://www.youtube.com/@tradewithmehaktrades/live"
    enabled: true
    mode: full_analysis
    trigger_keywords:
      - active
      - activated
      - enter
      - entry
      - entered
      - scalp
      - scalping
      - bought
      - buy
      - sold
      - sell
      - short
      - long
      - position
      - executed
      - order placed
      - book profit
      - partial booking
      - ctc
      - move sl
      - close
      - exit
      - cut
    trader_profile: Mehak Trades is an SMC and price action Forex and Gold trader broadcasting live commentary in Hindi/Hinglish. She focuses on XAUUSD, Gold, Forex, Bitcoin, Order Blocks, Fair Value Gaps (FVG), liquidity sweeps, and trade setups.

  - name: "Naresh Cool Trader"
    url: "https://www.youtube.com/@NareshCoolTrader/live"
    enabled: true
    mode: notifications_only
    trigger_keywords:
      - active
      - activated
      - entry
      - entered
      - bought
      - buy
      - sold
      - sell
      - short
      - long
      - position
      - executed
      - order placed
      - call option
      - put option
      - scalping
      - breakout
    trader_profile: Naresh Cool Trader is an Indian stock market index options and scalping trader broadcasting live commentary in Hindi. He focuses on NIFTY, BANKNIFTY, FINNIFTY, Option Buying (CE/PE), scalping setups, price action levels, support/resistance, CPR, and fast order executions.

audio:
  model: "whisper-large-v3"
  chunk_duration_seconds: 10.0
  context_buffer_seconds: 120
  initial_prompt: "Trade active. Entry taken. Buy executed. Sell executed. Short position. Long position. Stop loss. Target hit. Order placed. Scalp. Scalping. Book profit. Move SL to CTC. Trade closed. Gold, XAUUSD, BTC, Bitcoin, Forex."
  temperature: 0.0

capture:
  frame_interval_seconds: 5.0
  live_poll_interval_seconds: 60.0
  restart_initial_delay_seconds: 1.0
  restart_max_delay_seconds: 30.0
  restart_backoff_factor: 2.0
  restart_reset_window_seconds: 300.0

cv_filter:
  min_line_length_ratio: 0.10
  max_line_angle_degrees: 5.0

ai:
  model_name: "gemini-3.6-flash"
  confidence_threshold: 0.70

telegram:
  include_screenshot: true
  caption_timezone: "Asia/Kolkata"

news:
  enabled: true
  feed_url: "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
  schedule_times:
    - "08:00"
    - "14:00"
  timezone: "Asia/Kolkata"
  impact_levels:
    - "High"
  pre_event_alert_enabled: true
  pre_event_minutes: 30
  poll_interval_seconds: 30.0
  timeout_seconds: 15.0
  max_retries: 3
  retry_delay_seconds: 3.0
  rate_limit_cooldown_seconds: 900.0
  min_retry_interval_seconds: 300.0
```

### 5. Run the Monitoring Bot
```bash
python main.py --config config.yaml --env .env
```
Upon startup, the orchestrator logs both the total enabled channel count and the full-analysis breakdown:
```text
Starting YouTube Live Stream Trade Monitoring Bot for 2 channel(s) (1 full analysis, 1 notifications only)...
```

### 6. Run a Bounded Local Test

Run a 5-minute bounded test:
```bash
python main.py --config config.yaml --env .env --test-minutes 5
```

Run a 10-minute bounded test:
```bash
python main.py --config config.yaml --env .env --test-minutes 10
```

Test mode runs role-appropriate preflight verification before monitoring:
- **Telegram Preflight**: Always executed whenever any enabled channel is configured.
- **Groq & Gemini Preflight**: Executed only when at least one enabled channel is in `full_analysis` mode (skipped if all channels are `notifications_only`).
- Polls offline channels at least every 15 seconds, enables debug logging by default, and shuts down cleanly after the requested duration.

### 7. Recorded-Livestream Offline Benchmark

Run the offline benchmark to evaluate the OpenCV pre-filter candidate detection rate,
sampling accuracy, and throughput on recorded livestream video files without loading
provider credentials or invoking Gemini/Telegram APIs.

#### A. Download YouTube Livestream Recording with `yt-dlp`

Download a full recording or time-bounded clips (5-minute or 10-minute sections):

```bash
# Download a full recording:
yt-dlp -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" -o "recording.mp4" "https://www.youtube.com/watch?v=VIDEO_ID"

# Download a 5-minute slice (00:00:00 to 00:05:00):
yt-dlp --download-sections "*00:00:00-00:05:00" -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" -o "recording_5min.mp4" "https://www.youtube.com/watch?v=VIDEO_ID"

# Download a 10-minute slice (00:00:00 to 00:10:00):
yt-dlp --download-sections "*00:00:00-00:10:00" -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best" -o "recording_10min.mp4" "https://www.youtube.com/watch?v=VIDEO_ID"
```

#### B. Run Benchmark CLI

Run offline benchmarking across the recording, sampling from timestamp zero:

```bash
# Benchmark full recording with production config (default 5s interval):
python benchmark.py --video recording.mp4 --config config.yaml

# Benchmark 5-minute recording and save JSON report:
python benchmark.py --video recording_5min.mp4 --output report_5min.json

# Benchmark 10-minute recording with explicit 5.0s interval:
python benchmark.py --video recording_10min.mp4 --interval 5.0 --output report_10min.json
```

#### C. Sample JSON Output

The benchmark outputs a structured JSON report:
```json
{
  "source_metadata": {
    "video_path": "recording_5min.mp4",
    "duration_seconds": 300.0,
    "fps": 30.0,
    "total_video_frames": 9000,
    "width": 1920,
    "height": 1080,
    "frame_interval_seconds": 5.0
  },
  "total_sampled_frames": 60,
  "flagged_frames": 8,
  "not_flagged_frames": 52,
  "flagged_percentage": 13.33,
  "not_flagged_percentage": 86.67,
  "color_breakdown": {
    "BLUE": 6,
    "RED": 2,
    "BOTH": 0,
    "NONE": 52
  },
  "elapsed_time_seconds": 1.25,
  "effective_throughput_fps": 48.0
```

---

### 8. Whisper Base vs. Small STT Benchmark

Run the offline speech-to-text benchmark to compare local Faster-Whisper `base` and `small` models across recorded audio/video files. The benchmark measures per-file processing time, real-time factor (RTF), memory, inter-model agreement, and Word Error Rate (WER) accuracy when reference transcripts are supplied.

> Note: Running the offline local Whisper benchmark requires the optional dependency `faster-whisper` (`pip install faster-whisper`).

#### A. Run Benchmark on Latest Local Recordings

```bash
# Benchmark latest 5 recordings discovered in data/ (default: base vs small):
python -m src.whisper_benchmark --recordings-dir data/ --limit 5

# Benchmark latest 10 recordings and save JSON report:
python -m src.whisper_benchmark --recordings-dir data/ --limit 10 --output stt_benchmark_report.json

# Benchmark explicit media files:
python -m src.whisper_benchmark --inputs recording1.mp4 recording2.wav --output report.json

# Via scripts/compare_stt.py:
python scripts/compare_stt.py --benchmark --recordings-dir data/ --limit 5
```

#### B. Benchmark with Ground-Truth Reference Transcripts

When ground-truth reference transcripts (`.txt`, `.ref.txt`, or YouTube `.json3` caption files) are available, the benchmark computes Word Error Rate (WER) and Character Error Rate (CER) with full Unicode NFKC normalization and declares an accuracy winner:

```bash
# Benchmark with reference transcripts in a directory:
python -m src.whisper_benchmark --inputs data/*.wav --reference-dir data/references/ --output accuracy_report.json
```

*Note: Without reference transcripts, the benchmark measures inter-model agreement (sequence token similarity & Jaccard index) and does not claim an accuracy winner.*

#### C. Custom Model, Device, and Caching Options

```bash
# Run on CUDA with float16 quantization:
python -m src.whisper_benchmark --recordings-dir data/ --device cuda --compute-type float16

# Force re-transcription bypassing cache:
python -m src.whisper_benchmark --recordings-dir data/ --no-cache

# Compare different models (e.g. tiny vs base vs small):
python -m src.whisper_benchmark --recordings-dir data/ --models tiny base small
```

---

### 9. Standalone Offline Performance Evaluator

Run the standalone offline performance evaluator to benchmark local hot path execution latency, throughput, and percentiles (mean, median, p95, p99) without network access, credentials, or live streams.

#### A. Evaluated Hot Paths
1. **`audio_trigger_detection`**: In-memory `RollingTranscriptBuffer` ingestion, time-window pruning, context formatting, and `AudioTriggerDetector` regex matching across a deterministic English-translated corpus.
2. **`trade_state_transitions`**: `TradeStateTracker` 4-step lifecycle transition evaluations (`IDLE` -> `NEW_TRADE` -> `STILL_ACTIVE` -> `TRADE_CLOSED`).
3. **`cv_filter_720p`**: `CVFilter` HSV masking, morphological horizontal structuring, and Hough line transform over deterministic synthetic 1280x720 (720p) BGR frames.
4. **`session_metrics_persistence`**: `LiveSessionLogger` thread-safe session creation, metric increments, atomic JSON file replacement, and summary aggregation in an isolated temporary store.

#### B. Command Usage

```bash
# Run all scenarios with default settings (100 timed iterations, 10 warmup):
python -m src.offline_evaluator

# Run with custom iterations and save machine-readable JSON report:
python -m src.offline_evaluator --iterations 200 --warmup 20 --output perf_report.json

# Run specific scenarios (e.g. cv_filter_720p and audio_trigger_detection):
python -m src.offline_evaluator --scenarios cv_filter_720p,audio_trigger_detection

# Run with automated performance budget verification:
python -m src.offline_evaluator --check-budget

# Output clean JSON only:
python -m src.offline_evaluator --quiet
```

#### C. Output Meaning & Metrics
- **`iterations`**: Number of timed runs evaluated.
- **`throughput_ops_per_sec`**: Executions per second for that local component.
- **`latency_stats_ms`**: Millisecond latency distribution (`min_ms`, `max_ms`, `mean_ms`, `median_ms`, `p95_ms`, `p99_ms`).
- **`metadata`**: Scenario-specific deterministic accounting details (e.g., candidate detections, lines detected, trigger counts, transition counts).

#### D. Scope & Limitations
- **Local Component Evaluation Only**: This evaluator measures CPU/in-memory speed of local algorithms and disk persistence.
- **No End-to-End Latency**: It does not measure remote provider response times (Groq API, Gemini API, or Telegram Bot API).
- **No Provider Accuracy Claims**: It evaluates code path throughput and latency, not transcription or multimodal model prediction accuracy.

---

## Running Automated Tests

Run the full automated test suite using `unittest`:
```bash
python -m unittest discover -v tests
```
Or with `pytest`:
```bash
pytest -v
```

---

## Current Operational Status and Priorities

The production worker runs as the `trade-bot` systemd service on the Google Compute Engine VM `trade-bot-vm` (`us-central1-a`). It is a continuous worker, not an HTTP service.

### Forex Factory Calendar Integration & Reliability (Verified & Operational)

The economic calendar integration is fully operational in production on `trade-bot-vm` with 100% test coverage and multi-hour cloud verification:
- **Empty Event Cache Decoupling**: The calendar fetcher cleanly distinguishes an empty, successful event list (e.g. market bank holidays, weekends, or days without Red-Folder releases) from an error. `ForexNewsService` records `_last_successful_fetch_utc` on empty results and avoids redundant queries on subsequent 30s proximity ticks.
- **HTTP 429 Circuit Breaker & Cooldown**: If Cloudflare / Fair Economy issues an HTTP 429 ("Too Many Requests"), `ForexNewsFetcher` immediately aborts retries to avoid multiplying traffic, respects `Retry-After` (or defaults to `rate_limit_cooldown_seconds: 900.0` / 15 mins), and enforces an in-memory circuit breaker that suppresses upstream calls until the cooldown expires.
- **Sanitized Logging**: Replaced raw Cloudflare HTML error body dumps with concise single-line warning logs that state the exact remaining cooldown seconds and UTC recovery time.
- **Mirror Failover**: Transparently falls back to the secondary CDN mirror (`cdn-nfs.faireconomy.media`) on 5xx server or upstream network connection failures.
- **Throttled Retry Cadence**: Failed attempts are throttled by `min_retry_interval_seconds: 300.0` (5 minutes) before re-attempting upstream connections.
- **Production Proven**: Verified in live cloud runtime over continuous 3.5+ hour windows with 0 HTTP 429 occurrences and clean hourly and timezone-rollover refetches.

### Operational Service Health Monitoring

A monitoring utility is provided at [`scripts/monitor_service.py`](file:///d:/projects/bot_agy/scripts/monitor_service.py) to audit service uptime, memory/CPU consumption, and systemd journal logs over a customizable period (e.g., 1 hour):

```bash
# Monitor the production service for 1 hour with 60s reporting intervals:
python scripts/monitor_service.py --duration 3600 --interval 60

# Save output to structured JSON report:
python scripts/monitor_service.py --duration 3600 --output-json report.json
```

### Live Stream Audio Capture Coverage (Production Telemetry)

Telemetry recorded from multi-hour production live streams on `trade-bot-vm` (`data/live_sessions.json`):

| Session Broadcast ID | Date & Time (UTC) | Stream Duration | Audio Captured | Live Audio Coverage | Chunks | Audio Triggers | Dispatched Trades | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `XJJCSFVadlY` | 2026-09-04 09:30–12:35 | 11,102.0s (~3.08h) | 10,940.0s | **98.54%** | 1,094 | 117 | 4 | COMPLETED |
| `frHRy1aCe5U` | 2026-09-07 03:37–06:40 | 10,993.6s (~3.05h) | 10,870.0s | **98.88%** | 1,087 | 128 | 0 | COMPLETED |
| `Mm1WHSH4unQ` | 2026-09-07 09:31–12:31 | 10,834.7s (~3.01h) | 10,640.0s | **98.20%** | 1,064 | 129 | 4 | COMPLETED |

- **Audio Capture Reliability**: The Streamlink unthrottled PCM capture pipeline delivers **98.54% average audio coverage** across continuous 3+ hour broadcasts. The missing ~1.5% occurs almost exclusively during broadcast sign-off before channel offline status is established.
- **Verification Decision Bottleneck**: While audio ingestion achieved >98% coverage, AI decision coverage suffered a steep drop after the first 30–45 minutes due to the Gemini free tier limit (20 req/day). Over 1,136 `429 RESOURCE_EXHAUSTED` errors were logged across the day, dropping 178 verification evaluations and blinding the bot to subsequent trades.

### Immediate Next Problem: Suppress Spurious Alerts on UNKNOWN or Missing Instruments

- **Symptom**: On September 7 at `10:00:13 UTC`, the bot dispatched `DISPATCHING ENTRY ALERT: SELL on UNKNOWN` with confidence 0.85 when the transcript was *"Entry closed. Entry active. Entry here is 410.9."*
- **Root Cause**: Neither [`src/state_tracker.py`](file:///d:/projects/bot_agy/src/state_tracker.py) nor [`main.py`](file:///d:/projects/bot_agy/main.py) checks whether `trade.instrument` is `"UNKNOWN"` or empty before triggering an alert.
- **Required Fix**: Enforce fail-closed instrument validation. If `trade.instrument in ("UNKNOWN", "", None)`:
  - Do not dispatch an alert to Telegram.
  - Require the multimodal verifier to resolve an explicit tradable ticker (e.g. `BTCUSDT`, `XAUUSD`, `NIFTY`) before transitioning state to `NEW_TRADE`.

### Production Engineering Backlog (To Be Solved)

#### 1. Multi-provider verification fallback (implemented locally; deployment pending)

`ai.model_name` is the primary Gemini route. `ai.fallback_models` is an explicit ordered list of `{provider, model}` entries, tried once each on failure. The configured preference is Gemini 3.6 / 3.7 / 2.5 Flash, Cerebras GPT-OSS 120B, Groq GPT-OSS 120B, SambaNova DeepSeek V3.1, Groq/SambaNova Llama 3.3 70B, OpenRouter free Llama 3.3 70B, then Groq Llama 3.1 8B. This is an operator quality preference, not a verified trading-accuracy ranking. Models and free access can change; unavailable models fall through.

- Optional credentials: `CEREBRAS_API_KEY`, `SAMBANOVA_API_KEY`, `OPENROUTER_API_KEY`. Groq chat reuses `GROQ_API_KEY`; transcription is unchanged. Missing optional keys skip that provider.
- The separate private file `.env.providers.local` is git-ignored and loaded beside the selected `--env` file (current directory by default). Existing environment and `.env` values take precedence. Never commit it or print its contents.
- A 429 advances immediately. Model cooldown defaults to 60 seconds and honors longer `Retry-After` / reset durations. Explicit daily exhaustion uses a conservative 86,400-second cooldown, even when the provider suggests a short minute retry. Auth/billing failures cool down the provider for 86,400 seconds; missing models cool down that model. Other failures/malformed output cool down for 30 seconds.
- Cooldowns are in memory, shared across channels by one verifier, and reset on process restart. Higher-ranked models are retried after cooldown expires. Selection is serialized to prevent concurrent quota storms.
- Per-request timeout is 15 seconds; total verification budget is 60 seconds. A slow chain may not reach its lowest route within that budget. The old `ai.max_retries` / `retry_delay_seconds` fields remain accepted for compatibility but do not multiply waterfall attempts. Gemini SDK retries are disabled for generation.
- Non-Gemini routes receive text only, even when screenshots are enabled. Their prompts explicitly prohibit invented visual evidence. Responses use the same schema and confidence threshold. A valid `NONE` stops the chain. Total provider failure marks verification unavailable and skips trade-state processing, preserving an existing position.
- OpenRouter routes must name an explicit `:free` model; paid routes are rejected at configuration load. No billing method is added by the application. Cerebras onboarding currently offers limited $5 trial credits, not unlimited recurring free usage. SambaNova's new-account console may require credits despite its published free-tier limits; API smoke testing determines usability.
- September 8 cloud inspection confirmed `trade-bot` active at `/opt/youtube-live-trade-monitor`, with Gemini daily-quota 429s still occurring at 12:31:56 UTC. This observation precedes deployment of this change.

Provider references: [Groq models/limits](https://console.groq.com/docs/rate-limits), [Cerebras model catalog](https://inference-docs.cerebras.ai/models/overview), [SambaNova limits](https://docs.sambanova.ai/docs/en/models/rate-limits), [OpenRouter free limits](https://openrouter.ai/pricing). Account-specific quota is authoritative; earlier fixed RPD claims have been removed.

Validation: `.venv/Scripts/python.exe -m pytest tests/test_llm_fallback.py tests/test_multimodal_verifier.py tests/test_config.py tests/test_main.py -q`. These cover ordered failover, cooldown recovery, daily quota, concurrent triggers, missing keys, malformed output and free-model configuration. Metadata preflight checks model access, not remaining generation quota or classification accuracy.

#### 2. Suppress Phantom EXIT Alerts While IDLE
- **Problem**: On September 7 at `04:30:46 UTC`, the bot dispatched `DISPATCHING TRADE CLOSED / EXIT ALERT for Mehak Trades` when the streamer said *"BTC closed. BTC closed. Closing. Just wait. Don't enter now"*. The streamer was discussing a candle close, not closing an open trade. The bot had no open position tracked.
- **Root Cause**: [`src/state_tracker.py`](file:///d:/projects/bot_agy/src/state_tracker.py) synthesizes an `ephemeral_trade` and returns `StateTransition.TRADE_CLOSED` even when `record.state == TradeState.IDLE`.
- **Required Fix**: If `record.state == TradeState.IDLE`, an `EXIT` signal must transition to `StateTransition.NONE` and never broadcast an exit alert.

#### 3. Filter Live Chat Viewer Acknowledgments & Streamer Banter
- **Problem**: Streamers frequently read viewer comments (e.g., *"My running trade is short from 18. Very good."*) or discuss educational hypotheticals (*"Now that gap will be filled on retest, pinpoint entry on retest"*), which Gemini verified as live entry signals.
- **Required Fix**: Strengthen the system prompt in [`src/multimodal_verifier.py`](file:///d:/projects/bot_agy/src/multimodal_verifier.py) to explicitly demand first-person, present-tense execution confirmation by the streamer, and penalize passive chat reading or conditional level setups.

#### 4. Compound Audio Trigger Gating
- **Problem**: Generic standalone words like `active`, `scalping`, `position`, `sell` trigger verification during casual conversation, burning API quotas (129 triggers in 3 hours).
- **Required Fix**: Require compound matching (e.g., action verb AND instrument/price level) or negative chat-filtering before invoking remote LLMs.

#### 5. Re-enable Lightweight Vision Verification
- **Problem**: `screenshots_enabled: false` forces the verifier to judge trade execution solely on audio, leading to direction hallucinations or false positives during ambiguous Hindi/English speech.
- **Required Fix**: Re-enable screenshot capture during audio triggers or verify MT5/TradingView position boxes with an efficient vision gate.

#### 6. Clean Streamlink Transport Shutdown at Stream End
- **Problem**: When a live stream terminates, Streamlink logs up to 6 repeated transport stalls and restarts over 90 seconds before the channel is declared offline.
- **Required Fix**: On transport timeout/EOF, immediately query channel liveness rather than looping through multiple restart cycles.

### Documentation contract

`AGENTS.md` is the repository instruction source for automated coding harnesses. Any code change that alters architecture, configuration, commands, deployment, externally visible behaviour, operational constraints, or test workflow must update this README in the same change. README is the current-source-of-truth overview; implementation plans and `scratch/` are historical or experimental material.

---

## Container & Google Cloud Deployment

### Docker Local Build & Run
```bash
# Build Docker image
docker build -t trade-monitoring-bot:latest .

# Run container with environment file
docker run -d \
  --name trade-bot \
  --env-file .env \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  trade-monitoring-bot:latest
```

### Google Cloud Deployment (GCE / Cloud Run Worker Pool)

#### Option A: Cloud Run Worker Pool (Alternative deployment)

This bot does continuous non-HTTP work. Deploy it as a Worker Pool, not as a Cloud Run service. Worker pools do not require a listening port and do not autoscale; keep one instance running for this bot. See the [Cloud Run worker-pool documentation](https://cloud.google.com/run/docs/deploy-worker-pools).

1. **Store Secrets in Google Secret Manager**:
   ```bash
   printf '%s' "$GROQ_API_KEY" | gcloud secrets create groq-api-key --data-file=-
   printf '%s' "$GEMINI_API_KEY" | gcloud secrets create gemini-api-key --data-file=-
   printf '%s' "$TELEGRAM_BOT_TOKEN" | gcloud secrets create telegram-bot-token --data-file=-
   printf '%s' "$TELEGRAM_CHAT_ID" | gcloud secrets create telegram-chat-id --data-file=-
   ```

2. **Create an Artifact Registry repository, then build the image**:
   ```bash
   export REGION=asia-south1
   export REPOSITORY=trade-bots
   gcloud artifacts repositories create "$REPOSITORY" --repository-format=docker --location="$REGION"
   gcloud builds submit --config cloudbuild.yaml \
     --substitutions=_REGION="$REGION",_REPOSITORY="$REPOSITORY"
   ```

   The build output prints the image URL tagged with its Cloud Build ID. Set it as `IMAGE_URL`, then deploy:

   ```bash
   gcloud run worker-pools deploy trade-monitoring-bot \
     --image "$IMAGE_URL" \
     --region "$REGION" \
     --instances 1 \
     --cpu 1 \
     --memory 1Gi \
     --set-secrets GROQ_API_KEY=groq-api-key:latest,GEMINI_API_KEY=gemini-api-key:latest,TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,TELEGRAM_CHAT_ID=telegram-chat-id:latest
   ```

   Pin numbered secret versions instead of `latest` once the initial deployment is stable. Worker pools are billed while instances run.

#### Option B: Compute Engine (current production deployment)

Production currently uses the `trade-bot` systemd unit at `/etc/systemd/system/trade-bot.service`, with working directory `/opt/youtube-live-trade-monitor` on `trade-bot-vm` in `us-central1-a`. Inspect the existing unit before deploying; the Docker instructions above are an alternative, not the current runtime. For a new container-based VM deployment, use a startup script or `cloud-init`. Do not use `gcloud compute instances create-with-container`; Google shut down its container startup agent on July 31, 2026. Follow the current [Compute Engine container migration guidance](https://cloud.google.com/compute/docs/containers/prepare-for-container-agent-shutdown).

---

## Security & Reliability Guardrails
- **Zero Credential Leakage**: API tokens and bot secrets are masked in logs and exception traces via `SecretRedactorFilter`.
- **Untrusted AI Output Validation**: Gemini Vision responses are parsed with deterministic fallback and validated via strict Pydantic schemas.
- **Fail-Safe De-bouncing**: Trade closures require repeated inactive verification, while a confirmed stream end also concludes the session.
- **Exponential Backoff**: Resilient retry boundaries protect against transient rate-limits (429) or temporary network glitches.
