# Project Skills & Capabilities (skills.md)

This document outlines the specialized skills, tools, domain knowledge, and diagnostic workflows required for operating and developing the **XAU/USD Candle Close & Momentum Telegram Bot**.

---

## 1. Market Data Feed Engineering & Verification
- **Dual-Feed Orchestration**:
  - Primary provider: Twelve Data REST API (`https://api.twelvedata.com/time_series?symbol=XAU/USD`).
  - Fallback provider: Yahoo Finance Chart API (`https://query1.finance.yahoo.com/v8/finance/chart/GC=F`).
- **Resampling & Timeframe Synchronization**:
  - Calculates target boundary timestamps for `5m`, `15m`, `1h`, and `4h` intervals.
  - Correctly categorizes historical completed candles vs. real-time forming candles using UTC timestamps and interval length offsets.
  - Accounts for market open/close cycles, weekend liquidity halts, and missing quotes.

## 2. Quantitative Alert Logic & Boundary Testing
- **Price Range Evaluation**:
  - Deduces price touch (`c_low <= target <= c_high`), breakout (`c_high >= target`), and breakdown (`c_low <= target`) within closed bar ranges.
  - Automatically purges triggered alerts from active state while maintaining untouched alerts.
- **Streak Guard Deduplication**:
  - Validates 2-consecutive green (`diff > 0`) or 2-consecutive red (`diff < 0`) momentum closes.
  - Inspects previous candle (`c0`) to guarantee that ongoing runs (3+ green/red bars) do not emit duplicate notifications.

## 3. Telegram Bot Architecture & UI Engineering
- **Asynchronous PTB (v20+) Integration**:
  - Command handling (`/start`, `/alert`, `/alerts`, `/clearalerts`, `/candle`, `/status`).
  - Inline keyboard menus with dynamic toggle labels (`ON 🟢` / `OFF ⚪`) and live counter badges.
  - Callback query navigation with state preservation.
- **Authorization & Security Enforcement**:
  - Guarding routes with `@authorized_only` to restrict bot functionality exclusively to `ALLOWED_USER_ID`.
  - Silent drop of unauthorized interaction attempts with internal security logging.

## 4. Production VM Administration & Diagnostics
- **Google Compute Engine (GCE) Management**:
  - Secure remote shell via `gcloud compute ssh` to `trade-bot-vm` in `us-central1-a`.
  - Windows execution protocol: Base64 payload encoding to prevent quote corruption.
- **Systemd Service Lifecycle**:
  - Service unit management (`xauusd-bot.service`).
  - Memory containment monitoring (`MemoryHigh=90M`, `MemoryMax=120M`).
  - Log analysis and journal streaming via `journalctl -u xauusd-bot`.
