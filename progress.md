# Project progress

Updated: 13 October 2026 (IST); last production verification remains 8 October. [README.md](README.md) owns architecture/commands. [History](docs/progress-history/README.md) is read on demand.

## Current production state

- **Deployed/enabled:** Browserless screenshots [PR #1](https://github.com/vanshch/youtube-live-trade-monitor/pull/1), TradingView OANDA [PR #2](https://github.com/vanshch/youtube-live-trade-monitor/pull/2), startup replay guard [PR #3](https://github.com/vanshch/youtube-live-trade-monitor/pull/3). Main `a2a0400124e6f29b17d8b279dfdb1c6517e3c851`, deployed **4 October, 02:01:56 IST**. On 8 October the six deployed modules matched this revision after CRLF/LF normalization; raw file hashes differ from Git blobs.
- VM `trade-bot-vm`, project `true-ion-434206-f2`, zone `us-central1-a`; `xauusd-bot.service`, user `bot_deployer`, `/opt/xauusd_telegram_bot`. **Verified 8 October, 20:19:42.193 IST:** active/running, PID `1749355`, zero automatic restarts. Actual start **5 October, 03:30:01 IST** confirms the requested pause ended on schedule. Earlier stop **4 October, 17:34:39 IST** and one-time `xauusd-bot-resume-20261005.timer` remain historical evidence; no restart was performed during this audit.
- Python **3.12.15**, runtime `/opt/xauusd-runtime-3.12.15`, venv `/opt/xauusd_telegram_bot/venv-20261004`; override `/etc/systemd/system/xauusd-bot.service.d/20-runtime-20261004.conf`. Old Python 3.10/venv retained. Production flags `TRADINGVIEW_FEED_ENABLED=true`, `CHART_SCREENSHOTS_ENABLED=true`; timeframe **15m**. Repository defaults remain off; tvkit **0.18.0** requires Python >=3.11. Read-only inspection: python-telegram-bot **22.8**, httpx **0.28.1**, httpcore **1.0.9**.
- Current saved state: resistance **#3 $4,318.00** active; both momentum settings off. Four triggers consumed their one-shot settings/levels; #4/#5 were correctly invalidated silently. The audit did not alter alert state.
- **Resource snapshot 8 October, 20:38:29.325 IST:** active/same PID, zero restarts. Service RAM **35,680,256 bytes (34.03 MiB)**, peak **94,883,840 bytes (90.49 MiB)**; process RSS **33.00 MiB**, swapped **45.04 MiB**. Limits `MemoryHigh=90M`, `MemoryMax=120M`; cumulative `memory.events` **high=65, max=0, oom=0, oom_kill=0**, current pressure averages zero. CPU average **0.038% of one core** over **320,906s**; application/runtime directories **357.52 MiB**. This is a spot/lifetime-counter observation, not a burst profile. [Resource evidence](D:/projects/bot_agy/.experiments/alert-delivery-audit-20261008/resources.json).
- Primary checkout retains older/uncommitted source. Complete reviewed implementation is `C:/Users/Lenovo/.codex/worktrees/tradingview-oanda-feed/bot_agy`. Documentation updates are uncommitted in both checkouts; preserve unrelated changes and carry reviewed docs through integration.

## Manual-chart feature — local, unmerged/undeployed

- `codex/manual-hourly-charts-20261013`, based on `a2a0400`, in `C:/Users/Lenovo/.codex/worktrees/manual-hourly-charts-20261013/bot_agy`: immediate screenshot and 20-candle H1 tick-volume buttons/commands; labeled forming bar, background tasks, auth and busy/cooldown checks. No 15m scheduler/state changes.
- **13 October:** 49 active tests passed on Windows/Linux plus eight Linux harness checks and compilation. Live verified H1 snapshots **04:36:29/04:38:21 UTC**, exactly 20 bars including **04:00 UTC** forming bar; PNGs visually checked. Local Linux synthetic peak **104,919,040 bytes (100.06 MiB)** is below 120M but above 90M; no production qualification. [Validation and evidence](docs/manual-charts-validation-20261013.md).
- No Telegram sends, new Browserless capture, production restart or experiment resume. Next: reviewed integration, target-runtime combined-memory preflight, authorized deployment of eight modules/matching dependencies, then button/device checks. Old deployment rollback below remains current; local rollback is reverting the feature branch.

## Latest production validation and risks

- **8 October retrospective audit:** logs cover application startup **5 October, 03:30:04.094 IST** through **8 October, 20:15:10.560 IST**. Journal begins 5 October 14:30; `/var/log/syslog` recovered earlier same-PID records. **355/355** scheduled checks, plus startup seed/wait; **360/360** logged OANDA history operations successful. No fallback/scheduler/chart-skip warnings. Request counts are history operations, not wire or billing counts.
- Independent replay of **500** verified raw OANDA M15 bars and logged activation times predicts exactly **four** recorded alerts: 5 October 23:15 and 7 October 18:45 two-green; 8 October 08:00 #6 resistance false breakout; 16:15 #7 support wick rejection. No missed/extra/wrong-type or earlier eligible trigger found. Dispatch followed close by **10.608/11.140/11.084/10.869s**, including the intentional **10s** buffer. A later history snapshot cannot verify every OHLC value originally returned during the run.
- All four text/chart paths completed without exceptions. Completion by next loop log was **32.357/28.209/23.622/24.245s after close**; exact send-response/device times were not persisted. **56 Telegram `NetworkError: httpx.ReadError`** exceptions were all receive `get_updates`, not alert sends; latest **8 October 18:46:10.234 IST**. Polling continued, but incoming commands can be delayed. No registered application error handler. Alert/candle state is consumed before sending: failed text delivery has no durable outbox/retry; no such failure was observed here.
- Read-only provider probes completed **8 October, 20:22:21.034 IST**: OANDA 500-bar history **1.903s**; Twelve Data three closed bars **0.242s**; Yahoo three closed futures bars **0.174s**; Telegram authentication/webhook check **0.573s**, no webhook, zero pending updates. No second poller, Telegram test message or extra Browserless capture. [Full audit, evidence and limits](D:/projects/bot_agy/.experiments/alert-delivery-audit-20261008/REPORT.md).
- Existing offline qualification: **36 active tests**, CI `37151699143` passed Python 3.10/3.12 and eight Linux harness checks. Corrected BTC audit: **6.975h**, user-stopped **4 October 00:34:55 IST**, **39/39** requests, **35/35** due closes matching Binance, no missing/>60s late/integrity failures, recovery suites passed; peak RSS **78.65 MB**, zero OOM. [Preserved audit](D:/projects/bot_agy/.experiments/tradingview-btc-soak-20261003/audit-20261004/AUDIT.md). This qualifies BTC's observed window only.
- Offline OANDA/COMEX studies remain historical, not deployed strategies: **4 October 21:53:28–22:06:20 IST**, M15 cutoff **6,230**, 6,231 truncated; **68** complete sessions, **748 H1/2,992 M15** pairs; **49** requests, zero fetch errors. [History/stress](D:/projects/bot_agy/.experiments/volume-history-stress-20261004/REPORT.md), [original seven-day](D:/projects/bot_agy/.experiments/volume-comparison-20261004/REPORT.md), [session ratios](D:/projects/bot_agy/.experiments/volume-session-comparison-20261004/REPORT.md), [H1 opposite moves](D:/projects/bot_agy/.experiments/volume-opposite-moves-20261004/REPORT.md). Exact earlier metrics remain in reports/verbatim archive.

## Next actions and boundaries

- Manual-chart implementation is local only; no production changes. Polling error handling, durable retries and memory pressure remain separate work. The retrospective audit verifies known triggers; continuous OANDA market-session integrity/resource soak remains unperformed.
- BTC/OANDA experiment services and follow-ups remain **paused**; no new smoke run scheduled. The original 24h BTC target was intentionally stopped early. A separately authorized OANDA harness run needs corrected boundary scheduling/regressions and fresh state; its original harness has the offset bug.
- Production monitors 15m only; recurring market-hours gate, hourly monitoring and volume alert rules remain unimplemented/unenabled. Saved volume ideas/thresholds remain unspecified. Yahoo is futures; Twelve Data's approximate `-0.50` offset does not guarantee OANDA parity. Legacy YouTube monitoring is disabled.

## Evidence and rollback

- [Deployment report](C:/Users/Lenovo/.codex/worktrees/tradingview-oanda-feed/bot_agy/.chart-artifacts/deployment-20261004/DEPLOYMENT.md); VM `/opt/xauusd_telegram_bot/deployment-20261004.json`. Local ignored artifacts are machine-specific; preserve independently of Git.
- Root-private backup `/opt/xauusd-backups/20261004-pr1-pr2`; rollback `sudo python3 /opt/xauusd-backups/20261004-pr1-pr2/rollback.py`. Restores code/config/runtime, preserving **current** alerts. Audit requires no production rollback.

## Recent milestones

- **13 October:** manual screenshot/H1-volume feature implemented locally; live data/rendering and offline regressions verified; integration/deployment pending.
- **8 October:** read-only full-run trigger/API audit; timer resume verified retrospectively; four expected triggers matched, polling errors and memory pressure documented. No fixes deployed.
- **4 October:** H1 opposite-change/session analyses and history stress completed; source handoffs archived verbatim.
- **4 October:** documentation synchronized; substantial-work/PR-closeout maintenance added.
- **4 October, 17:34:39 IST:** requested production stop; one-time 5 October 03:30 resume timer established.
- **4 October:** PR #3 merged; PRs #1/#2/#3 deployed/enabled; runtime/state/memory verified at deployment.
- **4 October:** BTC smoke intentionally stopped/audited; PR #2 merged after authorization.
- **3 October:** PR #1 merged after confirmed Telegram test text/chart receipt and four timeframe captures.
