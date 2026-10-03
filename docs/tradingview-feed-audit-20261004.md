# TradingView BTC reliability audit

The corrected isolated `BINANCE:BTCUSDT` test was stopped at the user's request
on 4 October 2026 at 00:34:55 IST. Its service was healthy immediately before
stopping and inactive afterward, with service success and zero restarts. The
BTC monitoring heartbeat is paused, the OANDA experiment remains stopped, and
no automatic restart is scheduled. Production was active with zero restarts.

| Metric | Observed result |
| --- | --- |
| Observed window | 6.975 hours |
| Instrumented history requests | 39/39 successful (31 at 15m, 8 at 1h) |
| Request median / p95 / maximum | 0.49 / 1.34 / 1.52 seconds |
| Due finalized closes | 28/28 at 15m, 7/7 at 1h |
| Missing / later than 60s / integrity issues | 0 / 0 / 0 |
| 15m delivery average / maximum | 10.54 / 10.81 seconds after close |
| 1h delivery average / maximum | 11.09 / 11.78 seconds after close |
| Independent Binance finalized OHLCV | 35/35 matched, zero mismatches |
| Historical replays deduplicated | 2,148 |
| Startup and after-two-hour recovery suites | Both PASS |
| Process starts / automatic restarts | 1 / 0 |
| RSS samples / sampled peak | 420 / 78.65 MB |
| Last sampled RSS / warmed growth | 54.17 / -17.86 MB |
| Cgroup pressure / OOM / OOM kills | All zero |
| SQLite integrity check | ok |

Each recovery suite exercised socket abort/resubscription, injected retry
exhaustion and fresh polling-client recovery. The 39 requests include instrumented
history recovery calls. Four additional stream clients were used for fault
exercises; protocol messages/reconnect attempts were not comprehensively counted.
Two Binance REST calls were made for the independent comparison. Preflight and
the discarded run are excluded from the table.

The initial run exposed a harness scheduler defect causing artificial 65-second
delivery delays. Its evidence was retained separately. The corrected harness
anchors wakeup to the last processed boundary plus ten seconds; regressions cover
all 120 closes in a simulated full day. The measured run ended early by request,
so the original 24-hour/120-close acceptance target was not completed. This is
recorded as intentional curtailment, not a provider failure.

The measured BTC polling/recovery path was healthy. It does not qualify OANDA
gold market sessions, reopening behavior, maximum throughput, continuous
streaming or integrated bot memory. `tvkit==0.18.0` requires Python >=3.11;
the experiment used a separate Python 3.12 runtime, while production uses 3.10.
The user subsequently explicitly authorized a repository PR and merge. The
integration is optional and disabled by default, with the existing fallback
chain retained and no VM deployment or activation performed by that merge.

Original summary/state, journal, SQLite backup, events, memory/termination
evidence and Binance references are preserved in the local experiment audit
folder and on the experiment VM. Archive SHA-256:
`d9c3f5d4707890ad7d2927a95fce1c3a96d8f617bf8e32b07f8c826fa49c8443`.
The original summary still says RUNNING because manual TERM does not finalize
the harness; a separate run note records the intentional stop. Sandboxed
stop-post systemctl was unavailable, so host status and saved cgroup counters
were used. The termination cgroup peak was lower than sampled process RSS and
was not treated as a reconciled lifetime maximum.
