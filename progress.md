# Project progress

Updated: 20 October 2026 (IST). [README](README.md) owns architecture/commands; [history](docs/progress-history/README.md) preserves verbatim prior handoffs, read on demand.

## Hourly volume feature — branch only

- `feature/hourly-volume-alert`, based on reviewed main `0aa62c353c603e0ea9061e3f0ff00519931a164d`. User requires testing before merge: **do not merge or deploy**. Default OFF; Telegram button/`/volume1h` arms a one-shot alarm at closed H1 OANDA tick volume ≥120% of the preceding 20 closed trading bars' mean, excluding the candidate. Separate scheduler; 15m alerts unchanged. Forming/grazing/pre-arm/pre-restart/replayed closes cannot trigger. Invalid data retains arming; no fallback volume.
- Offline validation **20 October 03:56:42 UTC / 09:26:42 IST**: 70 tests passed (20 new), compilation passed; new module/tests pass Ruff lint/format. CodeRabbit/PR/CI pending. [Validation and isolated test procedure](docs/hourly-volume-validation-20261020.md). No live provider request, Telegram send, production check or deployment performed. One-shot consumes state before send; no durable retry. Device/market-open tests and combined VM memory qualification pending.

## Repository reconciliation

- **Merged cleanup ([PR #8](https://github.com/vanshch/Trading-Bot/pull/8)):** merged **18 October 16:24:56 UTC**, `0aa62c353c603e0ea9061e3f0ff00519931a164d`; branch `chore/untrack-legacy-youtube` removes 56 retired YouTube/deployment/temporary-auth files from tracking with exact ignore entries; local copies and Git history retained. Active bot/manifests, dev requirements, historical snapshots and substantive reviews remain tracked. Tracked-only compilation, 50 active tests and eight Linux harness checks passed; CodeRabbit CLI found zero issues. [Validation](artifacts/reviews/untrack-legacy-validation-20261018.md) records observed times/review. The linked PR owns final CI/merge status; this cleanup does not deploy the bot.
- Primary `D:/projects/bot_agy` is the authoritative local checkout. Reconciliation first fast-forwarded `ce197ca` to `a0f0a2044a7966efe232b7f66070a106007a777a`; [PR #7](https://github.com/vanshch/Trading-Bot/pull/7) then merged as `7c50d4bf2573659d90568cbb3dc617a6d849e7b4` on **18 October 15:50:38 UTC**. Its former local modifications/deletions are preserved in private recovery, not reapplied over newer code. Eight redundant original local branches were pruned after ancestry/reference checks; `codex/wisper_smal` and `legacy-main-pre-github` retain unique history. [Cleanup PR #7](https://github.com/vanshch/Trading-Bot/pull/7) owns final CI/merge status; [reconciliation evidence](artifacts/reviews/checkout-reconciliation-20261018.md) records local actions and review.
- Recovery: `C:/Users/Lenovo/.codex/recovery/bot_agy-20261018-210819-8055`; verified copies of 304 files from both checkouts, all-ref Git bundle, hashes, patches and original status. Virtual environments/caches excluded. Private environment, alerts, logs, experiments and local archives preserved. Divergent Markdown history retained under distinct paths. The duplicate detached worktree was retired after exact source/recovery/reference checks. Primary remains authoritative. No production operations or experiment restarts.
- [PR #5](https://github.com/vanshch/Trading-Bot/pull/5) merged 16 October **09:37:32 UTC / 15:07:32 IST**, runtime change `9b730d0f9f576cf1c9db1e1b562d48ff945df591`: removes timed manual-chart cooldown, retains one in-flight reservation. **Not deployed.** Historical validation: 50 active tests; CI [38881513484](https://github.com/vanshch/Trading-Bot/actions/runs/38881513484) on `9d69a60`; CLI CodeRabbit 0.9.0 zero findings at 15:04 IST on `3874971`. Broader inherited Ruff/formatting findings remain. [Evidence](artifacts/reviews/manual-chart-cooldown-validation-20261016.md).

## Last verified production state

- **PRs #1–#4 deployed**, source `05527f7857219432074deb5a6377e0b0009322e0`; PR #4 merged **13 October 04:48:10 UTC / 10:18:10 IST**. Deployment hashes verified **04:54:26.581714 UTC / 10:24:26.581714 IST**: eight modules/two manifests matched release. Documentation/main changes do not establish deployment.
- VM `trade-bot-vm`, project `true-ion-434206-f2`, zone `us-central1-a`; `xauusd-bot.service`, user `bot_deployer`, `/opt/xauusd_telegram_bot`. **Last health observation 13 October 04:54:52.834855 UTC / 10:24:52.834855 IST:** active/running, PID **3242675**, automatic restarts **0**, started **04:54:20 UTC / 10:24:20 IST**. OANDA startup history/scheduler/menu send logged, zero startup tracebacks/requested-chart failures. Manual Telegram device/photo outcomes **pending user testing**. No newer runtime check.
- Python **3.12.15**, `/opt/xauusd-runtime-3.12.15`, venv `/opt/xauusd_telegram_bot/venv-20261004`; `20-runtime-20261004.conf` unchanged. tvkit **0.18.0**, python-telegram-bot **22.8**, httpx **0.28.1**. Pillow **12.3.0** in `chart-deps-20261013-044810`, loaded through `30-chart-deps-20261013.conf` PYTHONPATH; direct maintenance must include this environment. Old runtime retained.
- Production flags `TRADINGVIEW_FEED_ENABLED=true`, `CHART_SCREENSHOTS_ENABLED=true`; scheduler **15m**. Manual screenshot/H1 commands/buttons independent of automatic flags; deployed code still has **30s cooldown plus one in-flight capture**. H1 on demand only: 20 native OANDA bars, labeled forming candle/tick volume.
- `.env`/`data/alerts.json` unchanged at cutover/health verification: resistance **#3 $4,318.00**, momentum off, next ID **9**. State SHA256 in [deployment evidence](docs/manual-charts-deployment-20261013.md).
- Health-check memory **82,190,336 bytes**, peak **82,739,200**; `MemoryHigh=90M`, `MemoryMax=120M`; new-cgroup **high/max/oom/oom_kill=0**. Startup observation only.

## Validation limits and open risks

- PR #4: 49 active Windows/Linux tests, eight Linux harness checks and compilation passed. CI **38348022918** on `71686b6d3deb8334c3bde5dad8d0319d46da1e28`; CodeRabbit skipped. H1 snapshots inspected **13 October 04:36:29/04:38:21 UTC**. [Validation](docs/manual-charts-validation-20261013.md).
- Target-runtime preflight **13 October 04:52:03.290317 UTC / 10:22:03.290317 IST**: dedicated 90M/120M cgroup, 20-bar H1 render plus synthetic maximum screenshot; PNG **45,987 bytes**, peak RSS **103,616,512**, cgroup peak **94,543,872**; **high=110, max/oom/oom_kill=0**. Bounded pass; simultaneous live-provider/long-run qualification outstanding. No manual Telegram photo or Browserless capture sent; normal production startup greeting only.
- Historical audit: resume **5 October 03:30:01 IST**; observations **8 October 20:19:42.193/20:38:29.325 IST**. **355/355** checks, **360/360** OANDA history operations, four expected triggers; close-to-dispatch **10.608/11.140/11.084/10.869s**, including 10s buffer. **56 receive-polling ReadErrors**, no observed send failure. [Audit](D:/projects/bot_agy/.experiments/alert-delivery-audit-20261008/REPORT.md).
- Command/polling delays, memory pressure, durable delivery-event logging/retry and continuous OANDA market-session soak remain unresolved. One-shot settings are consumed before send; **no durable outbox**. Startup success does not prove future delivery.
- BTC/OANDA experiments/follow-ups remain **paused**. BTC smoke stopped **4 October 00:34:55 IST**, after **6.975h**, **39/39** requests and **35/35** due closes. [Audit](D:/projects/bot_agy/.experiments/tradingview-btc-soak-20261003/audit-20261004/AUDIT.md). OANDA harness needs corrected boundaries/fresh state and separate authorization.
- Historical H1/session studies are not deployed strategies. Hourly volume alarm remains branch-only, not deployed; recurring market-hours gate remains unimplemented. Twelve Data -0.50 calibration approximate; Yahoo is futures. Legacy YouTube disabled.

## Next actions and rollback

- Use the cleanup evidence/linked PR for review and integration status. Deploy PR #5 only within authorized operations, then test repeated manual requests on Telegram. Until then production needs 30s between requests. Never start a second production-token poller; leave experiments paused.
- [Deployment report](docs/manual-charts-deployment-20261013.md); VM `/opt/xauusd_telegram_bot/deployment-20261013.json`; staging `/opt/xauusd-bot-staging-20261013-044810/`. Preserve local/private evidence independently of Git.
- Production rollback: root-private `/opt/xauusd-backups/20261013-pr4-044810/rollback.py` via `sudo python3`; restores prior code/manifests/drop-in, retains **current** alerts/environment. Dependency directory remains unused. Older backup `/opt/xauusd-backups/20261004-pr1-pr2` historical. No rollback performed. Local cleanup recovery instructions are in private `RESTORE.md` and reconciliation evidence; restore into a separate checkout first.

## Recent milestones

- **20 October:** branch-only H1 volume alarm implemented; user testing required before merge.

- **18 October:** retired-file tracking cleanup; local preservation and tracked-only validation recorded separately.
- **18 October:** primary source reconciled; private recovery verified; redundant branches pruned.
- **16 October 15:07 IST:** PR #5 merged, not deployed; CLI review/CI passed.
- **13 October 10:24 IST:** PR #4 deployed, startup verified; user photo tests pending.
- **8 October:** retrospective alert audit; polling/memory follow-ups remain.
- **4 October:** offline studies archived; experiments paused; production stop/resume history preserved.
