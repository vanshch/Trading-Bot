# XAU/USD Candle Close & Momentum Telegram Bot

A high-performance real-time Telegram bot designed to monitor Gold spot market data (`XAU/USD`) and deliver instant market momentum alerts, candle close notifications, dynamic price level tracking, and interactive status controls.

---

## 1. System Overview & Architecture

```
[Market Data Ingestion]
   ├── Primary: Twelve Data API (XAU/USD real-time time-series)
   │     └── Approximate calibration: OANDA Offset (-$0.50)
   └── Fallback: Yahoo Finance API (GC=F Gold Futures)
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
          ├── Optional Browserless /function capture (remote Chromium)
          │   └── One OANDA TradingView photo per alerted candle, after text
          │
          ▼
[Telegram Interactive Bot API]
   ├── Command Handlers (/start, /alert, /alerts, /clearalerts, /candle, /status)
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
- **Invariant Pip Deltas & Candle Direction**: Because the offset is applied uniformly to all OHLC fields, candle color (green/red) and pip movements remain mathematically identical.
- **Approximate Calibration**: The fixed offset does not guarantee parity with OANDA. Twelve Data uses a composite feed and Yahoo fallback uses gold futures. Set levels with that difference in mind.

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
| `/status` | Shows system health, uptime, OANDA alignment offset, and data feed status |

---

## 4. Production Deployment & Runtime Specs

The bot runs 24/7 as a managed systemd service on Google Compute Engine.

- **Target VM**: `trade-bot-vm` (`us-central1-a`)
- **Systemd Service**: `xauusd-bot.service`
- **Deployment Directory**: `/opt/xauusd_telegram_bot`
- **User**: `bot_deployer`
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

## 5. Local Setup & Configuration

### Prerequisites
- Python 3.10+
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

## Optional TradingView OANDA feed

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

The [BTC audit](docs/tradingview-feed-audit-20261004.md) found 39/39 successful
history requests over approximately seven hours, all 35 due closes matching
independent Binance OHLCV, and both recovery suites passing. This qualifies the
observed BTC polling window; OANDA market-open/session reliability remains
unmeasured. Repository merge does not deploy or enable the provider on the VM.
Validate an isolated Python 3.11+ runtime, OANDA sessions and combined bot memory
before enabling it there. Yahoo fallback remains gold futures, which can differ
from spot; the Twelve Data offset is approximate and does not guarantee OANDA parity.

Offline adapter checks: `python -m unittest discover -s tests -p test_tradingview_feed.py`.
The preserved corrected Linux BTC harness and its eight regression checks are in
`tools/tradingview_soak/`; see its README for the original procedure and stop state.

## 6. Optional Browserless TradingView screenshots

After a triggered level rejection, false breakout, or fresh two-candle momentum alert,
the bot sends the existing text immediately. If enabled, it then sends **one shared
photo per alerted candle**, even when multiple alerts fire. It makes no Browserless
requests for quiet candles, startup greetings, or command responses. Captures have a
60-second overall deadline and photo delivery a 20-second deadline; failures leave
the text delivery intact. No retries are made, to bound latency and credit usage.

The bot only uses async HTTP. Chromium runs on Browserless, outside the VM's 120 MB
service budget. Decoded images are capped at 5 MB and encoded responses at about
7 MB; live VM memory remains to be
validated before deployment. Screenshots are OANDA visual context, may contain a
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

### Local device test before merging

Use the PR worktree and install `requirements.txt` into a Python virtual environment.
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

The one-shot test explicitly enables capture for that invocation, while the normal
bot remains disabled. Verify on the device that the test text arrives before the
chart, the saved drawings/indicators render, and the OANDA symbol/timeframe is correct.
Check the Browserless request log for units billed; the earlier handoff test took
10.099 seconds and 1 unit, but that does not guarantee a fixed cost per capture.
For live trigger validation, use a **separate test bot token** and enable
`CHART_SCREENSHOTS_ENABLED=true` in that test environment. Running a second polling
process with the production token would compete with the existing VM bot.

### Offline checks and deployment gate

```powershell
python -m py_compile bot.py alerts.py config.py feed.py screenshots.py test_chart_delivery.py
python -m unittest discover -s tests -p test_alerts.py
python -m unittest discover -s tests -p test_screenshots.py
python -m unittest discover -s tests -p test_chart_scheduler.py
```

CI runs the active XAU/USD suites on Python 3.10 and 3.12. Historical YouTube files
remain in GitHub for now; their tests are outside this bot's runtime and CI scope.
The PR records the existing local XAU/USD implementation in a baseline commit
because GitHub main previously contained only the YouTube monitor, then adds the
screenshot feature in a separate commit. Unrelated local deletions and experiments
are excluded.

Keep the PR draft until device testing passes. Merge and production deployment are
separate later steps. Any eventual deployment must include `screenshots.py` alongside
the four original modules and set credentials privately on the VM; never commit `.env`.
