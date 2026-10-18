# Checkout reconciliation — 18 October 2026

Scope: repository ownership, preservation, documentation and local Git cleanup.
Runtime source, dependencies, alert rules, production and paused experiments are
unchanged. GitHub main at `a0f0a2044a7966efe232b7f66070a106007a777a` was fetched
and used as the reviewed starting point. This is not a runtime health check.

## Preservation and recovery

- Private, owner/SYSTEM-restricted recovery directory:
  `C:/Users/Lenovo/.codex/recovery/bot_agy-20261018-210819-8055`.
- SHA256-verified copies of **304 files** from both checkouts, including private
  environment/state and local experiment evidence. The all-ref Git bundle was
  verified with `git bundle verify`. Original statuses, branch refs and separate
  staged/unstaged binary patches are retained. Virtual environments and caches
  were explicitly excluded as reproducible; `.git` is represented by refs/bundle.
- Former primary files displaced by current source are additionally retained in
  `relocated-primary/`. Private values were never printed or added to this change.
- `manifest.json`, `RESTORE.md` and `pruned-local-branches.json` are private recovery
  records. Restore the old HEAD from the bundle in a **separate checkout**, recover
  original files and deletion/staging intent there, and compare before selectively
  reapplying anything. Do not replace current alerts/environment or source blindly.
- Existing histories were not overwritten. Primary-only and divergent snapshots
  are retained verbatim in [primary history](../../docs/progress-history/primary-preserved-20261018/README.md)
  and `docs/legacy/primary-preserved-20261018/`. The prior merged handoff was
  archived before compaction. Historical relative links retain their original
  project-root interpretation and may refer to machine-local evidence.

## Local reconciliation

- Original primary HEAD: `ce197ca227efe6b48e501d24457d1ac147cdd5c8`, 14 commits
  behind reviewed main, with 58 tracked deletions and older local bot files.
- Verified primary file hashes and status still matched recovery before mutation.
  A temporary recovery branch isolated the dirty state; after relocating conflicting
  files, restoring that branch and switching back, primary main fast-forwarded to
  `a0f0a20`. Old edits/deletion intent remain in recovery rather than being replayed
  over the complete implementation. No reset, force push or remote branch deletion.
- Primary `.env`, `data/alerts.json`, `.experiments/`, `archives/`, `archive.md` and
  logs remain locally available. Private historical artifacts use targeted local
  `.git/info/exclude` entries; raw experiments are also covered by `.gitignore`.
- The complete detached `D:/projects/bot_agy-no-chart-delay` worktree was preserved
  through reconciliation, then retired after every tracked file matched primary through Git's normal
  Windows line-ending normalization,
  all non-cache files matched recovery, and no active T3 run depended on it. Only
  the primary and this temporary review worktree remain until integration cleanup.

## Branch decisions

Immediately before removal, each candidate was checked as an ancestor of primary
main and absent from active worktree branch references. Eight original local
branches were removed; all their refs/objects remain in the private bundle:

- `archive/manual-hourly-charts-20261013-20261015` — `88226a1`.
- `archive/tradingview-oanda-feed-20261015`, `codex/agy-chart-buttons-20261013`,
  `codex/agy-on-demand-hourly-chart` — `a2a0400`.
- `codex/browserless-alert-screenshots` — `5e7c8af`.
- `codex/manual-hourly-charts-20261013` — `71686b6`.
- `codex/startup-candle-guard` — `ac42892`.
- `codex/tradingview-oanda-feed` — `b2ebe8f`.

The temporary recovery branch at `ce197ca` was also removed after fast-forward.
**Retained unique history:** `codex/wisper_smal` at `fdab008` and
`legacy-main-pre-github` at `e305f4b`. Remote branches were untouched.

## Validation and integration

Local verification on 18 October 2026, **15:42 UTC / 21:12 IST**: compilation and
all **50 active offline tests** passed using the primary Python 3.12 environment.
Primary private environment/alert hashes remain identical to recovery. Active
document links resolve; progress is below both caps; snapshot byte comparisons
and Git whitespace checks pass. Preserved evidence has targeted Git attributes
to retain original line endings. CodeRabbit/integration evidence follows after
actual observation.

Linux/WSL harness: **8/8 tests passed** (38.703s), 18 October 15:44 UTC.
The index was explicitly re-added with the preservation attributes; all copied
historical bytes were verified against the private originals before review.
The documentation establishes checkout/dependency/platform preflight instead of
repeating the original audit's unsupported `/opt/...` import diagnosis. Linux's
`resource` module belongs to the Linux soak harness; missing `telegram` requires
the matching interpreter/dependencies.

Production remains at the last recorded PR #4 deployment. PR #5 is merged but
undeployed; its no-cooldown behavior is not claimed as production behavior.
Durable delivery retry, polling and memory follow-ups remain unresolved.

CodeRabbit CLI **0.9.0**, authenticated remote review, observed **18 October
15:48:17 UTC (report reverified)**: completed against main `a0f0a20` at feature head
`bf227e0ad786bc7d8600ba245f6da7d568612e18`. **One minor finding** concerned
mojibake in this evidence file; corrected and UTF-8-checked. No substantive
findings. [Generated report](checkout-reconciliation-coderabbit-20261018.ndjson).
Later edits only record review/cleanup/integration evidence; runtime/tests are unchanged.

Review command (WSL `/tmp`, avoiding Windows Git worktree path translation):
`coderabbit review --agent --remote vanshch/Trading-Bot --base main --source-branch feature/reconcile-primary-checkout`.
