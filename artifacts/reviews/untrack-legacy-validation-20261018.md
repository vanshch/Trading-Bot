# Retired-file tracking cleanup — 18 October 2026

Base: `7c50d4bf2573659d90568cbb3dc617a6d849e7b4`.
Branch: `chore/untrack-legacy-youtube`; initially committed locally as `ed31da5`.
The user subsequently authorized publication and documentation alignment.
Integration/CI status is recorded below. No deployment, live provider calls,
Telegram sends or experiment restarts are part of this change.

## Scope and preservation

Removed 56 paths from the Git index using `git rm --cached`: retired YouTube
`main.py`, `src/`, `benchmark.py`, `config.yaml`, three legacy scripts, the legacy
research document, four implementation plans, legacy tests/fixture, obsolete
Dockerfile/Cloud Build configuration, and the temporary pre-login auth diagnostic.
All local files remain at their original paths. Exact `.gitignore` entries prevent
accidental re-addition while allowing unrelated new source/test/tool paths.

The eight active runtime modules, base/optional/dev requirements, active tests,
CI, historical Markdown snapshots and substantive review reports remain tracked.
The auth diagnostic's existing reference now points to its prior Git revision.

Private verified recovery:
`C:/Users/Lenovo/.codex/recovery/bot_agy-untrack-20261018-213221-3360`.
Owner/SYSTEM-restricted directory contains copies and SHA256s for all 56 files,
original refs/status, private-state hashes and a verified all-ref Git bundle.
Restore only selected paths from that directory or recover tracked history at the
base revision; adjust the matching ignore entry deliberately before re-adding.
Do not replace current source or private state wholesale.

## Validation

Results are recorded below after actual checks. Validate a tracked-only export
without private environment or retired files so local history cannot mask missing
imports or fixture dependencies. Production state/verification times remain as
recorded in progress; this cleanup does not provide a new runtime observation.

Observed **2026-10-18T16:03:50.652931+00:00**: tracked-only compilation and full active
discovery passed, **50/50 tests**. Observed **2026-10-18T16:04:11.336457+00:00**:
tracked-only Linux/WSL harness **8/8 passed**. Export contained none of the 56
retired files and no private `.env`. All removed paths still exist locally,
match their recovery SHA256s and are ignored. Active documentation links resolve
inside the export. Git whitespace/progress-cap checks pass. Eight active runtime
modules and all dependency manifests remain unchanged from base.

CodeRabbit CLI **0.9.0** completed the local committed review against `main`
(`7c50d4b`), reviewed head `deb10a7eda8a3fba24022f844580d004b18f2d42`, observed
**2026-10-18T16:07:51.261246+00:00**: **zero findings**.
[Generated report](untrack-legacy-coderabbit-20261018.ndjson). The binary legacy
fixture was excluded by CodeRabbit; its local preservation was independently
verified by SHA256. Subsequent changes only record this review/evidence.

Command (WSL, primary Git directory):
`coderabbit review --agent --committed --base main`.

README, AGENTS, skills, experience and progress reviewed together. Subsequent
documentation alignment describes the tracked GitHub tree, correct setup/defaults,
current no-cooldown source and separately dated production observations. Earlier
local validation is unchanged; final CI/integration is owned by the publication
PR and does not constitute a production check.

## GitHub publication

Published as [PR #8](https://github.com/vanshch/Trading-Bot/pull/8). Its
[final checks](https://github.com/vanshch/Trading-Bot/pull/8/checks) and merge
record own the exact final CI/integration outcome. Only merge after final-head
CI succeeds. The cleanup and documentation do not change deployed runtime.

The README now maps the current tracked tree, concrete clone/setup commands,
actual environment defaults, one-in-flight manual charts without a source-level
cooldown, clean-clone/local test differences and known limits. `.env.example`
labels Twelve Data as fallback and documents its existing -0.50 default. Active
code, manifests, CI and immutable history remain unchanged. Current Markdown
links were checked against tracked paths, not ignored legacy copies.
