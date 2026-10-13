# PR #4 production deployment — 13 October 2026

[PR #4](https://github.com/vanshch/Trading-Bot/pull/4) merged into main at
**04:48:10 UTC / 10:18:10 IST**, commit
`05527f7857219432074deb5a6377e0b0009322e0`. All three GitHub Actions checks passed
on head `71686b6d3deb8334c3bde5dad8d0319d46da1e28`, run `38348022918`.
CodeRabbit reported a skipped review; no independent review is claimed.
The user authorized merge and making the feature available for their Telegram test.

## Preflight and cutover

- Read-only observation **04:49:44.765041 UTC** confirmed the previous source
  matched `a2a0400`, service active, PID `1749355`, no restarts/OOM, flags on,
  15m timeframe and Browserless configuration present. Saved state remained
  resistance **#3 $4,318.00**, both momentum settings off, next ID **9**.
- Ten files (eight runtime modules and two dependency manifests) were staged with
  exact release hashes. The production Python **3.12.15**, tvkit **0.18.0**,
  python-telegram-bot **22.8** and httpx **0.28.1** were retained.
- **04:52:03.290317 UTC / 10:22:03.290317 IST:** isolated service-user cgroup
  preflight passed with `MemoryHigh=90M`, `MemoryMax=120M`: a live verified H1
  fetch, exactly 20 candles including the 04:00 UTC forming bar, both keyboard
  callbacks and a **45,987-byte PNG**, with a synthetic maximum screenshot
  payload resident. Peak RSS **103,616,512 bytes**; cgroup peak **94,543,872 bytes**.
  Events: **high=110, max=0, oom=0, oom_kill=0**. This demonstrates this bounded
  target-runtime run, not long-run or simultaneous live-provider qualification.
- Pillow **12.3.0** was installed into an isolated dependency directory
  `/opt/xauusd_telegram_bot/chart-deps-20261013-044810`, loaded by
  `/etc/systemd/system/xauusd-bot.service.d/30-chart-deps-20261013.conf`
  (`Environment=PYTHONPATH=...`). Existing venv packages/runtime overrides were
  retained. Direct Python maintenance commands need the same `PYTHONPATH`.
- Service started **04:54:20 UTC / 10:24:20 IST**. Cutover verification completed
  **04:54:26.581714 UTC / 10:24:26.581714 IST**. All ten hashes matched; private
  `.env` and current `data/alerts.json` were byte-for-byte unchanged. Alert-state
  SHA256: `11928638f4ed583d184a31e6db31b77ce20d0a9260229dffdb1b988fc7c5b2ee`.

## Health verification and remaining test

At **04:54:52.834855 UTC / 10:24:52.834855 IST**, the service was active/running,
PID **3242675**, automatic restarts **0**, memory **82,190,336 bytes**, peak
**82,739,200 bytes**, cgroup **high/max/oom/oom_kill all 0**. Source/manifests and
saved settings still matched. Logs confirm OANDA startup history, scheduler
startup and the normal startup greeting with the new keyboard; zero tracebacks
or requested-chart failures in this startup window.

The bot's normal startup message was sent by its sole production polling process.
No manual test photo, additional poller, new Browserless capture or paused
experiment was started. The user will test `/start` and the two buttons themselves.
Telegram device receipt/manual screenshot and H1 photo delivery remain **pending**;
service startup is not proof of those outcomes. The 15m alert schedule and automatic
text-first chart behavior are preserved. A 30-second cooldown is shared by both
manual buttons, and one manual capture may run at a time.

Polling `httpx.ReadError`, memory pressure, durable delivery retries, continuous
market-session qualification and the paused BTC/OANDA studies remain unresolved
as recorded in progress/history.

## Evidence and rollback

VM metadata: `/opt/xauusd_telegram_bot/deployment-20261013.json`. Staged evidence:
`/opt/xauusd-bot-staging-20261013-044810/{preflight-result,rollout-result,health-result}.json`.
Copies, preflight PNG, release tar/manifest and scripts are preserved in the local
ignored `.chart-artifacts/deployment-20261013/` directory of the merged worktree.
Raw logs/private environment values are not published in repository documentation.

Root-private backup: `/opt/xauusd-backups/20261013-pr4-044810`, containing prior code,
manifests/service definition, cutover alert snapshot, private-env snapshot and
rollback metadata/script. Roll back with:

```bash
sudo python3 /opt/xauusd-backups/20261013-pr4-044810/rollback.py
```

This restores prior modules/manifests and the chart dependency drop-in, preserving
the **current** alerts and private environment. It retains the isolated dependency
directory as an unused artifact. No rollback was needed. The older 4 October
backup remains historical and available independently.
