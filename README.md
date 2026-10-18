# XAU/USD Candle Close & Momentum Telegram Bot

Current production (13 October 2026): PRs #1–#4 are merged and deployed on
`trade-bot-vm`, including **Screenshot Now** and **1H Chart + Volume** buttons.
The Python 3.12.15 service uses verified TradingView OANDA history first, falls
back to Twelve Data/Yahoo, and sends one Browserless chart after triggered text.
The 15m alert schedule and existing alert state are preserved. Target-runtime
memory preflight and service/source checks passed; the user is testing manual
Telegram photo delivery. Polling errors, memory pressure and durable delivery
retries remain operational follow-ups; see `progress.md`.
Read `progress.md` for exact deployed commit, evidence, runtime override and rollback.
GitHub main is the authoritative merged source. The primary local checkout is
`D:/projects/bot_agy`; use its reviewed revision for future work. See progress for
checkout reconciliation evidence and changes that have not been deployed.

A high-performance real-time Telegram bot designed to monitor Gold spot market data (`XAU/USD`) and deliver instant market momentum alerts, candle close notifications, dynamic price level tracking, and interactive status controls.

---

## 1. System Overview & Architecture

```
[Market Data Ingestion]
   ├── Primary: TradingView OANDA:XAUUSD (raw OHLC, verified identity)
   ├── Fallback: Twelve Data API (XAU/USD time-series, approximate -$0.50 offset)
   └── Final fallback: Yahoo Finance API (GC=F Gold Futures)
          │
          ▼
[Feed Normalizer & Candle Engine]
   ├── Multi-Timeframe Resampling: 5m, 15m (Default), 1h, 4h
   ├── Forming Bar Calculation (Open, High, Low, Close, Pips, Emoji)
   └── Completed Bar Confirmation & Boundary Synchronization
          │
          ▼
[Alert Evaluation Engine]
   ├── Level Rejection & False Breakout (Sweep) Alerts
   ├── Momentum Streaks (2 Consecutive Green / 2 Consecutive Red)
   │     └── Streak Guard & One-Shot: Alerts strictly on sequence formation
   └── Event-Driven Dispatch: Zero spam, alerts only on triggers
          │
          ▼
[Telegram Interactive Bot API]
   ├── Command Handlers (/start, /alert, /alerts, /clearalerts, /candle, /status, /screenshot, /chart1h)
   ├── Inline Keyboard GUI (Live inspect, momentum toggles, price alert manager)
   └── Single-User Authorization Gate (ALLOWED_USER_ID)
```

---

## 2. Core Capabilities & Alert Types

### 1. Level Rejection & False Breakout (Sweep) Alerts
- **How to Set**:
  - Auto-side: `/alert <price>` (e.g. `/alert 2740.00` — sets resistance if above current price, support if below)
  - Explicit side: `/alert 2740.00 resistance` or `/alert 2710.00 support`
- **Evaluation Rules**:
  - **Wick Touch (Clean Rejection)**: If a candle's wick tests or pierces the level while the candle body closes strictly inside, an immediate rejection notification is dispatched.
  - **Body Breakout + Immediate Opposite Rejection**: If a candle breaks the level with its body (e.g. Green candle closing above resistance), the alert enters `PENDING_CONFIRMATION` awaiting the immediate next candle:
    - If the next candle is of the **opposite color** (e.g. Red) **AND** closes back outside the price zone (below resistance) ➔ Alert is dispatched (*Confirmed Rejection / Fakeout*).
    - If the next candle does not close in the opposite direction outside the zone ➔ Zone is marked **Breached / Invalidated** and the alert is cancelled silently without notifying.

### 2. Candle Momentum Alerts (One-Shot & Streak Guard)
- **2-Green Momentum (`two_green_candles`)**: Triggers when two consecutive bullish (green) candles close.
  - Command: `/alert 2green` (or tap `🟢🟢 2-Green` on the keyboard)
  - **One-Shot Auto-Reset**: Automatically switches to `OFF` after firing one notification.
- **2-Red Momentum (`two_red_candles`)**: Triggers when two consecutive bearish (red) candles close.
  - Command: `/alert 2red` (or tap `🔴🔴 2-Red` on the keyboard)
  - **One-Shot Auto-Reset**: Automatically switches to `OFF` after firing one notification.
- **Streak Guard Deduplication**: Only fires on a **fresh** 2-candle sequence. If a third or fourth consecutive candle of the same color closes, the alert is suppressed to prevent notification spam.

### 3. Live Forming & Closed Bar Inspector (On-Demand)
- Command: `/candle` (or tap `📊 Live & Closed Bar`)
- Inspects the currently active forming bar in real time (open, high, low, current close, pip delta, elapsed start time) alongside the previous closed bar. Non-spammy, on-demand query.

---

## 3. OANDA Price Calibration Factor

- **Background & Empirical Analysis**: Twelve Data sources an institutional composite mid-price feed across 60+ Tier-1 European banks. Compared against retail OTC spot quotes on TradingView (`OANDA:XAUUSD`), Twelve Data sits consistently **+$0.50 to +$0.60 higher** (mean signed bias of **+$0.58**, ~5.8 pips).
- **Corrective Calibration (`OANDA_PRICE_OFFSET = -0.50`)**:
  - Configurable via `OANDA_PRICE_OFFSET` in `config.py` / `.env` (default `-0.50`).
  - Automatically applied to Twelve Data's `open`, `high`, `low`, and `close`.
  - **100% Invariant Pip Deltas & Candle Direction**: Because the offset is applied uniformly to all OHLC fields, candle color (green/red) and pip movements remain mathematically identical.
- **Scope**: This approximate offset applies only to Twelve Data fallback, never raw OANDA prices. It does not guarantee parity with OANDA, and Yahoo futures can differ from spot.

---

## 4. Bot Commands & Interactive Keyboard

| Command | Description |
| :--- | :--- |
| `/start` | Launches the interactive control panel with inline keyboard |
| `/alert <price>` | Sets a level rejection alert (auto-detects resistance/support) |
| `/alert <price> resistance` | Sets an upper resistance rejection alert |
| `/alert <price> support` | Sets a lower support rejection alert |
| `/alert 2green` | Toggles the 2 consecutive green candles momentum alert (one-shot) |
| `/alert 2red` | Toggles the 2 consecutive red candles momentum alert (one-shot) |
| `/alerts` | Lists all active price alerts with one-tap cancel buttons |
| `/clearalerts` | Clears all pending price alerts immediately |
| `/candle` | Displays the current live forming bar and latest closed bar |
| `/screenshot` | Captures the saved TradingView layout immediately at the active alert timeframe |
| `/chart1h` | Renders the latest 20 verified OANDA hourly candles with tick volume |
| `/status` | Shows system health, uptime, OANDA alignment offset, and data feed status |

---

## 5. Production Deployment & Runtime Specs

The bot normally runs 24/7 as a managed systemd service on Google Compute Engine.
The user-requested pause ended on **5 October 2026, 03:30:01 IST**; see
`progress.md` for the verified restart, latest observation and remaining risks. There is no recurring
market-hours pause in the application yet.

- **Target VM**: `trade-bot-vm` (`us-central1-a`)
- **Systemd Service**: `xauusd-bot.service`
- **Deployment Directory**: `/opt/xauusd_telegram_bot`
- **User**: `bot_deployer`
- **Runtime**: dedicated Python 3.12.15; `/opt/xauusd_telegram_bot/venv-20261004/bin/python`
- **Enabled features**: TradingView OANDA primary and Browserless chart enrichment; both production flags are `true`.
- **Manual chart dependency**: Pillow 12.3.0 in `chart-deps-20261013-044810`, loaded through the systemd `30-chart-deps-20261013.conf` PYTHONPATH drop-in. Direct Python maintenance commands need the same environment.
- **Resource Constraints**: `MemoryHigh=90M`, `MemoryMax=120M` (Strict RAM containment)
- **State Persistence**: `data/alerts.json` (Atomically saved via temporary file replacement)

### Useful Operational Commands on VM
```bash
# Service status
systemctl status xauusd-bot.service

# Live follow logs
journalctl -u xauusd-bot.service -f

# Recent logs
journalctl -u xauusd-bot.service -n 50 --no-pager

# Restart service
sudo systemctl restart xauusd-bot.service
```

---

## 6. Local Setup & Configuration

Use the primary checkout or a clean feature worktree based on reviewed main.
Before editing, check `git status --short --branch`, `git worktree list`, and
`git log -1 --oneline`; preserve unrelated work before switching or updating.
Recovery details for the former primary state are in
[checkout reconciliation](artifacts/reviews/checkout-reconciliation-20261018.md).

### Prerequisites
- Python 3.10+ for base operation; Python 3.11+ for optional TradingView/tvkit (production: 3.12.15).
- Telegram Bot Token (from `@BotFather`)
- Twelve Data API Key

### Installation
```bash
git clone <repo-url>
cd bot_agy
python -m venv venv
source venv/bin/activate  # On Windows: .\venv\Scripts\activate
pip install -r requirements.txt
```

### Environment Variables (`.env`)
```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USER_ID=your_numeric_telegram_user_id
TWELVE_DATA_API_KEY=your_twelve_data_api_key
DEFAULT_TIMEFRAME=15m
LOG_LEVEL=INFO
```

### Run Locally
```bash
python bot.py
```

## 7. Optional TradingView OANDA feed

`TRADINGVIEW_FEED_ENABLED=false` is the default. Enabling it tries anonymous
`OANDA:XAUUSD` history through `tvkit==0.18.0` before Twelve Data and Yahoo.
Use a **Python 3.11+ virtual environment** and install
`pip install -r requirements-tradingview.txt`, then set
`TRADINGVIEW_FEED_ENABLED=true` privately in `.env`.
The base requirements and disabled configuration continue to support Python 3.10.
If the optional package/runtime is unavailable, requests fail or time out, or source
identity/OHLCV validation fails, the bot uses the existing fallback providers.

The adapter supports 5m, 15m, 1h and 4h, requests 60 bars per history fetch and has
a 25-second request deadline with bounded reconnect attempts. It verifies the
server identifies `OANDA:XAUUSD` from OANDA, uses raw OHLC prices without the
Twelve Data offset, converts bar-start timestamps to closed-bar end timestamps,
and excludes forming bars from closed history. The scheduler suppresses equal
or older candles when a provider replays history or fallback returns older data.
OANDA session starts are preserved rather than forced to UTC epoch boundaries.
At startup, the latest available closed candle seeds the scheduler's processed
timestamp, so restarting the service does not replay an old close against saved
alerts. Subsequent new closes continue through normal alert evaluation.

The [BTC audit](docs/tradingview-feed-audit-20261004.md) found 39/39 successful
history requests over approximately seven hours, all 35 due closes matching
independent Binance OHLCV, and both recovery suites passing. This qualifies the
observed BTC polling window; The 8 October retrospective audit verified four expected OANDA alerts;
a continuously instrumented market-session soak remains outstanding. The merged provider is recorded as deployed and enabled on the VM with Python 3.12.15; see progress for source hashes, memory preflight, the historical production pause and verified resume, and remaining market-open validation. Future runtime/provider changes still require isolated preflight. Yahoo fallback remains gold futures, which can differ
from spot; the Twelve Data offset is approximate and does not guarantee OANDA parity.

Offline adapter checks: `python -m unittest discover -s tests -p test_tradingview_feed.py`.
The preserved corrected Linux BTC harness and its eight regression checks are in
`tools/tradingview_soak/`; see its README for the original procedure and stop state.

## 8. Optional Browserless TradingView screenshots

After a triggered level rejection, false breakout, or fresh two-candle momentum alert,
the bot sends the existing text immediately. If enabled, it then sends **one shared
photo per alerted candle**, even when multiple alerts fire. It makes no Browserless
requests for quiet candles or startup greetings. Explicit `/screenshot` requests also capture charts. Captures have a
60-second overall deadline and photo delivery a 20-second deadline; failures leave
the text delivery intact. No retries are made, to bound latency and credit usage.

The bot only uses async HTTP. Chromium runs on Browserless, outside the VM's 120 MB
service budget. Decoded images are capped at 5 MB and encoded responses at about
7 MB; recorded deployment preflight passed combined memory checks under the VM limits (see progress); long gold-session behavior remains unverified. Screenshots are OANDA visual context, may contain a
forming candle, and can differ from the feed that triggered the alert. They do not
replace the market-data pipeline. Existing candle deduplication and one-shot settings
remain in control of triggers.

The provider uses the documented [Function API](https://docs.browserless.io/rest-apis/function)
and [authenticated profiles](https://docs.browserless.io/baas/features/authenticated-profiles).
It requires a restored TradingView login, the OANDA gold chart header at the selected
timeframe, and a rendered canvas before returning a PNG. TradingView UI changes or
an expired login cause the optional photo to be skipped. API tokens use an
Authorization header and are excluded from error logs.

Live validation on 3 October passed for 5m, 15m, 1h and 4h using the saved profile
and layout; drawings and indicators were visually checked. Telegram accepted the
labeled 15-minute test text followed by the photo, and the user confirmed device
receipt and chart correctness. The hosted function sandbox lacks Node's `Buffer`,
so the provider returns bounded base64 JSON and Python decodes it before PNG
validation. HTTPX request logs are suppressed because Telegram URLs contain bot
credentials.

### Explicit local capture/device test

Use the complete merged checkout and install `requirements.txt` into a Python virtual environment.
Put the existing Telegram credentials and these settings into the ignored `.env`:

```ini
CHART_SCREENSHOTS_ENABLED=false
BROWSERLESS_API_TOKEN=your_private_browserless_token
BROWSERLESS_ENDPOINT=https://production-sfo.browserless.io
BROWSERLESS_PROFILE=tradingview-test
TRADINGVIEW_CHART_URL=https://www.tradingview.com/chart/your_saved_layout/
```

Use the saved layout URL from the verified Browserless handoff to preserve drawings
and indicators. Requests override its symbol with `OANDA:XAUUSD` and select the bot's
timeframe (5m/15m/1h/4h). Defaults use the generic chart URL, which does not promise
saved layout restoration.

```powershell
# Capture only; uses one Browserless request, does not contact Telegram.
python test_chart_delivery.py --timeframe 15m

# Send a clearly labeled test and chart to ALLOWED_USER_ID, without polling.
python test_chart_delivery.py --send --timeframe 15m
```

The one-shot test explicitly enables capture for that invocation, while normal capture follows its environment flag (off by default locally, recorded as enabled in production). Verify on the device that the test text arrives before the
chart, the saved drawings/indicators render, and the OANDA symbol/timeframe is correct.
Check the Browserless request log for units billed; the earlier handoff test took
10.099 seconds and 1 unit, but that does not guarantee a fixed cost per capture.
For live trigger validation, use a **separate test bot token** and enable
`CHART_SCREENSHOTS_ENABLED=true` in that test environment. Running a second polling
process with the production token would compete with the existing VM bot.

### Offline checks and recorded integration

```powershell
python -m py_compile bot.py alerts.py config.py feed.py tradingview_feed.py screenshots.py hourly_chart.py manual_charts.py test_chart_delivery.py tools/preview_hourly_chart.py
python -m unittest discover -s tests -p test_alerts.py
python -m unittest discover -s tests -p test_screenshots.py
python -m unittest discover -s tests -p test_chart_scheduler.py
python -m unittest discover -s tests -p test_tradingview_feed.py
python -m unittest discover -s tests -p test_manual_charts.py
```

CI runs the active XAU/USD suites on Python 3.10 and 3.12. Historical YouTube files
remain in GitHub for now; their tests are outside this bot's runtime and CI scope.
PRs #1–#4 are merged; deployed source revision is `05527f7857219432074deb5a6377e0b0009322e0`. Deploy all eight runtime modules (`bot`, `alerts`, `config`, `feed`, `screenshots`, `tradingview_feed`, `hourly_chart`, `manual_charts`) and matching dependencies together. Repository automatic-feature defaults remain off; production flags are on. Private credentials and current alert state were preserved. See progress and [13 October deployment](docs/manual-charts-deployment-20261013.md) for actual verification, dependency overlay, rollback, historical pause and remaining manual/device and gold-session limits. Do not start production or paused experiments as part of documentation maintenance.

## 9. On-demand screenshots and hourly volume charts

The main control panel includes **📸 Screenshot Now** and **🕐 1H Chart + Volume**.
`/screenshot` uses the configured Browserless saved layout and active alert timeframe
(production currently 15m). Capture begins on request; completion still takes the
provider's rendering time. It requires existing Browserless credentials/profile.

`/chart1h` fetches verified raw `OANDA:XAUUSD` through tvkit at interval `60` and
renders exactly the latest **20** hourly candles, including the current forming
bar when returned. It uses a local Pillow PNG renderer with a dark background,
teal/red candles, wicks, dotted grid, right price scale, latest OHLC/change,
current-price badge and matching volume bars. Labels use UTC and preserve native
bar starts; market closures are compressed. **Volume is OANDA tick counts**, not
exchange-traded contracts. The image is a snapshot, with no saved drawings,
invented bid/ask prices, TradingView branding or live countdown.

These explicit commands work independently of automatic-alert feature flags.
The hourly command needs Python 3.11+ and `requirements-tradingview.txt`; there is
no Twelve Data/Yahoo substitution for this OANDA chart. Short/invalid history or
provider failures produce an availability message. Pillow (`requirements.txt`)
keeps rendering small and runs it in a worker thread; no local Chromium is used.
Captures run as application background tasks so bot commands remain responsive.
Only one manual request can run at once. Another request can start immediately
after delivery or failure, with no timed cooldown between either button or command.
Authorization remains limited to `ALLOWED_USER_ID`. See progress for deployment status.

This adds on-demand hourly visualization only. The existing 15m scheduler,
processed-candle state, alert settings and text-first automatic photos are unchanged.

Offline checks: `python -m unittest discover -s tests -p test_manual_charts.py`.
Capture a local image and optional candle evidence without polling or Telegram:

```powershell
$env:TESTING='1' # allows this read-only tool without Telegram credentials
python tools/preview_hourly_chart.py --data-output .chart-artifacts/hourly-candles.json
```

Deployments of this feature also require `hourly_chart.py`, `manual_charts.py` and
the updated dependency manifest. Check `progress.md` for integration/deployment
status and Linux memory preflight limits before enabling it in production.

## 10. Contributor documentation and closeout

Follow [AGENTS.md](AGENTS.md) for agent/contributor instructions. After substantial work and whenever a PR is merged or closed, review and update affected parts of this README, AGENTS, [skills.md](skills.md), [experience.md](experience.md), and [progress.md](progress.md). Keep architecture/commands here, contributor boundaries in AGENTS, workflow routing in skills, durable lessons in experience, and dated verification/next actions in progress. Distinguish a closed, unmerged PR from shipped work; merging does not itself establish deployment or validation. Worktrees do not synchronize edits automatically: carry the reviewed documentation changes through integration with their code. Historical references are loaded on demand.
