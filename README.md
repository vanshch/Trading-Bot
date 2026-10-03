# XAU/USD Candle Close & Momentum Telegram Bot

A high-performance real-time Telegram bot designed to monitor Gold spot market data (`XAU/USD`) and deliver instant market momentum alerts, candle close notifications, dynamic price level tracking, and interactive status controls.

---

## 1. System Overview & Architecture

```
[Market Data Ingestion]
   ├── Primary: Twelve Data API (XAU/USD real-time time-series)
   │     └── Calibration: OANDA Offset (-$0.50) for 1:1 TradingView spot parity
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
  - **100% Invariant Pip Deltas & Candle Direction**: Because the offset is applied uniformly to all OHLC fields, candle color (green/red) and pip movements remain mathematically identical.
  - **Seamless Alignment**: You can mark levels directly on TradingView's OANDA chart and set alerts using the exact price you see on TradingView without mental math.

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
