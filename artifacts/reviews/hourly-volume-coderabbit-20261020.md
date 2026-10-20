Notice: Detected codex environment. Use `coderabbit review --agent` for structured agent-friendly output.
Connecting to CodeRabbit... 4s elapsed
Preparing review... 9s elapsed
Preparing review... 1m 04s elapsed - still working
────────────────────────────────────────
CodeRabbit Review

Diff      : committed changes only
Compare   : feature/hourly-volume-alert → origin/main
Directory : bot_agy
────────────────────────────────────────

(\(\
(• .•)  Winning the war on bugs, one line at a time.

Summarizing changes... 1m 14s elapsed - still working
Writing review comments... 1m 24s elapsed - still working
Writing review comments... 2m 04s elapsed - still working

────────────────────────────────────────────────────────────────────────
  minor [Maintainability & Code Quality]
  → ]8;;vscode://file/D:\projects\bot_agy\README.md:100README.md:100-101]8;;

  Remove the branch status from the README feature description.

  Lines 100-101 say the feature is "on feature/hourly-volume-alert,
  pending user testing and merge". Line 21 adds "(feature branch)" for the
  same purpose. README.md owns the current architecture and commands. Once
  this PR merges, both statements are wrong. Line 209 already covers the
  production limit, and progress.md already records the branch and testing
  state. Describe the behavior here and refer readers to progress.md for
  status.


  📝 Proposed wording

  -  The evaluated candle is excluded; small grazes are ignored. This feature is
  -  on `feature/hourly-volume-alert`, pending user testing and merge.
  +  The evaluated candle is excluded; small grazes are ignored. See
  +  [progress](progress.md) for testing/deployment status.

  As per coding guidelines: "README.md owns current
  architecture/setup/commands; … progress.md owns dated operational
  state."


────────────────────────────────────────────────────────────────────────
  minor [Maintainability & Code Quality]
  → ]8;;vscode://file/D:\projects\bot_agy\AGENTS.md:72AGENTS.md:72]8;;

  Move branch status out of the durable invariant.

  Line 72 mixes a durable rule with dated branch state: "branch-only", "User
  testing must precede merge", and "no deployment is authorized". AGENTS.md
  owns contributor rules and boundaries. progress.md owns dated
  operational state. After merge, this invariant becomes a stale claim in a
  file that every session reads first. Lines 57-59 have the same problem
  with "This branch adds…" and "current user instruction: branch testing
  only". Keep the rule here, and keep branch, merge and deployment status in
  progress.md only.


  📝 Proposed wording

  -- **Hourly volume**: branch-only opt-in one-shot alarm at closed H1 volume ≥1.20× the preceding 20 closed OANDA H1 bars' mean, excluding the candidate. Ignore forming/pre-arm/pre-restart/equal/older closes; no fallback volume. Keep its state/scheduler separate from price/momentum; failed data retains arming. User testing must precede merge; no deployment is authorized.
  +- **Hourly volume**: opt-in one-shot alarm at closed H1 volume ≥1.20× the preceding 20 closed OANDA H1 bars' mean, excluding the candidate. Ignore forming/pre-arm/pre-restart/equal/older closes; no fallback volume. Keep its state/scheduler separate from price/momentum; failed data retains arming. Consult `progress.md` for merge/deployment status and authorization.

  As per coding guidelines: "AGENTS.md owns contributor rules and
  boundaries; … progress.md owns dated operational state, validation
  limits, evidence, and next actions."


────────────────────────────────────────
Review complete
Review completed
2 findings ✔

Minor    2

12 files reviewed:
  - .github/workflows/xauusd-checks.yml
  - AGENTS.md
  - README.md
  - alerts.py
  - bot.py
  - docs/hourly-volume-validation-20261020.md
  - experience.md
  - hourly_volume.py
  - progress.md
  - skills.md
  ... and 2 more files
────────────────────────────────────────

Print all AI prompts: coderabbit review --show-prompts
