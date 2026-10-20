# Hourly volume alarm deployment — 20 October 2026

The user explicitly authorized merging and deploying for phone testing,
superseding the earlier branch-only restriction. [PR #9](https://github.com/vanshch/Trading-Bot/pull/9)
merged **00:30:02 UTC / 06:00:02 IST**, revision
`06b149058e29b80535347e06b478af3c2d262ffd`.
All three [CI jobs](https://github.com/vanshch/Trading-Bot/actions/runs/39335755861)
passed on head `5578aee0f00e569c7973d6362f6ce80e2ce6d0a3`: 70 active tests on
Python 3.10/3.12, optional dependency import, and eight Linux harness checks.
The [CLI review](../artifacts/reviews/hourly-volume-coderabbit-20261020.md)
found no code issues; its two minor documentation findings were fixed. The
hosted draft review was skipped, so it is not additional review evidence.

## Runtime preflight

Source was staged from a tracked-only archive. Windows `core.autocrlf=true`
initially converted archive contents to CRLF; hash validation rejected that
staging before production changes. Rebuilding with
`git -c core.autocrlf=false archive` matched all nine modules and both dependency
manifests against Git blobs. The merged revision has those same eleven hashes.

An isolated service-user cgroup used the production Python/PYTHONPATH and
**MemoryHigh=90M, MemoryMax=120M**, with no Telegram poller or test sends.
Two preliminary probes timed out: the first retained a redundant 5 MB original
image, and the second ran a separate Linux test subprocess while retaining the
stress buffers. The second successfully completed live fetches and rendering
before its unrelated subprocess exhausted the time budget. These failures are
retained in private evidence and are not counted as successful qualifications.

The application-only probe passed **00:30:26.925617 UTC / 06:00:26.925617 IST**:

- **22.85 seconds**, including optional adapter warming, three concurrent verified
  OANDA requests (3 closed 15m bars, 21 closed H1 alarm bars, 20 H1 chart bars),
  volume evaluation, keyboard/default-OFF checks and H1 rendering.
- A synthetic **5,242,880-byte** decoded image and **6,990,542-byte** response
  remained resident during the requests/render; output chart **41,147 bytes**.
- In-process peak RSS **94,797,824 bytes**. Last running cgroup sample at
  **00:30:19.077015 UTC**: peak **94,703,616**, high events **162**,
  max/oom/oom_kill **0**. Unit subsequently exited successfully, status **0**.
  This host removes an exited cgroup; final cgroup counters are unavailable.
  The sampled counters and final process RSS are distinct measurements.
- Host memory/I/O pressure was significant; `warp-svc` used approximately
  **555,336 KiB RSS** at that sample. It was not modified or stopped. This
  bounded pass does not establish sustained latency or future market delivery.

## Cutover and verification

At **00:33:16.229237 UTC / 06:03:16.229237 IST**, installed all nine modules and
both manifests together from the verified staging directory. Runtime remained
Python **3.12.15**, tvkit **0.18.0**, python-telegram-bot **22.8**, httpx **0.28.1**,
Pillow **12.3.0**. Existing runtime/chart dependency drop-ins and 90M/120M limits
were unchanged. This also deploys PR #5's removal of the manual-chart cooldown;
the shared in-flight reservation remains.

Final health observation **00:34:41.981633 UTC / 06:04:41.981633 IST**:

- `xauusd-bot.service` active/running, PID **239854**, automatic restarts **0**;
  started **00:33:16 UTC**. All eleven deployed hashes match the merged release.
- Memory **93,782,016 bytes**, peak **94,580,736**; high events **28**,
  max/oom/oom_kill **0**. Memory pressure remains an open operating concern.
- The sole production poller logged successful startup greeting acceptance,
  application startup and 15m scheduler startup. Zero startup tracebacks,
  scheduler errors, provider-unavailable warnings, startup-send failures or
  polling ReadErrors were observed in this window.
- `.env` and `data/alerts.json` were byte-for-byte preserved at cutover and health
  check. State SHA256 remained
  `11928638f4ed583d184a31e6db31b77ce20d0a9260229dffdb1b988fc7c5b2ee`:
  one price alert, momentum OFF, next ID **9**. New volume state was absent and
  therefore defaults **OFF**; deployment did not arm it.
- Raw OANDA/automatic-photo flags remain enabled; price/momentum timeframe **15m**.
  The independent H1 alarm is available through `/volume1h` and its inline button.
  No extra poller, manual photo send, live Browserless capture or paused
  BTC/OANDA experiment was started. Device receipt and a qualifying live H1
  alert remain user acceptance checks, not verified startup outcomes.

For phone testing, send `/start` and tap **🔊 1H Volume ≥120% MA20: OFF**.
It arms a one-shot alert at hourly close for volume at least 20% above the
preceding 20 completed H1 trading candles' average. It excludes the candidate
and forming bars. Existing static menus need refreshing after state changes.

## Evidence and rollback

Machine-local evidence:

- VM `/opt/xauusd_telegram_bot/deployment-20261020-volume.json`.
- VM `/opt/xauusd-bot-staging-20261020-volume-lf/`: verified manifest, successful
  preflight result/gate, failed-probe inspections, rollout and health JSON.
- Local ignored `.chart-artifacts/deployment-20261020-volume/`: scripts, archives
  and evidence bundle. Raw/private evidence is not uploaded to GitHub.

Root-private backup `/opt/xauusd-backups/20261020-volume` contains prior code,
manifests, service configuration, cutover private-state snapshots and a rollback
script. Backup code/script and private-snapshot hashes were verified at the
final health check. To restore the prior deployed code:

```bash
sudo python3 /opt/xauusd-backups/20261020-volume/rollback.py
```

Rollback preserves **current** `.env` and alerts, restores the previous modules
and manifests, removes the added H1 module and restarts the sole service. It
restores the former manual-chart cooldown too. No rollback was executed.
Reverting Git history is separate: revert merge `06b1490` with mainline parent 1
through the reviewed PR process, then deploy the intended reviewed revision.
No history rewrite is needed.

One-shot state still consumes before sending and has no durable outbox/retry.
Long-run memory, receive-polling reliability, device/manual-photo outcomes and
a continuous OANDA-session soak remain open. Paused experiments remain paused.
