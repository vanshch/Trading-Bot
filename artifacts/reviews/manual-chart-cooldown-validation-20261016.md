# Manual chart cooldown removal

Base: `88226a1` (main). Local verification: 16 October 2026, 03:43 IST.

- Removed the 30-second manual chart cooldown. Screenshot and hourly commands/buttons share only the existing in-flight reservation.
- Regression checks exercise immediate screenshot repeats and screenshot/hourly switching after actual mocked delivery, plus overlapping-request rejection.
- All 50 active offline tests passed: alerts 8, screenshots 9, scheduler 7, TradingView 12, manual charts 14.
- Runtime modules and chart tools compile successfully.
- `ruff check --isolated --select E4,E7,E9,F manual_charts.py tests/test_manual_charts.py` passed.
- Inherited broader Ruff configuration reports eight existing findings, identical to baseline. Formatter checks already fail on both baseline files; formatting changes were kept outside this behavioral fix.
- README, AGENTS, skills, experience and progress reviewed; affected guidance updated. No provider calls, Telegram messages, production restart or deployment performed.

CodeRabbit CLI 0.9.0 is installed under WSL. Review requires user OAuth sign-in; no AI review result is claimed until the authenticated review finishes.
