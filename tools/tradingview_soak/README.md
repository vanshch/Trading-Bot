# Preserved corrected BTC harness

This is the corrected Linux-only harness from the stopped weekend BTC run,
using Python 3.11+ and `tvkit==0.18.0`. It polls anonymous BINANCE:BTCUSDT
15-minute/hourly boundaries at +10 seconds, validates/deduplicates candles,
records SQLite/events/resource samples, and exercises startup/live recovery.
The corrected run was intentionally stopped; no restart is scheduled.
See [the audit](../../docs/tradingview-feed-audit-20261004.md).

Offline regression checks (no network):

```bash
python -m unittest discover -s tools/tradingview_soak -p test_soak.py
```

For a separately authorized new isolated Linux test, use a **fresh** state directory:

```bash
python tools/tradingview_soak/soak_tvkit.py --state-dir /path/to/fresh-private-state --preflight
python tools/tradingview_soak/soak_tvkit.py --state-dir /path/to/another-fresh-private-state
```

Preflight and measurement must not reuse state: every process start is counted.
The original procedure used systemd sandboxing, no production credentials,
90 MB memory-high/120 MB hard limits and a 24h10m runtime guard. Stopping a run
requires preserving its state and separately recording the stop reason because
the harness does not finalize on TERM. Do not infer a completed acceptance pass
from a partial run or reuse an old measurement window after an intentional pause.
