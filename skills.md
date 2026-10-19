# Project workflows and capabilities

This is a reference map for the current XAU/USD candle alert bot, not an installed Codex skill. Read [README.md](README.md) for architecture/setup, [AGENTS.md](AGENTS.md) for contributor rules, [experience.md](experience.md) for durable lessons, and [progress.md](progress.md) for dated deployment state and validation limits. Update affected guidance at substantial milestones and PR closeout as required by AGENTS.

## Market data and candle timing
- In the tracked implementation, `feed.py` orchestrates optional verified TradingView OANDA history (`tradingview_feed.py`), Twelve Data spot, and Yahoo gold-futures fallback.
- TradingView uses `tvkit==0.18.0` on Python 3.11+; base operation supports Python 3.10. Repository flags default off; recorded production enables TradingView and charts.
- Support 5m, 15m, 1h, and 4h; production selects 15m. Verify identity, OHLCV shape, timestamps, session alignment, forming/closed separation, startup seeding, and replay suppression. The TradingView request is 60 bars with a bounded 25-second deadline.
- The approximate -0.50 offset affects Twelve Data only. OANDA tick volume and futures contract volume have different meanings. The deployed adapter retains volume/native starts for on-demand H1 images; production still schedules 15m alerts. `hourly_volume.py` implements an independent opt-in H1 alarm at ≥120% of the previous 20 closed trading bars' average, excluding the candidate and forming bars. No fallback volume; consult progress for testing/merge/deployment status.

## Alert engine and persistence
- `alerts.py` handles wick rejection and body breakout followed by immediate opposite-candle confirmation; an unconfirmed breached level is invalidated silently.
- Fresh two-green/two-red alerts are one-shot. Test ongoing-streak suppression, timestamp deduplication, and startup/provider replay boundaries.
- `config.py` manages runtime configuration; `alerts.py` manages atomic `data/alerts.json` persistence with safe default recovery, including volume arming time/processed-close watermark. A process-start cutoff prevents restart replay. One-shot consumption occurs before send, without a durable outbox.

## Telegram and optional charts
- `bot.py` owns async commands, callbacks, the single-user gate, scheduler, and text-first dispatch.
- `screenshots.py` captures Browserless saved-layout charts remotely; one photo per alerted candle, no quiet-candle capture, and bounded failure handling. `test_chart_delivery.py` is an explicit one-shot capture/device-test tool, not a second polling bot.
- `manual_charts.py` runs explicit screenshot/hourly requests as bounded background tasks with shared busy protection and no timed cooldown. `hourly_chart.py` renders exactly 20 verified raw OANDA hourly candles and tick volume with Pillow, including a labeled forming bar when available. `tools/preview_hourly_chart.py` saves PNG/data evidence without polling or Telegram. Automatic-alert flags do not suppress explicit requests; credentials/optional tvkit remain required.
- Verify actual parse mode, escaped dynamic text, payload limits, and credential-safe logs. Live device tests consume provider resources and send messages only when explicitly requested.

## Validation and operations
- Use the named active offline suites and compilation commands in AGENTS from the primary checkout or a clean feature worktree based on reviewed main. Run the resource/soak harness on Linux/WSL, not Windows.
- CI checks the active bot on Python 3.10/3.12 plus optional tvkit and Linux harness regressions. `tools/tradingview_soak/` preserves the corrected BTC harness. Experiment services/follow-ups remain stopped; do not restart them implicitly.
- GCE/systemd operations use `xauusd-bot.service`, encoded remote commands from Windows, exact source-hash verification, state preservation, bounded memory, and evidence-backed rollback.
- Distinguish code availability, feature flags, deployment, service health, and market-session validation. Consult progress before operational changes; its recorded production pause and verification limits remain authoritative until new evidence supersedes them.

- For trigger audits, reconstruct alert activation and invalidation times from service/system logs, replay the same provider candle history independently, and compare expected closes with dispatch records. Check log coverage and separate polling errors from send failures; never start a competing production-token poller.

## Historical scope
The YouTube/audio/LLM signal pipeline is disabled and no longer tracked in the current tree. Source/tests/plans and obsolete deployment recipes remain in Git history through `7c50d4b` and may exist as ignored local copies. Historical references live under `docs/legacy/` and in the primary checkout's `archives/` and `archive.md`; load them only for a relevant historical task. They are not current bot requirements. Keep the named active test patterns when local historical tests remain on disk.

## Repository reconciliation and review
- Start with Git state/ancestry and private recovery copies; CodeRabbit reviews the finished diff but cannot establish whether local-only files or unique commits are safe to remove.
- Keep one primary checkout, preserve divergent historical snapshots verbatim, and prune only verified redundant local branches. Record recovery locations and exclusions in the reconciliation evidence.
- Follow the invoked CodeRabbit pipeline after local checks, save its generated report under `artifacts/reviews/`, and distinguish review completion, CI, merge and deployment.
