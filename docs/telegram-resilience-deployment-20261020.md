# Telegram resilience release — 20 October 2026

[PR #11](https://github.com/vanshch/Trading-Bot/pull/11) merged at **03:37:00 UTC**,
revision `0861d91771f2cf4a09ffb2f942b9db837c02d415`. Cutover was
**03:37:36.635849 UTC / 09:07:36.635849 IST**. All nine runtime modules and both
manifests matched the reviewed Git blobs; documentation-only commits do not
change these release bytes. WARP remains stopped and disabled.

## Review and validation

- **79 offline tests** passed locally; syntax checks and focused Ruff
  E9/F63/F7/F82 checks passed. Final-head `b6c3445` CI passed Python 3.10/3.12,
  optional tvkit dependencies and eight Linux harness checks:
  [run 39365137852](https://github.com/vanshch/Trading-Bot/actions/runs/39365137852).
- CodeRabbit CLI **0.9.0**, [first pass](../artifacts/reviews/telegram-resilience-coderabbit-20261020.md)
  on `043e549`: three minor findings fixed. [Final pass](../artifacts/reviews/telegram-resilience-coderabbit-final-20261020.md)
  on `a132960`: no runtime findings; two suggestions to rewrite immutable archive
  links intentionally rejected under AGENTS. Final later changes were review
  documentation; merged runtime hashes matched `a132960` exactly.
- Target-runtime combined probe used the service PYTHONPATH and 90M/120M cgroup
  limits, three concurrent OANDA histories (3 price, 21 H1 alarm, 20 H1 chart),
  chart rendering and maximum screenshot buffers. First probe passed in 5.111s
  but cgroup counters vanished before collection; that collection failed and is
  not claimed as a counter verification.
- Repeated probe passed **03:35:39.956351 UTC**, **4.698s**, histories `[3,21,20]`,
  PNG **42,200 bytes**, synthetic image **5,242,880 bytes**, response **6,990,542
  bytes**, process peak RSS **112,889,856 bytes**. In-process cgroup observation
  **03:35:39.959584 UTC**: peak **94,625,792**, current **94,113,792**, high **96**,
  max/oom/oom_kill **0**. Process RSS and cgroup memory use different accounting;
  preserve both measurements. Runtime: Python **3.12.15**, tvkit **0.18.0**, PTB
  **22.8**, httpx **0.28.1**, Pillow **12.3.0**. No preflight Telegram messages.

## Cutover and recovery

The service was stopped, its source/manifests/service configuration and private
cutover snapshots backed up under `/opt/xauusd-backups/20261020-resilience`, and
all eleven release files installed together with `bot_deployer` ownership.
`.env`, current `data/alerts.json` and systemd configuration were byte-identical
across cutover. Dependencies/manifests did not change. Startup observation
**03:38:04.626513 UTC**: active/running, PID **257088**, automatic restarts **0**,
RAM **84,627,456 bytes**, peak **85,147,648**, high/max/oom/oom_kill **0**; greeting
accepted, application/15m scheduler started, no startup traceback/provider/send
or scheduler errors. Hashes, rollback source/script and private backup snapshots
verified. One price alert, momentum OFF, next ID **9**, H1 volume **ON**, armed at
**03:19:58.588567 UTC**, last processed zero, preserved from the user's current
settings. No test changes alert settings.

Rollback, only within authorized recovery:

```sh
sudo python3 /opt/xauusd-backups/20261020-resilience/rollback.py
```

This restores the previous nine modules/two manifests while preserving current
alerts/environment; it does not re-enable WARP. Previous volume-release rollback
remains historical. Rollback has not been executed.

## Desktop acceptance and limits

Computer Use selected the existing Telegram Desktop window, the **current**
folder and bot **tele_alert**. After deployment, observed `/start` response,
Status-button reply, Live & Closed Bar reply and a fully rendered 20-candle
OANDA H1/tick-volume image. Screenshot Now also delivered a rendered TradingView image, captured **03:39:48
UTC**. Final observation **03:40:52.310046 UTC / 09:10:52.310046 IST**: bot
active, same PID, restarts **0**, RAM **90,161,152 bytes**, peak **93,327,360**,
high/max/oom/oom_kill **0**; zero traceback, provider/scheduler/polling/handler/chart
failures since cutover. Source/private-state and rollback hashes still verified;
WARP disabled with no process. No alert toggles, test price alerts or
competing production-token poller were used. Desktop observation does not prove
receipt on a separate phone.

Expired acknowledgments, unreachable notices and provider errors were simulated
in offline regressions, not deliberately induced on production. Durable trading
alert outbox/retry, continuous OANDA-session qualification and long-run polling
reliability remain open. Bounded retries do not guarantee external connectivity.
Experiments stay paused; WARP must not be re-enabled without user instruction.

Local private evidence: `.chart-artifacts/deployment-20261020-resilience/` and VM
staging `/opt/xauusd-bot-staging-20261020-resilience/`, including release manifest,
first/repeated preflight results, rollout record and health result. Raw private
logs/environment are not tracked or uploaded.
