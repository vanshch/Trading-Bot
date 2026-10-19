# Hourly high-volume alarm: branch testing

Branch: `feature/hourly-volume-alert`, based on reviewed main
`0aa62c353c603e0ea9061e3f0ff00519931a164d` (merged PR #8).
User explicitly requires testing before merge. No merge, deployment, production
restart, live provider request, Telegram test send or experiment resume occurred.

## Rule and interface

- `/start` exposes **🔊 1H Volume ≥120% MA20: OFF/ON**; `/volume1h` toggles it too.
- Default OFF; one-shot. Either candle color qualifies.
- At hourly close, compare raw verified OANDA XAUUSD tick volume with **1.20 ×
  the arithmetic mean of the preceding 20 completed H1 trading candles**.
  Exclude the candidate from the average. This is deliberately different from
  a TradingView SMA that includes the evaluated bar.
- Example: baseline 20,000 ticks; 20,001 and 23,999 do not qualify; 24,000 qualifies.
  The 20% margin was selected by the user on 20 October 2026.
- Ignore forming bars. No preliminary intrabar cross notification is sent.
- Use the existing verified TradingView adapter directly, with 21 closed bars;
  optional tvkit/Python 3.11+ is required. The explicit alarm works independently
  of `TRADINGVIEW_FEED_ENABLED`; no fallback volume is accepted.
- Check at UTC hourly boundaries plus 10 seconds. Arming mid-hour allows that
  hour's eventual close. State includes arming time and a processed-close
  watermark; an independent process-start cutoff prevents historical replay.
- Missing/invalid/zero-baseline data leaves it armed until the next hourly check.
  No catch-up/backfill is attempted. Weekend/session gaps are allowed in the
  20-trading-bar baseline; native bar starts are retained.
- The independent task changes neither the primary monitoring timeframe nor
  price/momentum settings. Triggered text reports the H1 closing timestamp,
  volume, baseline and excess percentage. It sends no additional photo.

## Offline validation

Observed **2026-10-19 22:26:42 UTC / 20 October 03:56:42 IST** on Windows, Python 3.12.0.
The selected `.venv` satisfies `requirements.txt`: python-telegram-bot 22.8,
httpx 0.28.1, python-dotenv 1.2.2, Pillow 12.3.0.

- Compilation of all nine runtime modules and existing preview/delivery tools passed.
- All **70** active offline tests passed: 50 existing plus **20** volume tests.
  New coverage includes exact 20% equality, grazes, rolling baseline, large volume,
  native/session gaps, malformed/short/forming/mixed-source history, disabled
  no-fetch behavior, persistence/migration/corruption, one-shot completion,
  re-arm/restart/replay suppression, in-flight OFF/re-arm, failed state writes,
  single-user authorization, independent timeframe and scheduler recovery/cancel.
- Ruff 0.16.3 lint and format checks pass for the two new Python files.
  Existing modified modules have no new Ruff findings compared with main
  (alerts 19→18, bot 6→5, scheduler tests 4→4). They retain broad lint/format debt; no repository-wide
  clean-lint claim is made. CI includes the new suite on Python 3.10/3.12.
- Following a lint-driven exception-type correction, 28 alert/volume tests
  passed again at **19 October 22:30:25 UTC / 20 October 04:00:25 IST**.
- CodeRabbit CLI 0.9.0 reviewed commit `08bf97e` against `origin/main`: no code
  findings; two minor documentation ownership findings, both resolved by moving
  branch/testing status to progress. [Generated report](../artifacts/reviews/hourly-volume-coderabbit-20261020.md).
  The later code correction only changes invalid JSON root classification from
  ValueError to TypeError; its 28-test rerun is recorded above. Draft [PR #9](https://github.com/vanshch/Trading-Bot/pull/9) is linked to the task;
  hosted CI is pending.

Reproduce the new suite without contacting providers or Telegram:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p test_hourly_volume.py
```

## Isolated live acceptance — pending

1. Use a separate clone/worktree of this branch with its **own `.env`, test bot
   token and `data/`**. Install `requirements-tradingview.txt` using Python 3.11+.
   Set your authorized Telegram user ID. Never run a second poller using the
   production token. No production service/config changes are needed for this test.
2. Run `python bot.py` in that isolated environment. Send `/start`; check the new
   button defaults OFF. Tap it; confirm ON and the 20% rule. `/status` and `/alerts`
   should show the alarm while the price/momentum timeframe stays unchanged.
3. During a gold trading session, independently collect 21 verified closed OANDA
   H1 candles and calculate candidate volume / previous-20 mean. Compare full
   numeric volumes, not rounded chart labels or a current-inclusive SMA line.
4. Confirm no forming/grazing alert. At a qualifying close, expect one message
   shortly after the hourly check; record candle close, ratio, Telegram acceptance
   and device receipt separately. Confirm the fresh menu/status is OFF afterward.
5. Toggle OFF to cancel; re-arm and restart to confirm old closes are not replayed.
   Disable/uninstall the optional provider in the test environment to confirm
   failed data does not consume the alarm or stop existing alerts.

## Limits and rollback

Live market delivery, device receipt, production memory/latency and a sustained
session soak remain unverified. Simultaneous 15m/H1 provider requests need combined
Linux memory qualification before any future VM release. The current VM's last
verified production state is still the 13 October release recorded in progress.
One-shot state is consumed before sending, matching the existing momentum path;
a failed send has no durable retry and requires re-arming. Existing Telegram
menus are static messages; send `/start` or `/status` to refresh displayed state.

To stop an isolated test, cancel the alarm or stop its test process. Production
needs no rollback because it was untouched. Keep the branch/draft PR unmerged
until the user accepts testing; production deployment requires separate authority.
