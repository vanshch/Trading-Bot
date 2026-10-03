# Project progress

Last updated: 3 October 2026 (IST). Read README.md for architecture/commands; this file records current state and the next action.

## Production

- XAU/USD Telegram bot runs on `trade-bot-vm`, zone `us-central1-a`, as `xauusd-bot.service`; directory `/opt/xauusd_telegram_bot`.
- Still uses Twelve Data with the existing `-0.50` offset and Yahoo `GC=F` fallback. No chart feature or TradingView feed was integrated today.
- Last verified active when stopping the experiment: original PID `94302`, zero restarts. Production source hashes were unchanged. Service memory cap: 120 MB.
- Correction to older notes/README: the offset does **not** guarantee OANDA parity. Twelve Data is a composite feed; OANDA's feed/session differs. Yahoo fallback is gold futures, not spot.

## Completed today

- Sent a clearly labeled mock Telegram notification; Telegram accepted it. Phone receipt was not independently confirmed.
- Generated 15-minute chart previews outside the bot. Rendering needs a tighter price scale and roughly 10–15 visible candles. A tested Twelve Data 60-candle request used one API credit.
- Tested `lumduan/tvkit==0.18.0` first in `.experiments/tradingview-feed-probe-20261003`; it worked, so other candidates were not tested.
- Anonymous `OANDA:XAUUSD` fetches returned 60 candles each at 15 minutes and 1 hour. Server identified OANDA CFD; latest 15-minute OHLC matched the supplied TradingView screenshot exactly. Forced reconnect passed.
- Installed an isolated VM soak service, separate Python 3.12.15/venv, private storage and resource limits. VM preflight and five offline checks passed; initial RSS approximately 79 MB. System Python remains 3.10.12.
- At the user's request, stopped and disabled `tradingview-soak-20261003.service` for the weekend; heartbeat `tradingview-oanda-soak-follow-up` is **PAUSED**. Evidence is preserved. **No market-open soak hours completed; stability has not passed.**

## Next action

- User plans to run again Monday, 5 October. No automatic restart is scheduled. Archive the weekend state and start a fresh measurement window before re-enabling the experiment and follow-up; reusing old state would misclassify the intentional pause as a failure.
- Target: 48 market-open hours of 15-minute/hourly boundary polling; check gaps, late delivery, integrity, recovery and memory. Review results before discussing integration; keep production unchanged during testing.
- VM code: `/opt/tradingview-soak-20261003`; results: `/var/lib/tradingview-soak-20261003`. Full procedure/evidence pointers: [.experiments/tradingview-feed-probe-20261003/SOAK.md](.experiments/tradingview-feed-probe-20261003/SOAK.md).
- Future adapter must convert TradingView bar-start timestamps to the bot's closed-bar end timestamps, exclude forming bars, deduplicate replayed candles, and apply no Twelve Data offset to OANDA data. Verify Python compatibility and memory before deployment.
- Chart preview artifacts: `C:/Users/Lenovo/.codex/visualizations/2026/10/02/01a0fef5-933b-7d93-88a8-3ba4a0d64c30`.
- Browserless setup was reported in a side chat; its configuration was not accessible/verified here. Obtain that handoff before using it.

Legacy YouTube monitor is disabled; archives are in `archives/`. Deferred roadmap: live ticks, indicators, multiple assets and execution bridge.
