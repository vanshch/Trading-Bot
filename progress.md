# Project progress

Last updated: 4 October 2026 (IST). Read README.md for architecture/commands; this file records current state and the next action.

## Production rollout preparation — 4 October 2026

- User authorized deploying/enabling both merged PRs for Monday, 5 October. VM inspection confirmed neither feature was present: original Python 3.10 bot and absent screenshots/TradingView modules. Staged latest main with both flags true and the verified Browserless settings, while preserving production state.
- Dedicated Python 3.12.15 runtime/venv prepared. VM's 34 offline tests and compilation passed. Isolated preflight under unchanged 90 MB high/120 MB max limits returned verified OANDA 15m history, validated Telegram authentication and captured the saved chart; sampled combined RSS peaked at 80.53 MB. No extra Telegram test photo was sent.
- Startup now seeds the last processed candle from the existing initialization fetch. This prevents a weekend restart from evaluating Friday's close against preserved price alerts; two regression checks cover available/missing startup history. Market-open behavior is still pending real Monday candles.

## Authorized TradingView feed repository integration

- After the audit, user explicitly requested creating and merging the PR. This supersedes the earlier conditional decision to withhold the merge. Merge does not imply completed OANDA/session qualification or VM deployment.
- Optional `TRADINGVIEW_FEED_ENABLED=false` provider adapter uses verified anonymous OANDA:XAUUSD, raw prices, correct closed-bar end timestamps, forming-bar separation, bounded requests and existing Twelve Data/Yahoo fallback. Base Python 3.10 operation stays supported; enabling tvkit requires a Python 3.11+ runtime and separate optional requirements.
- Scheduler rejects equal/older candles across provider replay/fallback. Price-offset messages now label the calibration as Twelve Data only. Corrected Linux BTC harness, eight regressions and sanitized [audit](docs/tradingview-feed-audit-20261004.md) are committed with the integration. Original local/VM evidence is preserved.
- Smoke tests and follow-ups remain stopped. Production activation/runtime migration, combined memory and OANDA market-open behavior remain separate verification steps.
- Local validation: 34 active offline tests and seven runtime/test-entry modules compiled on Python 3.12. Live adapter history fetches returned verified OANDA data for 5m/15m/1h/4h (four requests; market closed, so these are historical/identity checks only). CI additionally checks Python 3.10 base operation and eight Linux harness regressions with optional dependencies on Python 3.12.

## Latest smoke-test audit — 4 October 2026

- User requested immediate stop and conditional PR/merge after audit. BTC r2 service was healthy before stopping at **00:34:55 IST**; now inactive/dead, Result=success, zero restarts, static unit. BTC heartbeat is PAUSED; OANDA remains inactive/disabled. No restart is scheduled.
- Corrected run covered **6.975 observed hours**: **39/39 history requests** (31 quarter-hour, 8 hourly); p50/p95/max request latency **0.49/1.34/1.52s**. All **28 quarter-hour + 7 hourly** due closes received; zero missing, >60s late, real request failures or integrity issues. Delivery maximum **10.81s / 11.78s**. Both startup/live recovery suites PASS; **2,148** history replays deduplicated.
- Independent Binance public REST comparison verified **all 35 finalized OHLCV**, zero mismatches. SQLite integrity OK. **420** RSS samples; sampled peak **78.65 MB**, last **54.17 MB**, warmed growth **-17.86 MB**. Zero cgroup pressure/OOM/kill events; manual TERM was successful. Termination cgroup peak is lower than sampled RSS and is not treated as a reconciled lifetime maximum.
- **Healthy observed BTC window; production XAU/USD feed integration remains unqualified. No PR raised/merged.** User ended the 24-hour target early, which is not classified as provider failure. OANDA still has zero market-open soak hours; tvkit requires Python >=3.11 while production is Python 3.10. BTC results alone do not establish gold sessions or production compatibility. Production remains active, PID **94302**, zero restarts; no production changes made.
- Preserved consistent SQLite backup, original summary/state/events, journal, resource/termination evidence and independent reference data under [.experiments/tradingview-btc-soak-20261003/audit-20261004/AUDIT.md](.experiments/tradingview-btc-soak-20261003/audit-20261004/AUDIT.md). Original summary says RUNNING because manual TERM does not finalize the harness; separate VM run-note and audit record the stop. Later RUNNING/24-hour review references are historical and superseded by this closeout.

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
- Subsequently authorized a separate 24-hour weekend BTC test. First run stopped with evidence preserved after a harness scheduler bug caused artificial ~65-second delivery delays; these cannot be attributed to TradingView. Fixed boundary+10s scheduling and added regressions. Corrected run **RUNNING** from 3 October **17:35:42 IST** until approximately 4 October **17:35:42 IST**: `BINANCE:BTCUSDT`, same `tvkit==0.18.0` polling/recovery path, 15-minute/hourly closes. Service `tradingview-btc-soak-20261003-r2.service`, code `/opt/tradingview-btc-soak-20261003-r2`, state `/var/lib/tradingview-btc-soak-20261003-r2`. Eight offline tests passed, including all 120 closes at +10s in a simulated full day; VM preflight and corrected startup recovery passed. Initial RSS approximately 79 MB; no OOM/pressure events. Production PID `94302`, zero restarts and source hashes verified unchanged. The OANDA experiment remains paused.
- BTC measurement stops itself after 24 hours and preserves evidence; heartbeat `TradingView BTC 24-hour soak follow-up` monitors failures/completion every **12 hours**, updated per user request. Immediate small check on 3 October around 17:56 IST was healthy: first measured 15-minute close delivered in 10.65s, no missing/late/integrity/request failures/restarts/OOM events, current RSS 65.34 MB (sampled peak 78.65 MB), production active. Next follow-up approximately 4 October 05:56 IST. VM candle polling/sampling continues unchanged. Procedure: [.experiments/tradingview-btc-soak-20261003/SOAK.md](.experiments/tradingview-btc-soak-20261003/SOAK.md). **BTC stability has not yet passed**, and cannot qualify OANDA sessions or maximum throughput.

## Next action

- BTC test is closed at the user's request; do not restart or continue its follow-up. User explicitly authorized repository integration/merge after seeing the audit. Enabling the merged optional provider on production still needs a Python 3.11+ runtime, combined memory and OANDA/session validation; no VM deployment is requested here.
- User plans to run again Monday, 5 October. No automatic restart is scheduled. Archive the weekend state and start a fresh measurement window before re-enabling the experiment and follow-up; reusing old state would misclassify the intentional pause as a failure.
- Before that OANDA run, port the BTC scheduler fix/regression check to its paused harness: the old OANDA copy contains the same boundary-offset scheduling defect. Preserve prior code/evidence and initialize a fresh run.
- Target: 48 market-open hours of 15-minute/hourly boundary polling; check gaps, late delivery, integrity, recovery and memory. Review results before discussing integration; keep production unchanged during testing.
- VM code: `/opt/tradingview-soak-20261003`; results: `/var/lib/tradingview-soak-20261003`. Full procedure/evidence pointers: [.experiments/tradingview-feed-probe-20261003/SOAK.md](.experiments/tradingview-feed-probe-20261003/SOAK.md).
- Future adapter must convert TradingView bar-start timestamps to the bot's closed-bar end timestamps, exclude forming bars, deduplicate replayed candles, and apply no Twelve Data offset to OANDA data. Verify Python compatibility and memory before deployment.
- Chart preview artifacts: `C:/Users/Lenovo/.codex/visualizations/2026/10/02/01a0fef5-933b-7d93-88a8-3ba4a0d64c30`.
- Browserless handoff verified from the completed chat "Check Browserless TradingView access" and local evidence: `POST https://production-sfo.browserless.io/function`, authenticated profile `tradingview-test`, HTTP 200, logged-in OANDA XAU/USD 15-minute chart with saved drawings/indicators. Client duration 10.099 seconds; request log billed 1 unit, balance 979 -> 978 remaining. The used-total dashboard field was cached.
- Handoff artifacts: `C:/Users/Lenovo/.codex/visualizations/2026/10/03/01a1015d-6ddf-7bb0-a215-d24af056e82e/browserless-test/` (`result.json`, `receipt.json`, screenshot, billing proof). Token is not recorded in project documentation; use the account token privately in `.env`. The saved layout URL is in the local `result.json`.

## Browserless screenshot integration PR (3 October 2026)

- PR #1 merged: https://github.com/vanshch/youtube-live-trade-monitor/pull/1 at 17:57:49 IST on 3 October 2026. Merge commit: `d479cab1577bb69c94192e163722c4b74f7d4cc4`.

- Branch: `codex/browserless-alert-screenshots`; isolated worktree: `C:/Users/Lenovo/.codex/worktrees/browserless-alert-screenshots/bot_agy`. GitHub main still held the YouTube monitor; the first PR commit records the existing local XAU/USD bot, the second adds optional screenshots. Unrelated local deletions, archives, secrets and feed experiments are excluded.
- Optional remote capture is disabled by default. Existing alert text goes first; one chart follows per alerted candle. No capture for quiet candles. Deadlines, bounded image size, PNG checks, restored-login/chart checks and sanitized errors contain capture failures. No local Chromium dependency, TradingView feed replacement or deployment.
- Added `test_chart_delivery.py`: capture to disk or send a labeled test to the allowed Telegram user without starting polling or changing alert state. Configure the ignored worktree `.env` with the account token, verified profile and saved layout URL before running it.
- Offline checks cover provider success/errors/timeouts/size, safe captions, cancellation, multiple alerts sharing one chart, candle deduplication, and both momentum directions. Device receipt, current saved-profile validity and all timeframe layouts passed during live verification below. VM memory validation remains **pending before deployment**.
- Validation: all 20 active offline tests passed on local Python 3.12; six runtime/test-entry modules passed compilation and Python 3.10 syntax parsing. GitHub CI also passed on Python 3.10 and 3.12 (run `37121569208`).
- Device validation and merge are complete. Production remains unchanged; screenshots remain disabled by default. The separate market-open TradingView **feed** soak remains unpassed and is not a prerequisite for this visual-only feature. Production configuration/deployment and VM memory validation are separate later steps.

### Live integration verification and merge authorization

- User authorized merging PR #1 after tests complete. Initial live capture found the hosted function sandbox has no Node `Buffer` global. Replaced the binary return with bounded base64 JSON, decoded/validated in Python; added wrapped-response and invalid/oversized-payload coverage. Disabled HTTPX request-level logging to prevent Telegram token URLs in logs.
- Corrected live captures passed for 5m, 15m, 1h and 4h, with OANDA headers, selected timeframes, saved drawings and indicators visually verified. Local images are preserved under the ignored worktree `.chart-artifacts/`.
- Telegram accepted the labeled test text at approximately 17:54 IST, then the 15-minute chart (both HTTP 200). User confirmed both arrived on the device and the chart looks correct. No competing polling process or alert-state changes were made.
- All 21 active offline tests and compilation passed after the fix. Final-commit CI passed on Python 3.10 and 3.12 (run `37122892997`, tested head `5e7c8afe8435aadff8c710afd6a0af0034e6cbd5`). PR #1 was then merged under the user's authorization. Production deployment and VM memory validation remain separate later steps.

Legacy YouTube monitor is disabled; archives are in `archives/`. Deferred roadmap: live ticks, indicators, multiple assets and execution bridge.
