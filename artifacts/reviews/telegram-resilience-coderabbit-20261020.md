────────────────────────────────────────
CodeRabbit Review

Diff      : committed changes only
Compare   : fix/telegram-resilience-20261020 → origin/main
Directory : bot_agy
────────────────────────────────────────

(\(\
(• .•)  Will debug for carrots.


────────────────────────────────────────────────────────────────────────
  minor [Maintainability & Code Quality]
  → ]8;;vscode://file/D:\projects\bot_agy\progress.md:7progress.md:7]8;;

  Fix the mojibake in the text.

  The text contains corrupted characters, for example â‰¥120%, 3Ã—15m
  and 92â€“96%. They come from a UTF-8 to Windows-1252 re-encoding.
  Restore the characters ≥, × and –. The history snapshot file shows
  the correct form.


────────────────────────────────────────────────────────────────────────
  minor [Stability & Availability]
  → ]8;;vscode://file/D:\projects\bot_agy\manual_charts.py:17manual_charts.py:17]8;;

  Do not block the busy reply on Telegram.

  request_chart awaits _send_notice for the "already being prepared"
  reply. When Telegram is unreachable, this call can take up to about 11
  seconds: two 5-second attempts plus one 1-second sleep. The callback
  handler is held for that time. Send the reply in a background task, or use
  a single attempt.


────────────────────────────────────────────────────────────────────────
  minor [Functional Correctness]
  → ]8;;vscode://file/D:\projects\bot_agy\manual_charts.py:46manual_charts.py:46-54]8;;

  Handle the case where both attempts fail.

  _capture_hourly retries only HourlyHistoryUnavailable and
  asyncio.TimeoutError. capture_hourly_chart calls fetch_tradingview.
  That function catches all exceptions and returns empty lists, so provider
  errors surface as HourlyHistoryUnavailable. This is correct.

  Each attempt can take up to 25 seconds. Two attempts plus a 1-second sleep
  can reach 51 seconds. This fits the 55-second budget but leaves little
  margin for rendering. The outer wait_for(..., timeout=55) then cancels
  the render. The user sees the "temporarily unavailable" message although
  the data was fine. Raise the budget or retry only when time remains.


────────────────────────────────────────
Review complete
Review completed
3 findings ✔

Minor    3

12 files reviewed:
  - README.md
  - bot.py
  - docs/progress-history/2026-10-20-033100-before-telegram-resilience.md
  - docs/progress-history/README.md
  - docs/warp-disabled-validation-20261020.md
  - experience.md
  - hourly_chart.py
  - manual_charts.py
  - progress.md
  - skills.md
  ... and 2 more files
────────────────────────────────────────

Print all AI prompts: coderabbit review --show-prompts


## Resolution

Reviewed commit: `043e549`. All three minor findings addressed: restored UTF-8 progress text, moved busy replies to tracked background tasks, and retained the 55s total capture budget with a truthful fetching/rendering timeout message rather than attributing every timeout to missing provider data. Added regression coverage; provider-only retries remain bounded and photos are never automatically resent.
