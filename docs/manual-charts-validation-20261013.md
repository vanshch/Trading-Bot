# Manual chart validation — 13 October 2026

Branch `codex/manual-hourly-charts-20261013` is based on deployed/main
`a2a0400124e6f29b17d8b279dfdb1c6517e3c851`. These results describe local validation,
not deployment or Telegram device receipt.

## Behavior

- **📸 Screenshot Now** (`/screenshot`) captures the existing saved Browserless
  TradingView layout at the active alert timeframe. Explicit requests require
  existing credentials and work independently of automatic-alert feature flags.
- **🕐 1H Chart + Volume** (`/chart1h`) fetches verified raw `OANDA:XAUUSD` through
  tvkit, retaining native bar starts and tick volume. Exactly 20 latest bars are
  shown, including a labeled forming bar when present. There is no fallback
  provider/offset for this OANDA chart; insufficient history fails visibly.
- Pillow renders a 1296×800 dark PNG with teal/red candles/wicks, volume bars,
  OHLC/change, dotted grid, right scale/price badge and UTC timestamps. Quotes,
  branding, saved drawings and live countdowns are not fabricated.
- Authorized-only commands spawn application background tasks, share a busy
  reservation and 30-second cooldown, and release the reservation after failure
  or cancellation. The 15m scheduler, saved alerts and feed diagnostics remain
  unchanged. Capture deadlines are 60 seconds for screenshots/35 seconds for H1;
  photo delivery is capped at 20 seconds.

## Verification

**Windows Python 3.12.0:** 49 active bot checks passed (36 existing + 13 new), plus
compilation. The Linux harness requires `resource` and cannot run on Windows.
**WSL Ubuntu 24.04 / Python 3.12.3:** all 49 active checks plus eight Linux harness
checks and compilation passed, completed approximately **04:41:44 UTC**.
New coverage exercises volume/native starts, forming/closed selection, short and
invalid history, flat/zero-volume charts, PNG size/colors, auth/callback routing,
busy/cooldown, timeout, photo failure, cancellation, log redaction and unchanged
alert timeframe/feed state.

**Live tvkit 0.18.0:** local H1 requests completed **04:36:29 UTC** and
**04:38:21 UTC**, each returning 60 bars with verified OANDA identity. Exactly 20
were selected; the latter includes the **2026-10-13 04:00 UTC** forming bar. Both
images were visually inspected against the supplied reference. Final Windows PNG
**43,454 bytes**, same cached data rendered on Linux **43,451 bytes**. Dependencies:
Pillow **12.3.0**, python-telegram-bot **22.8**, httpx **0.28.1**.
No Telegram sends, production-token poller, production changes or new Browserless
capture occurred. Its unchanged provider has nine passing offline checks and
previously recorded live qualification.

**Local Linux synthetic memory overlap:** importing bot/tvkit and rendering cached
verified candles while holding maximum 5 MiB decoded/base64/JSON screenshot
representations peaked at **104,919,040 bytes (100.06 MiB)**; import baseline peak
**71,757,824 bytes (68.43 MiB)**. Below 120 MiB, above the 90 MiB high threshold;
this does not qualify production cgroup headroom or long-run behavior.

Machine-local ignored evidence is in
`C:/Users/Lenovo/.codex/worktrees/manual-hourly-charts-20261013/bot_agy/.chart-artifacts/`:
`hourly-preview.png`, `hourly-preview-linux.png`, `hourly-candles.json`,
`validate_linux.py`, `memory_probe.py`. Reproduce PNG/data with the README tool.

## Integration limits

Unmerged/undeployed at report creation. Deploy eight runtime modules and matching
requirements together, preserve `.env`/current alerts, and preflight combined
features on the target runtime/cgroup before an authorized restart. Verify actual
Telegram receipt, buttons, hashes, preserved settings and memory/OOM counters.
Paused experiments remain paused; hourly monitoring/volume rules are out of scope.
