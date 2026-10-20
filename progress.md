# Project progress

Updated: 20 October 2026 (IST). [README](README.md) owns architecture; [history](docs/progress-history/README.md) preserves verbatim prior handoffs, including pre-volume, WARP-disabled and pre-resilience-closeout states. Load history only on demand.

## Integration and authorization

- [PR #11](https://github.com/vanshch/Trading-Bot/pull/11) merged **20 October 03:37:00 UTC**, `0861d91771f2cf4a09ffb2f942b9db837c02d415`. User authorized PR, CodeRabbit pipeline, shipping and desktop tests. Known-expired/transient acknowledgments preserve authorized actions; chart notices run in background and retry once; unavailable verified H1 history retries once within 55s. Invalid data is rejected; photo sends never retry. Telegram error handler logs credential-safe operation/type; PTB receive-polling recovery remains unchanged.
- **79 offline tests**, syntax and focused Ruff checks passed. Final reviewed source `a132960`; final-head `b6c3445` CI passed all three jobs plus eight Linux harness checks, [39365137852](https://github.com/vanshch/Trading-Bot/actions/runs/39365137852). CodeRabbit CLI **0.9.0**: [first review](artifacts/reviews/telegram-resilience-coderabbit-20261020.md), three minor findings fixed; [final review](artifacts/reviews/telegram-resilience-coderabbit-final-20261020.md), no code findings, two immutable-history link suggestions intentionally rejected. Later documentation does not change runtime bytes.

## Last verified production

- [Release/desktop/rollback evidence](docs/telegram-resilience-deployment-20261020.md). Cutover **20 October 03:37:36.635849 UTC / 09:07:36.635849 IST**: all nine modules/two manifests matched merged `0861d91`; environment, current alerts and service configuration byte-identical. Source hashes and backup integrity verified. Earlier PR #9/`06b1490` deployment **00:33:16.229237 UTC** and original probe limits remain in [volume report](docs/hourly-volume-deployment-20261020.md); they are historical, not the running revision.
- GCE `trade-bot-vm`, project `true-ion-434206-f2`, zone `us-central1-a`, `e2-micro`; `xauusd-bot.service`, `bot_deployer`, `/opt/xauusd_telegram_bot`. Python **3.12.15**, runtime `/opt/xauusd-runtime-3.12.15`, venv `venv-20261004`; tvkit **0.18.0**, PTB **22.8**, httpx **0.28.1**, Pillow **12.3.0** via `chart-deps-20261013-044810` PYTHONPATH/`30-chart-deps-20261013.conf`. Direct maintenance needs service PYTHONPATH. Runtime drop-in unchanged. TradingView/automatic photos ON; monitoring **15m**; **MemoryHigh=90M / MemoryMax=120M**.
- **Latest actual observation 20 October 03:40:52.310046 UTC / 09:10:52.310046 IST:** active/running, PID **257088**, restarts **0**, RAM **90,161,152 bytes**, peak **93,327,360**, high/max/oom/oom_kill **0**. Zero startup/test traceback, scheduler/provider/send/polling/handler/chart failures. Startup greeting accepted; source, environment and alert-state hashes verified. One price alert, momentum OFF, next ID **9**; H1 volume **ON**, armed **03:19:58.588567 UTC**, last processed **0**, preserved from user settings.
- Desktop Computer Use confirmed **current → tele_alert**. `/start`, Status and Live & Closed Bar callbacks responded; H1 OANDA image and Browserless screenshot arrived/rendered (screenshot captured **03:39:48 UTC**). No alert toggles or competing poller. This establishes desktop receipt, not a separate phone or a live qualifying alert.

## Qualification limits and open work

- Combined live target-runtime probe passed **03:35:39.956351 UTC**, **4.698s**: concurrent histories **[3,21,20]**, H1 PNG **42,200 bytes**, maximum synthetic screenshot **5,242,880 bytes**/response **6,990,542 bytes**. Process peak RSS **112,889,856**; in-process cgroup observation **03:35:39.959584 UTC**, peak **94,625,792**, current **94,113,792**, high **96**, max/oom/oom_kill **0**. First probe passed but post-exit cgroup collection failed; that counter check is not a pass. Full details in release evidence.
- **WARP remains disabled/stopped; do not re-enable without user instruction.** Package/config retained. [Maintenance](docs/warp-disabled-validation-20261020.md): stop requested **03:16:52.261390 UTC**, systemd terminated after 90s (unit failed/stopped, PID 0). Available RAM rose to **663 MiB**; I/O wait fell from **92–96%** to **0.30%/0.10%**, zero swap-in/out over two 5s samples. WARP accounted for **1,084/1,102** major faults before stop; leak not proven. Desktop acceptance now passed; long-run memory/polling reliability remains open.
- Durable trading-alert outbox/retry remains **unimplemented**: one-shot state consumes before send. No production network faults were deliberately induced; offline tests cover expiry/timeouts. Continuous OANDA-session and live qualifying H1/phone receipt remain unqualified. H1 processed 03:00 UTC and switched OFF before maintenance; that notification receipt remains unconfirmed; user later re-armed it.
- BTC/OANDA experiments stay **paused**. BTC stopped **4 October 00:34:55 IST**, 6.975h/39 requests/35 due closes; [local historical audit](D:/projects/bot_agy/.experiments/tradingview-btc-soak-20261003/audit-20261004/AUDIT.md). Historical alert audit resumed **5 October 03:30:01 IST**; 8 October **20:19:42.193/20:38:29.325 IST**, 355/355 checks, 360/360 histories, four triggers, 56 polling ReadErrors; [local report](D:/projects/bot_agy/.experiments/alert-delivery-audit-20261008/REPORT.md). These do not qualify this release. Market-hours gate unimplemented; Twelve Data offset approximate, Yahoo futures distinct; YouTube disabled.

## Recovery and next actions

- Runtime rollback: `sudo python3 /opt/xauusd-backups/20261020-resilience/rollback.py`; restores previous source/manifests, preserves current alerts/environment and leaves WARP disabled. Backup source/script/private snapshots verified; rollback not executed. VM record `deployment-20261020-resilience.json`; staging `/opt/xauusd-bot-staging-20261020-resilience/`; private local `.chart-artifacts/deployment-20261020-resilience/`. Prior volume/PR #4 backups remain historical. Git revert and runtime rollback are separate.
- Observe normal usage and next qualifying armed H1 close; verify device receipt/OFF state. Do not re-enable WARP, start another production-token poller or resume experiments implicitly. WARP restoration only when requested: `sudo systemctl enable --now warp-svc.service`, then check host pressure/connectivity.
- Primary `D:/projects/bot_agy`; GitHub main authoritative. [PR #8](https://github.com/vanshch/Trading-Bot/pull/8) cleanup merged **18 October 16:24:56 UTC**, `0aa62c353c603e0ea9061e3f0ff00519931a164d`. Recovery remains `C:/Users/Lenovo/.codex/recovery/bot_agy-20261018-210819-8055` (304 files/all refs/private evidence; venvs/caches excluded). Unique legacy branches retained; no branch/worktree pruning this release.

## Recent milestones

- **20 October:** PR #11 merged/deployed; desktop command/H1/screenshot acceptance passed.
- **20 October:** WARP disabled for user testing; host paging stalls eased.
- **20 October:** PR #9 H1 volume alarm merged/deployed, default OFF.
- **18 October:** PR #8 cleanup merged; primary reconciled, recovery preserved.
- **16 October:** PR #5 cooldown removal merged; deployed with PR #9.
- **13 October:** PR #4 deployed; original evidence preserved.
- **8 October:** retrospective alert audit; long-run follow-ups remain open.
- **4 October:** studies archived; experiments paused.
