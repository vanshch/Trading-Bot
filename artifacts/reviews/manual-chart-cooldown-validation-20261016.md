# Manual chart cooldown removal

Base: `88226a1` (main). Local verification: 16 October 2026, 03:43 IST.

- Removed the 30-second manual chart cooldown. Screenshot and hourly commands/buttons share only the existing in-flight reservation.
- Regression checks exercise immediate screenshot repeats and screenshot/hourly switching after actual mocked delivery, plus overlapping-request rejection.
- All 50 active offline tests passed: alerts 8, screenshots 9, scheduler 7, TradingView 12, manual charts 14.
- Runtime modules and chart tools compile successfully.
- `ruff check --isolated --select E4,E7,E9,F manual_charts.py tests/test_manual_charts.py` passed.
- Inherited broader Ruff configuration reports eight existing findings, identical to baseline. Formatter checks already fail on both baseline files; formatting changes were kept outside this behavioral fix.
- README, AGENTS, skills, experience and progress reviewed; affected guidance updated. No provider calls, Telegram messages, production restart or deployment performed.

Implementation commit: `21e36a1e61914bfc3ff518afae6349df1e8a2371`.
[Draft PR #5](https://github.com/vanshch/Trading-Bot/pull/5).
[CI 38815878411](https://github.com/vanshch/Trading-Bot/actions/runs/38815878411) passed
both Python 3.10/3.12 offline jobs and optional-provider/harness checks; observed
16 October 2026, 03:46 IST. Subsequent documentation commits need their own CI result.

CodeRabbit CLI 0.9.0 is installed under WSL. The attempted agent review of the
remote feature branch stopped at OAuth sign-in without producing findings.
[Authentication diagnostic](manual-chart-cooldown-auth-status-20261016.txt).
The original sign-in session remains pending for the user. GitHub CodeRabbit
reported “Review skipped: draft pull request”; that is not a completed review.
Keep the PR draft and unmerged until authentication/review completes. No AI review
result is claimed.
