# XAU/USD Candle Close & Momentum Telegram Bot

An asynchronous, single-user Telegram bot for gold candle alerts, price-level
rejections, fresh two-candle momentum signals and on-demand charts.

The active entrypoint is `python bot.py`. GitHub main owns the reviewed source;
[progress.md](progress.md) records integration, the last verified production
revision, unresolved issues and rollback. Repository defaults and deployed
settings are documented separately below.

## Current repository

- `bot.py`: authorized Telegram commands/buttons, candle scheduler and dispatch.
- `alerts.py`: level/momentum settings and atomic `data/alerts.json` persistence.
- `config.py`: environment configuration, timeframe and in-memory scheduler state.
- `feed.py`: provider orchestration and candle normalization.
- `tradingview_feed.py`: optional verified raw `OANDA:XAUUSD` history through tvkit.
- `screenshots.py`: optional remote Browserless TradingView captures.
- `manual_charts.py`: background manual-chart requests with shared busy protection.
- `hourly_chart.py`: Pillow renderer for 20 verified OANDA hourly candles/tick volume.
- `hourly_volume.py`: opt-in one-shot closed-H1 OANDA tick-volume alarm.
- `tests/`: six active offline suites; `.github/workflows/xauusd-checks.yml` runs CI.
- `tools/preview_hourly_chart.py` and `test_chart_delivery.py`: explicit live capture
  tools. `tools/tradingview_soak/` preserves the Linux offline harness and procedure.
- `docs/` and `artifacts/reviews/`: historical handoffs, deployment and review evidence.

The retired YouTube/audio/LLM monitor, its tests/plans, old Dockerfile and Cloud
Build recipe are removed from the tracked current tree. Recover them from
[revision 7c50d4b](https://github.com/vanshch/Trading-Bot/tree/7c50d4bf2573659d90568cbb3dc617a6d849e7b4).
Ignored local copies may remain. `.env`, alert state, logs, experiment databases,
virtual environments and generated chart images are local and excluded from Git.
Historical Markdown and substantive review reports remain tracked.

## Setup

Base runtime: Python **3.10+**. Optional TradingView/tvkit requires **3.11+**.
Dependencies are `python-telegram-bot`, `httpx`, `python-dotenv` and Pillow;
`requirements-tradingview.txt` additionally pins `tvkit==0.18.0`.

```sh
git clone https://github.com/vanshch/Trading-Bot.git
cd Trading-Bot
python -m venv .venv
```

Activate with `.venv\Scripts\Activate.ps1` in PowerShell or
`source .venv/bin/activate` on Linux/macOS, then:

```sh
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` (`Copy-Item .env.example .env` in PowerShell or
`cp .env.example .env` on Linux/macOS). Fill `TELEGRAM_BOT_TOKEN` and numeric
`ALLOWED_USER_ID`. Configure the desired providers:

```ini
TWELVE_DATA_API_KEY=your_private_key
DEFAULT_TIMEFRAME=15m
LOG_LEVEL=INFO
OANDA_PRICE_OFFSET=-0.50
TRADINGVIEW_FEED_ENABLED=false
CHART_SCREENSHOTS_ENABLED=false
```

Both optional automatic features default **off**. To enable verified TradingView
history, install `python -m pip install -r requirements-tradingview.txt` on Python
3.11+ and set `TRADINGVIEW_FEED_ENABLED=true`.

Browserless captures require private `BROWSERLESS_API_TOKEN`,
`BROWSERLESS_ENDPOINT`, `BROWSERLESS_PROFILE` and `TRADINGVIEW_CHART_URL` settings
from `.env.example`. Use your authenticated TradingView profile and saved layout
for drawings/indicators; a generic chart URL does not promise layout restoration.
Set `CHART_SCREENSHOTS_ENABLED=true` for automatic alert photos. Explicit chart
commands are independent of those automatic flags but still require their
provider dependencies/credentials.

Start with `python bot.py`. Use a separate test bot token for local polling when
the production bot is running; a second poller with the production token competes
for Telegram updates.

## Commands and alert behavior

All commands and callbacks accept only `ALLOWED_USER_ID`; other users are silently
rejected. `/start` opens the inline control panel.

- `/alert 4318 resistance` or `/alert 4280 support`: set a rejection level.
  `/alert 4318` selects the side using current price.
- `/alert 2green` and `/alert 2red`: toggle one-shot momentum alerts. They fire on
  a fresh two-candle sequence and switch off; third/later candles of the same
  streak do not trigger another signal.
- `/alerts`: inspect/cancel price alerts; `/clearalerts`: clear pending price alerts.
- `/candle`: inspect the forming and latest closed bar on demand.
- `/status`: inspect bot uptime/settings/feed information.
- `/screenshot`: request the saved TradingView layout at the active alert timeframe.
- `/chart1h`: render exactly 20 native OANDA hourly candles with tick volume.
- `/volume1h` or **🔊 1H Volume ≥120% MA20**: arm/cancel the hourly volume alarm.
  It sends one text alert when a completed H1 candle has OANDA tick volume
  **at least 1.20 × the mean of the preceding 20 completed H1 candles**.
  The evaluated candle is excluded; small grazes are ignored. See
  [progress](progress.md) for testing/deployment status.

A wick rejection requires the wick to touch/pierce a level while the candle body
stays on the unbroken side. A body breakout waits for the immediate next candle:
an opposite-color close back across the level confirms a false breakout;
otherwise the level is invalidated silently. See `alerts.py` and its tests for
boundary conditions.

Alert evaluation uses completed candles, excludes forming bars, seeds its startup
processed timestamp from history and rejects equal/older closes across replay or
fallback. Settings are atomically replaced in `data/alerts.json` and recover to
safe defaults if missing/corrupt. Supported configured timeframes are 5m, 15m, 1h
and 4h; default/recorded production monitoring is 15m. The active menu has no
interactive timeframe selector. H1 charts do not enable a second alert scheduler.

The volume alarm has its own hourly check (UTC hour boundary plus 10 seconds),
without changing price/momentum monitoring. It fetches only while armed and
requires optional tvkit/Python 3.11+, independently of `TRADINGVIEW_FEED_ENABLED`,
as an explicit user request. Only verified OANDA tick volume is accepted; no
Twelve Data/Yahoo volume is substituted. It needs 21 closed bars, skips forming
bars and closes before arming/restart, and persists its one-shot state atomically.
The baseline is 20 **trading bars**, allowing session/weekend gaps, and differs
from an SMA that includes the evaluated bar. Unavailable data leaves the alarm
armed for the next hourly check. No intrabar alert, catch-up/backfill, photo or
durable send retry is implemented. Like momentum alerts, its one-shot state is
consumed before sending. See the [isolated test procedure and validation limits](docs/hourly-volume-validation-20261020.md).

## Data and chart contracts

With TradingView enabled, the order is verified raw **OANDA:XAUUSD**, then Twelve
Data **XAU/USD**, then Yahoo **GC=F**. With it disabled, start at Twelve Data;
a missing Twelve Data key skips that provider. The approximate `-0.50` OHLC
offset applies to Twelve Data only and does not guarantee OANDA parity. Yahoo is
gold futures and can differ from spot. Optional-provider failures fall through.

The OANDA adapter validates identity/OHLCV, preserves native session boundaries,
requests 60 history bars and uses a bounded 25-second deadline. Automatic alerts
send text first and then at most one Browserless photo per alerted candle.
Quiet candles do not capture charts. Capture/photo failures leave text intact;
there is no photo retry. Chromium runs remotely. Captures have a 60-second bound,
photo delivery 20 seconds, decoded PNGs a 5 MB limit and encoded responses an
approximately 7 MB bound. Images can show a forming candle and are visual context.

Manual screenshots/H1 charts run as background tasks with **one shared in-flight
reservation**. Current source has no timed cooldown: another request can start
after delivery/failure. H1 uses verified OANDA data without fallback volume;
insufficient history is rejected. Its forming bar is labeled, and OANDA volume
means tick counts, not exchange-traded contracts. H1 renders locally with Pillow;
it does not reproduce saved TradingView drawings or indicators.

Manual chart preparation notices run in the background and cannot prevent a
capture if Telegram is unavailable. Informational notices have two five-second
send attempts, separated by one second. Unavailable verified hourly history gets
one retry within a 55-second capture budget. Invalid chart data is rejected;
uncertain photo sends are not retried. Error messages distinguish provider
unavailability from unconfirmed photo delivery. Expired/transiently failed button
acknowledgments allow the authorized action to continue; unrelated errors reach
the registered, credential-safe Telegram error handler. PTB retains its own
receive-polling recovery; these changes do not guarantee external connectivity.

## Validation

```sh
python -m py_compile bot.py alerts.py config.py feed.py tradingview_feed.py screenshots.py hourly_chart.py manual_charts.py hourly_volume.py test_chart_delivery.py tools/preview_hourly_chart.py
python -m unittest discover -s tests -p test_alerts.py
python -m unittest discover -s tests -p test_screenshots.py
python -m unittest discover -s tests -p test_chart_scheduler.py
python -m unittest discover -s tests -p test_tradingview_feed.py
python -m unittest discover -s tests -p test_manual_charts.py
python -m unittest discover -s tests -p test_hourly_volume.py
```

These six suites contain 79 offline tests. CI runs Python 3.10/3.12, plus an
optional-provider dependency job and eight Linux harness checks. On Linux/WSL:

```sh
python -m unittest discover -s tools/tradingview_soak -p test_soak.py
```

The harness uses Linux's `resource` module. Use the named test patterns locally
because ignored legacy tests may still exist on disk. A fresh Git clone can run
`python -m unittest discover -s tests` for all active tests. Development tools are
in `requirements-dev.txt`; they are optional for the unittest suites.

Explicit live tools consume provider resources and are outside offline checks:

```sh
python test_chart_delivery.py --timeframe 15m
python test_chart_delivery.py --send --timeframe 15m
```

The first saves a Browserless image; `--send` sends labeled test text/photo without
polling. Confirm receipt on the device separately. For a local H1 PNG, set
`TESTING=1` in your shell and run:

```sh
python tools/preview_hourly_chart.py --data-output .chart-artifacts/hourly-candles.json
```

That command still makes a live OANDA request and requires optional dependencies.

## Production and known limits

Recorded deployment is GCE `trade-bot-vm`, systemd `xauusd-bot.service`, user
`bot_deployer`, directory `/opt/xauusd_telegram_bot`; Python **3.12.15**.
The service has `MemoryHigh=90M`, `MemoryMax=120M`. Pillow **12.3.0** is supplied
through a service PYTHONPATH overlay, which direct maintenance commands must use.

**Last recorded production verification: 20 October 2026, 03:40:52 UTC.**
PR #11 is deployed, including resilient callback acknowledgments, background
chart notices and bounded hourly-history retries. All nine modules/two manifests
matched merged release `0861d91`; saved alerts/environment were preserved.
Desktop Computer Use verified **current → tele_alert**: `/start`, Status,
Live & Closed Bar, H1 chart and Screenshot Now. WARP remains stopped/disabled;
do not re-enable it without user instruction. Package/configuration remain for
rollback. See [release/desktop/rollback evidence](docs/telegram-resilience-deployment-20261020.md)
and [WARP maintenance](docs/warp-disabled-validation-20261020.md).

TradingView/automatic photos remain enabled, monitoring stays 15m. The independent
H1 alarm defaults OFF; the user's current setting is ON (see progress). Desktop
success does not establish separate-phone receipt, live qualifying H1 delivery or
sustained-load reliability. Documentation commits are not runtime observations.

Polling errors, long-run memory pressure, durable delivery-event persistence/retry and a
continuous OANDA market-session soak remain open. One-shot state is consumed
before sending and there is no durable outbox. A recurring market-hours gate is not implemented. Live qualifying H1 alert
delivery/device receipt remains pending user testing. Experiments remain
paused. Prior BTC/historical-candle checks do not qualify future live gold delivery.

## Contributor and historical documentation

Read [AGENTS.md](AGENTS.md) before work. [skills.md](skills.md) maps workflows;
[experience.md](experience.md) records durable lessons; [progress.md](progress.md)
owns dated operational/integration state. Review all five after substantial work
and PR closeout. [Progress history](docs/progress-history/README.md),
[legacy references](docs/legacy/README.md) and [review artifacts](artifacts/reviews/)
are historical evidence, loaded on demand. Absolute local/VM evidence paths in
those documents are machine-local and are not files available from GitHub.
