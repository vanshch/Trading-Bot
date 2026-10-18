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
[PR #5](https://github.com/vanshch/Trading-Bot/pull/5).
[CI 38815878411](https://github.com/vanshch/Trading-Bot/actions/runs/38815878411) passed
both Python 3.10/3.12 offline jobs and optional-provider/harness checks; observed
16 October 2026, 03:46 IST. Subsequent documentation commits need their own CI result.

After successful user OAuth authentication, CodeRabbit CLI **0.9.0** completed
one authenticated remote review with **zero findings**, observed 16 October 2026,
**15:04 IST**. Reviewed head `38749710cd9ea170805558cd51e79c02d6287c7a`
against main `88226a14d1c9be87732a9652438a53cd4b1fc8e1`. Runtime/test changes
have not changed since that review; subsequent edits reconcile documentation and
preserve this evidence. [Generated agent report](manual-chart-cooldown-coderabbit-20261016.ndjson).

Command (run from WSL `/tmp` to avoid Windows worktree Git path translation):
`coderabbit review --agent --remote vanshch/Trading-Bot --base main --source-branch feature/remove-manual-chart-cooldown`.

[CI 38816370798](https://github.com/vanshch/Trading-Bot/actions/runs/38816370798)
also passed all three jobs on reviewed head `3874971`.
[Final CI 38881513484](https://github.com/vanshch/Trading-Bot/actions/runs/38881513484)
passed all three jobs on final head `9d69a6056341cdebee54c338ac1c97f8a4794cb8`.
The GitHub CodeRabbit check skipped OSS review; the authenticated CLI result above
is the substantive review. PR #5 merged **16 October 2026, 09:37:32 UTC / 15:07:32 IST**
as `9b730d0f9f576cf1c9db1e1b562d48ff945df591`. Feature branches were deleted;
the complete worktree was synchronized to the merged source. No deployment is part
of this change. README/AGENTS/skills/experience/progress reviewed at closeout;
only operational evidence needed further edits.

Historical only: the first OAuth attempt timed out, and GitHub initially skipped
the draft PR. The [pre-login authentication diagnostic](https://github.com/vanshch/Trading-Bot/blob/7c50d4bf2573659d90568cbb3dc617a6d849e7b4/artifacts/reviews/manual-chart-cooldown-auth-status-20261016.txt)
is preserved in Git history; its transient local copy is now untracked. Neither
historical event is a current review blocker.
