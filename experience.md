# XAU/USD bot engineering experience

These are durable lessons for the active candle alert bot. Current architecture and commands belong in [README.md](README.md), contributor rules in [AGENTS.md](AGENTS.md), workflows in [skills.md](skills.md), and dated observations/rollback in [progress.md](progress.md). Review affected guidance after substantial work and PR closeout. Prior versions are preserved under [historical references](docs/legacy/README.md); their YouTube/LLM rules do not describe the active system.

## Keep source and state explicit
- A merged PR does not establish deployment, enabled flags, runtime compatibility, or live alert delivery. Verify each separately and record actual observation times.
- Worktrees share Git history but have independent files. Check the branch/revision and dirty state before edits or deployment; preserve local-only changes and transfer reviewed changes through Git rather than copying old source onto newer code.
- A detached HEAD can be the only complete checkout. Verify source completeness, ancestry and active references before cleanup. Bundle all refs and preserve private local files separately; Git bundles do not contain uncommitted state or ignored evidence. Preserve divergent same-named history snapshots under distinct paths.
- Missing packages and OS-specific harness imports are different failures. Use the matching virtual environment for `telegram`, and Linux/WSL for the soak harness's `resource` dependency; do not infer a runtime path defect from unrelated tool failures.
- Untracking leaves files on disk. Verify the remaining application from a tracked-only export, preserve local bytes/private state, and use exact ignore entries to prevent accidental re-addition without blocking unrelated future source files.
- Read the current handoff before operational decisions. Historical successes cannot override a later user stop/pause, and editing documentation does not verify a timer's future execution.

## Treat market feeds as different contracts
- Verified raw `OANDA:XAUUSD` history is the enabled primary path, with Twelve Data then Yahoo fallback. Apply the approximate calibration only to Twelve Data; never claim spot/futures parity.
- Validate provider identity, ordered timestamps, finite prices, OHLC ranges, and volume before accepting bars. Preserve native session alignment and distinguish bar-start from closed-bar end timestamps.
- OANDA tick counts and COMEX traded contracts are different units. Correlation/history studies do not establish a trading strategy or authorize a production volume feature.
- Optional dependencies need isolated runtime checks. Python 3.10 base compatibility does not imply tvkit compatibility; combined feature memory matters under the VM's 90M high/120M max limits.
- Isolated dependency overlays can preserve existing runtime packages and make rollback a drop-in restoration. Direct maintenance and preflight commands must include the service's PYTHONPATH; importing successfully in a different environment does not qualify production.

## Evaluate only genuinely new closes
- Exclude forming candles, seed startup with the latest available completed candle, and suppress equal/older timestamps across replay or fallback. Restarting must not evaluate old history against saved alerts.
- Fresh two-candle streaks and ongoing streaks are distinct. Keep timestamp deduplication and one-shot reset separate from pattern recognition.
- Define volume thresholds numerically and identify whether the evaluated bar is included in the moving average. A previous-20 baseline needs 21 completed bars; forming-bar volume and spot/futures fallback cannot qualify an OANDA H1 close. Persist arming and processed-close times; apply a process-start cutoff to prevent restart replay without changing the primary timeframe.
- Test candle-boundary scheduling with deterministic time. Artificial harness delays must be separated from provider latency; preserve faulty-run evidence and start corrected measurements with fresh state.

## Keep notifications and persistence reliable
- All incoming commands/callbacks pass the single-user gate. Unknown users are silently rejected and logged without sensitive payloads.
- Save changed alerts/settings with a same-directory temporary file and atomic replacement. Deployment/rollback must preserve current user settings instead of restoring an old state snapshot.
- Send triggered text before optional charts; capture one shared chart per alerted candle. Quiet candles must not consume chart requests, and screenshot failures must not interrupt text alerts.
- Browserless hosts Chromium remotely. Bound decoded/encoded image size and deadlines; the hosted function sandbox lacks Node Buffer, so use the verified bounded base64 JSON response contract.
- On-demand chart captures must return control to Telegram promptly and reserve their in-flight slot before spawning a task; repeated taps must not queue paid captures. Keep rendering off the async event loop, bound the canvas, and measure combined memory before deployment. Label native tick volume/forming bars without fabricating bid/ask quotes or live countdowns.
- Escape dynamic fields according to the actual Telegram parse mode and respect message/caption limits. Suppress HTTPX request logs containing Telegram token URLs; sanitize provider errors and authenticated profile data.

- Distinguish candle eligibility, pre-send dispatch, Telegram API acceptance and device receipt. Persist credential-safe message IDs/timestamps and delivery outcomes; consuming one-shot state before a failed send needs durable retry handling.
- Classify Telegram receive-polling failures separately from sends. Recover journal retention gaps from rotated system logs before claiming full-run coverage; memory high-threshold events are pressure, distinct from OOM kills.

## Validate the relevant behavior
- Keep automated checks offline with mocks/synthetic candles. Use active bot suites; disabled legacy YouTube tests are not evidence of current bot health.
- Test failure boundaries: provider timeout/malformed identity, fallback replay, absent startup history, ongoing streaks, failed capture/photo delivery, and corrupt persistence.
- A BTC smoke window qualifies that observed BTC window, not OANDA gold sessions. Closed-market historical fetches do not verify new market-open closes or triggers.
- Benchmark application overlap separately from unrelated test subprocesses. Holding maximum screenshot buffers while spawning another interpreter inflates the workload. Record final process RSS separately from timestamped cgroup samples; exited cgroups may disappear. Inspect host-wide memory/I/O pressure alongside service limits.
- Use authoritative systemd logs, deployed source hashes, memory/restart/OOM counters, and preserved state evidence. Test totals alone are insufficient acceptance evidence.
- From Windows, encode remote Linux commands and keep them bounded/non-interactive. Use the correct `xauusd-bot.service` unit rather than legacy monitor names.

## Keep agent work proportional
- Implement small mechanical changes directly. Use optional AGY delegation only when requested and its isolation/verification benefits justify the overhead.
- If delegation is requested, specify observable behavior, allowed files, preserved invariants, and independent acceptance checks. Worker prose is not proof that a change passed.
- Keep this file focused on durable lessons; timelines, exact counts, raw logs, and incident transcripts belong in progress, immutable history, or audit artifacts. Consolidate repeated lessons instead of adding another rule stack.
