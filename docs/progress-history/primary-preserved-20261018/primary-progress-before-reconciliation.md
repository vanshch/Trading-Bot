# Project progress

Updated: 13 October 2026 (IST). [README](README.md) owns architecture/commands; [history](docs/progress-history/README.md) preserves prior handoffs verbatim, read on demand.

## Current production state

- **Merged/deployed:** PRs #1–#4, including [manual screenshots/H1 volume](https://github.com/vanshch/Trading-Bot/pull/4). Runtime source `05527f7857219432074deb5a6377e0b0009322e0`; PR #4 merged **13 October 04:48:10 UTC / 10:18:10 IST**, deployed verification **04:54:26.581714 UTC / 10:24:26.581714 IST**. Eight modules/two manifests matched release SHA256s. Documentation-only main commits do not change deployed code.
- VM `trade-bot-vm`, project `true-ion-434206-f2`, zone `us-central1-a`, `xauusd-bot.service`, user `bot_deployer`, `/opt/xauusd_telegram_bot`. **Verified 13 October 04:54:52.834855 UTC / 10:24:52.834855 IST:** active/running, PID **3242675**, automatic restarts **0**; started **04:54:20 UTC / 10:24:20 IST**. Logs confirm OANDA startup history, scheduler and normal startup-menu send; zero startup tracebacks/requested-chart failures. Manual Telegram device/photo outcomes remain **pending user testing**.
- Python **3.12.15**, `/opt/xauusd-runtime-3.12.15`, venv `/opt/xauusd_telegram_bot/venv-20261004`; prior runtime override `20-runtime-20261004.conf` unchanged. tvkit **0.18.0**, python-telegram-bot **22.8**, httpx **0.28.1**. Pillow **12.3.0** is isolated in `chart-deps-20261013-044810`, loaded through `30-chart-deps-20261013.conf` PYTHONPATH; include that environment in direct maintenance commands. Old runtime retained.
- Production automatic flags `TRADINGVIEW_FEED_ENABLED=true`, `CHART_SCREENSHOTS_ENABLED=true`; scheduler **15m**. Manual `/screenshot` and `/chart1h`/buttons are available, independent of automatic flags; shared 30s cooldown and one in-flight capture. H1 is on demand only, exactly 20 native OANDA bars with labeled forming candle/tick volume.
- `.env` and `data/alerts.json` unchanged at cutover/health verification. Resistance **#3 $4,318.00** active, both momentum settings off, next ID **9**. State SHA256 and backup details in [deployment evidence](docs/manual-charts-deployment-20261013.md).
- Service memory at health check **82,190,336 bytes**, peak **82,739,200 bytes**; `MemoryHigh=90M`, `MemoryMax=120M`; **high/max/oom/oom_kill=0** in new cgroup. This is startup observation, not sustained load qualification.
- Authoritative merged source: `C:/Users/Lenovo/.codex/worktrees/manual-hourly-charts-20261013/bot_agy` and GitHub main. Primary `D:/projects/bot_agy` retains older/uncommitted source; do not deploy it wholesale. Current docs are integrated into main; preserve unrelated primary changes.

## Validation and unresolved risks

- **PR #4:** 49 active tests passed on Windows/Linux, plus eight Linux harness checks and compilation; CI **38348022918** passed Python 3.10/3.12 and tvkit dependency checks on head `71686b6d3deb8334c3bde5dad8d0319d46da1e28`. CodeRabbit skipped review. Two local verified H1 snapshots/render inspections **04:36:29/04:38:21 UTC**. [Implementation validation](docs/manual-charts-validation-20261013.md).
- **Target-runtime preflight 13 October 04:52:03.290317 UTC / 10:22:03.290317 IST:** dedicated 90M/120M cgroup, live verified 20-bar H1 render, synthetic maximum screenshot payload; PNG **45,987 bytes**. Peak RSS **103,616,512 bytes**, cgroup peak **94,543,872 bytes**; **high=110, max=0, oom=0, oom_kill=0**. Bounded run passed; pressure and simultaneous live-provider/long-run qualification remain limitations. No manual Telegram photo or new Browserless capture was sent; the sole production bot sent its normal startup greeting.
- **8 October retrospective audit remains valid historically:** actual prior resume **5 October 03:30:01 IST**; verification **8 October 20:19:42.193 IST**, resources **20:38:29.325 IST**. 355/355 checks, 360/360 OANDA history operations, exactly four expected recorded triggers; dispatch followed close by 10.608/11.140/11.084/10.869s including the 10s buffer. **56 receive-polling ReadErrors** occurred, no observed alert-send failure. Exact historical metrics/state in verbatim history and [audit](D:/projects/bot_agy/.experiments/alert-delivery-audit-20261008/REPORT.md).
- Incoming command delays/polling error handling, memory pressure, delivery-event persistence/durable retry, and continuous OANDA market-session integrity/resource soak remain unresolved. One-shot state is consumed before sending; no durable outbox. Startup success does not prove manual photo delivery or future alerts.
- BTC/OANDA experiment services/follow-ups remain **paused**; original BTC smoke intentionally stopped **4 October 00:34:55 IST** after 6.975h, 39/39 requests and 35/35 due closes. [Audit](D:/projects/bot_agy/.experiments/tradingview-btc-soak-20261003/audit-20261004/AUDIT.md). Future OANDA harness work needs boundary corrections/fresh state and separate authorization.
- Historical H1/session/history studies and their original evidence links are preserved in [prior handoffs](docs/progress-history/README.md). They are not deployed strategies. Hourly alert monitoring, volume rules and recurring market-hours gate remain unimplemented/unenabled; thresholds unspecified. Yahoo is futures, Twelve Data's -0.50 offset approximate. Legacy YouTube disabled.

## Next actions, evidence and rollback

- User tests `/start`, **Screenshot Now**, **1H Chart + Volume** (30s between requests). Record device outcomes separately; do not start another production-token poller. Address broader delivery/polling/resource work separately, leaving experiments paused.
- [Deployment report](docs/manual-charts-deployment-20261013.md); VM `/opt/xauusd_telegram_bot/deployment-20261013.json`; staged evidence `/opt/xauusd-bot-staging-20261013-044810/`; local ignored `.chart-artifacts/deployment-20261013/` in merged worktree. Preserve machine-local evidence independently of Git; no raw logs/secrets in Markdown.
- Root-private backup `/opt/xauusd-backups/20261013-pr4-044810`; rollback `sudo python3 /opt/xauusd-backups/20261013-pr4-044810/rollback.py`. Restores prior code/manifests/drop-in while retaining **current** alerts/private environment; dependency directory remains unused. Older `/opt/xauusd-backups/20261004-pr1-pr2` remains historical. No rollback performed.

## Recent milestones

- **13 October 10:24 IST:** PR #4 merged/deployed; target cgroup, hashes, state and startup verified; user photo testing pending.
- **13 October:** manual screenshot/H1-volume implementation and local/live/CI checks passed.
- **8 October:** read-only trigger/API audit; four expected triggers, polling errors and memory pressure documented.
- **4 October:** offline volume/session/history analyses; handoffs archived.
- **4 October:** contributor closeout/documentation workflow synchronized.
- **4 October 17:34:39 IST:** requested production stop; scheduled **5 October 03:30:01 IST** resume later verified.
- **4 October:** PRs #1/#2/#3 deployed, startup replay guard enabled; BTC smoke stopped/audited after user request.
- **3 October:** PR #1 merged after verified four-timeframe captures and Telegram device receipt.
