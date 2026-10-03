# AGENTS.md

## Session Initialization & Pre-Decision Rule (Mandatory)
1. Read `README.md` as the authoritative source of truth for the official project architecture, commands, and specifications.
2. Read `progress.md` for AI context on prior accomplishments, current work in progress, and future milestones.
3. **Check Before Taking Major Decisions**: Before making or proposing any significant code, architectural, or deployment changes, always check `progress.md` to verify active system state and prevent regressions.

## Tech Stack & Runtime
- **System**: XAU/USD Candle Close & Momentum Telegram Alert Bot.
- **Symbol & Asset**: Gold Spot (`XAU/USD`), Gold Futures fallback (`GC=F`).
- **Core Pipeline**: Python 3.10+, `python-telegram-bot` (v20+ async), `httpx` async HTTP client, Twelve Data API (time-series candles), Yahoo Finance fallback.
- **Production Host**: Google Compute Engine VM `trade-bot-vm` (`us-central1-a`, `e2-micro`).
- **Service Daemon**: `xauusd-bot.service` (managed under systemd, running under user `bot_deployer`).
- **Working Directory**: `/opt/xauusd_telegram_bot`.

## Commands

### Local Execution & Testing
- Run bot locally: `python bot.py`
- Syntax check: `python -m py_compile bot.py alerts.py config.py feed.py screenshots.py`

### Production VM Management
- Check service status: `systemctl status xauusd-bot.service`
- Check recent logs: `journalctl -u xauusd-bot -n 100 --no-pager`
- Follow live logs: `journalctl -u xauusd-bot -f`
- Restart service: `sudo systemctl restart xauusd-bot.service`
- Verify memory bounds: `systemctl status xauusd-bot.service | grep Memory`

### Production Code Deployment Flow
1. Verify syntax locally: `python -m py_compile bot.py alerts.py config.py feed.py screenshots.py`
2. Upload modified files to VM `/tmp`:
   `gcloud compute scp bot.py alerts.py config.py feed.py screenshots.py trade-bot-vm:/tmp/ --zone=us-central1-a --quiet`
3. Copy into `/opt/xauusd_telegram_bot` and restore ownership:
   `sudo cp /tmp/bot.py /tmp/alerts.py /tmp/config.py /tmp/feed.py /tmp/screenshots.py /opt/xauusd_telegram_bot/`
   `sudo chown bot_deployer:bot_deployer /opt/xauusd_telegram_bot/*.py`
4. Restart service and verify health:
   `sudo systemctl restart xauusd-bot.service`
   `systemctl is-active --quiet xauusd-bot.service && echo "DEPLOY_SUCCESS"`

## Core Boundaries & Invariants

- **Single-User Security Gate**:
  - The bot is restricted strictly to `ALLOWED_USER_ID`.
  - All incoming commands and inline button callbacks must pass through `@authorized_only`.
  - Unauthorized requests must be logged as warnings and rejected silently without responding.

- **Data Integrity & Fallback**:
  - Primary market data is sourced from Twelve Data. If Twelve Data returns non-200, rate limits, or empty sets, gracefully fallback to Yahoo Finance.
  - Never crash the candle scheduler loop on transient API timeouts or HTTP 5xx errors; log errors and wait for the next interval boundary.

- **Streak Guard Deduplication**:
  - 2-green (`two_green_candles`) and 2-red (`two_red_candles`) alerts must only fire on a **fresh sequence**.
  - A third, fourth, or subsequent candle matching the streak must NOT re-trigger the alert.
  - Deduplicate by candle timestamp identifier (`candle_id`).

- **Atomic State Persistence**:
  - All dynamic alerts and settings modifications must be committed to `data/alerts.json` using atomic temporary file write and replacement (`.tmp` -> replace).
  - Corrupt or missing JSON files must safely recover to defaults without halting the bot process.

- **Telegram Formatting & Markdown Safety**:
  - Dynamic user text and prices must respect Telegram Markdown entity limits.
  - Keep messages clean, concise, and mobile-friendly with monospace price values (`$2,735.50`).

- **Remote Execution on Windows**:
  - When issuing commands to the GCE VM via `gcloud compute ssh`, Base64-encode command strings to prevent shell escaping or argument stripping issues.
