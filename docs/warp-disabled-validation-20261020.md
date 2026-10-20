# Temporary WARP disablement — 20 October 2026

The user authorized temporarily removing WARP from operation while testing basic
bot commands. `warp-svc.service` is **stopped and disabled**. The package and its
configuration remain installed for rollback. Do not automatically re-enable it;
subsequent desktop acceptance is recorded in [the resilience release report](telegram-resilience-deployment-20261020.md).

## Action and verification

- Before maintenance, WARP was enabled/running, PID 554310. It operated as a
  loopback proxy on port 40000; no bot proxy environment or WARP tunnel routes were
  found. Its memory/page-fault activity dominated the observed host stalls.
- First `systemctl disable` exceeded the diagnostic's eight-second bound. A
  fresh observation at **03:16:49.695801 UTC** confirmed disablement had taken
  effect; this timeout was not treated as a successful stop.
- Stop requested **03:16:52.261390 UTC / 08:46:52.261390 IST**. WARP did not exit
  after SIGTERM. Systemd's existing 90-second stop timeout and `SendSIGKILL=yes`
  completed termination; by **03:18:24.554912 UTC** the unit was failed/stopped,
  `MainPID=0`, disabled. No separate manual kill was needed. The failed status
  records the shutdown timeout, not a running process. `pgrep -x warp-svc` found
  no process at the end of verification.
- Last actual observation **03:18:35.281756 UTC / 08:48:35.281756 IST**: bot
  active/running, original PID **239854**, automatic restarts **0**. Bot was not
  restarted or redeployed. `.env`, `data/alerts.json` and `bot.py` remained
  byte-identical throughout maintenance; private hashes stay in local evidence.
- Post-stop available RAM: **663 MiB**, versus **97.1 MiB** immediately before
  maintenance. Swap used fell to **105.5 MiB**; no swap-in/out occurred in two
  consecutive five-second intervals. Remaining occupied swap was not forced back
  into RAM.
- Those intervals showed **0.30% / 0.10% CPU I/O wait** and **0.52% / 0.34% disk
  busy**, compared with prior **92–96% I/O wait / approximately 97% disk busy**.
  These are short observations, not sustained-load qualification. Rolling PSI
  averages retain earlier stalls and require time to decay.
- Public TLS handshakes, without bot tokens or provider requests, passed for
  `api.telegram.org` (**0.397s**) and `data.tradingview.com` (**0.120s**). These do
  not establish Telegram command handling, chart retrieval or device receipt.

The before/after result strongly supports WARP-driven paging as the immediate
host bottleneck. It does not establish a WARP memory leak or explain every prior
network error. No code change, package uninstall, swap reset or paused experiment
restart was performed. Alert-delivery retry and callback weaknesses remain open.

## User acceptance and rollback

Try `/start`, `/status`, `/candle`, `/chart1h` and `/screenshot`; check response
latency and device receipt. These command/chart paths were subsequently verified
on Telegram Desktop after PR #11 deployment; see the release report. Separate
phone and live qualifying-alert receipt remain unconfirmed. An hourly-volume alarm had switched OFF after processing the
03:00 UTC close before this maintenance; its device receipt remains unconfirmed.

Only when the user requests restoration:

```sh
sudo systemctl enable --now warp-svc.service
```

Then verify WARP/proxy state, the bot, host paging and latency again. Restoration
may reintroduce pressure. This is separate from the application rollback in
[the deployment report](hourly-volume-deployment-20261020.md).

## Local private evidence

- Investigation: `logs/host-stalls-findings-20261020.md`, with its raw-evidence index.
- First action attempt: `logs/host-warp-disabled-20261020T031535Z.log`.
- Disablement/stop request: `logs/host-warp-disabled-retry-20261020T031636Z.log`.
- Final termination, post-stop counters, TLS and preservation checks:
  `logs/host-warp-stopped-20261020T031749Z.log`.

Raw evidence is ignored/local and must not be uploaded. Reviewed runtime source
remains release `06b1490`; all nine modules/two manifests matched GitHub main
`273ab5c` in the preceding 03:02 UTC audit. Local documentation changes do not
alter that runtime or establish newer runtime observations.
