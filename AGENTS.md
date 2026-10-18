# AGENTS.md

## Session Initialization & Pre-Decision Rule (Mandatory)
1. Read `README.md` as the authoritative source of truth for the official project architecture, commands, and specifications.
2. Read `progress.md` for AI context on prior accomplishments, current work in progress, and future milestones.
3. **Check Before Taking Major Decisions**: Before making or proposing any significant code, architectural, or deployment changes, always check `progress.md` to verify active system state and prevent regressions.

## Progress Maintenance (Mandatory)
- Treat `progress.md` as a current handoff, not an append-only diary. Replace superseded status in place; keep current state, validation limits, unresolved issues, next actions, evidence links and rollback instructions explicit.
- After every update, keep the entire file at **no more than 1,200 whitespace-delimited words AND 10,240 UTF-8 bytes**, with at most **eight recent milestone entries**. Lines alone are not a reliable limit because dense lines can contain hundreds of words. Compact at a completed milestone or before an update would exceed either size cap.
- Before compaction, preserve the existing file verbatim in a uniquely dated `docs/progress-history/` snapshot and add a concise index link. Never overwrite a prior snapshot. The archive is historical and read only on demand; do not load all history during session initialization. Keep the archive index to at most 50 snapshot rows, splitting older rows into yearly indexes when needed.
- Preserve exact verified versions/timestamps, operational constraints, open risks, user stop/pause decisions and evidence pointers. Do not turn pending checks into successes or silently drop unresolved work. Keep durable architecture/invariants in README/AGENTS rather than duplicating them in every progress entry. Keep raw logs and secrets outside progress/history Markdown.
- Reconcile new information against the current snapshot instead of stacking contradictory updates. Retain original historical snapshots to avoid information loss from repeatedly summarizing summaries. Record the last actual verification time; documentation edits do not constitute a fresh runtime check.
- Verify the caps after editing: `python -c "from pathlib import Path; p=Path('progress.md'); t=p.read_text(encoding='utf-8'); w=len(t.split()); b=p.stat().st_size; print(f'{w} words, {b} bytes'); assert w<=1200 and b<=10240, 'Compact progress.md before finishing'"`. Review the recent-entry count and evidence/archive links as well.

## Documentation Maintenance (Mandatory)
- After substantial work (a completed feature, architecture/provider/runtime change, deployment, or major experiment), review `README.md`, `AGENTS.md`, `skills.md`, `experience.md`, and `progress.md` together. Update affected sections before handing off; a documentation review need not change every file.
- When a PR is merged or closed, reconcile these files with the final outcome. Distinguish merged code, deployed code, enabled configuration, and actually verified behavior. A closed, unmerged PR must not be described as shipped; keep unresolved work explicit. If closure happens after the implementation turn, perform this review in the closeout turn.
- `README.md` owns current architecture/setup/commands; `AGENTS.md` owns contributor rules and boundaries; `skills.md` maps current workflows; `experience.md` records durable engineering lessons; `progress.md` owns dated operational state, validation limits, evidence, and next actions.
- Keep historical material explicitly historical and load it only when relevant. Replace superseded claims rather than appending contradictions. Keep feature defaults distinct from production flags; documentation edits are not runtime verification.
- Verify facts against the implementation and deployment evidence in the relevant checkout. Worktrees do not automatically share file edits: identify the authoritative revision and carry documentation changes through the same reviewed integration flow as code. Preserve unrelated changes and historical evidence; do not copy an older implementation over merged code.

## Tech Stack & Runtime
- **System**: XAU/USD Candle Close & Momentum Telegram Alert Bot; the legacy YouTube monitor is disabled.
- **Feeds when enabled**: verified raw TradingView `OANDA:XAUUSD` via `tvkit==0.18.0`, then Twelve Data `XAU/USD`, then Yahoo `GC=F` gold futures. With TradingView disabled, start at Twelve Data.
- **Runtime**: Python 3.10+ base; Python 3.11+ for optional tvkit; async `python-telegram-bot` v20+, `httpx`, and `python-dotenv`. Recorded production runtime is Python 3.12.15.
- **Production**: GCE `trade-bot-vm`, project `true-ion-434206-f2`, zone `us-central1-a`, `e2-micro`; `xauusd-bot.service` under `bot_deployer` in `/opt/xauusd_telegram_bot`. Memory limits: `MemoryHigh=90M`, `MemoryMax=120M`.
- **Feature flags**: repository defaults are disabled for TradingView and Browserless screenshots. Consult `progress.md` for deployed revision, enabled flags, user pauses, last verification, and rollback; do not infer current service health from a merge.
- **Checkout**: GitHub main is authoritative; `D:/projects/bot_agy` is the primary local checkout. Check its revision and dirty state before work. Main can contain undeployed changes; documentation-only commits do not change runtime hashes. Consult progress for exact deployed revision, reconciliation evidence and recovery.

## Checkout and platform preflight
- Run `git status --short --branch`, `git worktree list`, and `git log -1 --oneline` before editing. Use a dedicated feature branch/worktree based on reviewed main; never edit a dirty base branch or remove a worktree only because HEAD is detached.
- Before cleanup, preserve staged/unstaged changes, untracked files, private ignored state/evidence and all Git refs in a verified private recovery location outside the repository. Exclude reproducible virtual environments/caches; record exclusions. Never upload recovery copies or raw private logs.
- Recheck ancestry and active worktree references immediately before local branch removal. Preserve unique history and unrelated deletion intent. Remote branch deletion is a separate action.
- Retired YouTube code/tests/plans and obsolete deployment recipes are ignored local history, removed from the tracked tree after `7c50d4b`. Preserve local copies and do not re-add them implicitly. Keep explicit ignore entries scoped to the retired paths; remove the relevant entry deliberately if replacing a retired path with new active code. Validate untracking with a tracked-only export so ignored source cannot hide a missing dependency.
- Use the selected interpreter with `python -m pip`; install matching requirements before offline tests. Missing `telegram` is a dependency/interpreter problem. `tools/tradingview_soak` uses Linux's `resource` module and runs on Linux/WSL; it is not a Windows runtime requirement. Keep `/opt/...` paths in Linux operations, not Python import paths. Diagnose actual errors before adding platform workarounds.

## Commands

### Local Execution & Testing
- Install base dependencies: `python -m pip install -r requirements.txt`.
- For TradingView, use Python 3.11+ and `python -m pip install -r requirements-tradingview.txt`.
- Run locally: `python bot.py`. Do not start a second poller using the production token.
- Syntax check: `python -m py_compile bot.py alerts.py config.py feed.py tradingview_feed.py screenshots.py hourly_chart.py manual_charts.py test_chart_delivery.py tools/preview_hourly_chart.py`.
- Active offline suites: run `python -m unittest discover -s tests -p <pattern>` for `test_alerts.py`, `test_screenshots.py`, `test_chart_scheduler.py`, `test_tradingview_feed.py`, and `test_manual_charts.py`. Linux harness checks: `python -m unittest discover -s tools/tradingview_soak -p test_soak.py`.
- Match checks to the change. Documentation-only edits require factual/link/diff checks and the progress caps, not provider requests or Telegram sends. Legacy YouTube tests are not tracked or part of CI; ignored local copies may still exist, so use the named patterns above.

### Production VM Management
- Check status: `systemctl status xauusd-bot.service`.
- Recent logs: `journalctl -u xauusd-bot.service -n 100 --no-pager`.
- Follow logs: `journalctl -u xauusd-bot.service -f`.
- Inspect memory/pressure: `systemctl show xauusd-bot.service -p MemoryCurrent -p MemoryHigh -p MemoryMax` and its cgroup counters.
- Restart only within authorized deployment/operations work and respecting all user pauses: `sudo systemctl restart xauusd-bot.service`.

### Production Code Deployment
1. Check current `progress.md`, the complete reviewed revision, rollback evidence, and user authorization; never deploy stale or dirty source or override a requested pause. Checkout reconciliation does not authorize deployment.
2. Validate the active source/tests above. Production source comprises `bot.py`, `alerts.py`, `config.py`, `feed.py`, `tradingview_feed.py`, `screenshots.py`, `hourly_chart.py`, and `manual_charts.py`; install matching dependency manifests in the target runtime. Preserve private `.env` and current `data/alerts.json`.
   Pillow 12.3.0 is deployed through an isolated PYTHONPATH drop-in; direct maintenance/preflight commands need that service environment. Deploy all eight modules together after combined Linux memory checks; consult progress for current revision and rollback.
3. For this feature, stage all eight source files to `/tmp` with `gcloud compute scp bot.py alerts.py config.py feed.py tradingview_feed.py screenshots.py hourly_chart.py manual_charts.py trade-bot-vm:/tmp/ --project=true-ion-434206-f2 --zone=us-central1-a --quiet`.
4. Through the encoded remote command mechanism below, back up current code/config/runtime, install staged files into `/opt/xauusd_telegram_bot/`, restore `bot_deployer:bot_deployer` ownership, and restart only when authorized. Runtime/dependency changes require their own preflight, including combined memory.
5. Verify service state, deployed source hashes, preserved alert settings, recent logs, memory, and restart/OOM counters. Record the actual observation time and limits; an active service alone does not prove market-open alert delivery.

## Core Boundaries & Invariants
- **Authorization**: all commands and callbacks use `@authorized_only`, accept only `ALLOWED_USER_ID`, and silently reject unauthorized requests with warning logs.
- **Feed integrity**: verify OANDA source identity and OHLCV validity; use raw OANDA OHLC. Apply the approximate `OANDA_PRICE_OFFSET=-0.50` only to Twelve Data. Yahoo is futures, not spot; no fallback guarantees OANDA parity. Missing optional runtime/package, invalid data, timeouts, or provider failures must fall through without killing the scheduler.
- **Candle timing**: exclude forming bars from close evaluation; preserve provider session boundaries. Seed the processed timestamp from startup history and reject equal/older closes across replay and fallback.
- **Momentum**: fire fresh two-green/two-red sequences only; suppress third/later candles of an ongoing streak. Deduplicate by candle timestamp and turn one-shot settings off after triggering.
- **Persistence**: commit alert/settings changes to `data/alerts.json` through a same-directory temporary file and atomic replacement. Missing/corrupt JSON recovers safely to defaults.
- **Charts**: deliver triggered text first, then at most one Browserless photo per alerted candle. Quiet candles do not capture charts. Capture/photo failures must leave text delivery intact. Screenshots can contain forming bars and are visual context, not the authoritative candle feed. Browserless runs Chromium outside the VM.
- **Manual charts**: explicit screenshots and 20-candle hourly tick-volume images use background tasks and one shared in-flight reservation, with no timed cooldown. Keep verified raw OANDA data/source and native start timestamps; reject insufficient history rather than substituting fallback volume. Forming bars may appear only as labeled visual context. Do not change scheduler timeframe/state or saved alerts from these requests.
- **Formatting/secrets**: respect the actual Telegram parse mode and entity/payload limits; escape dynamic text where needed and use monospace prices. Never log tokens, private environment values, authenticated URLs, or private profile data. Keep HTTPX request logs suppressed because Telegram URLs contain credentials.
- **Scope**: production currently monitors 15m. Hourly monitoring, volume alert rules, and a recurring market-hours gate are not implemented/enabled. Respect paused experiments and production operations in `progress.md`.
- **Windows remote execution**: Base64-encode Linux command strings passed via `gcloud compute ssh`; decode and execute on the VM to avoid shell argument stripping. Use non-interactive flags, verified host keys, and bounded commands.
