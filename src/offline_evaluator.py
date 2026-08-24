"""Standalone offline performance evaluator for local hot paths.

Evaluates local component latency and throughput across:
- Audio trigger detection (rolling buffer ingestion + keyword regex matching)
- Trade state tracker lifecycle transitions
- OpenCV CVFilter on deterministic synthetic 720p frames
- LiveSessionLogger thread-safe atomic disk persistence

Explicitly measures local CPU/in-memory performance without provider APIs,
external networks, or live video/audio streams.
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.audio_trigger import AudioTriggerDetector, RollingTranscriptBuffer
from src.config import CVFilterConfig
from src.cv_filter import CVFilter
from src.session_logger import LiveSessionLogger
from src.state_tracker import TradeStateTracker

DEFAULT_P95_BUDGETS_MS = {
    "audio_trigger_detection": 10.0,
    "trade_state_transitions": 5.0,
    "cv_filter_720p": 50.0,
    "session_metrics_persistence": 150.0,
}


@dataclass
class ScenarioLatencyStats:
    """Statistical summary of per-iteration execution latency in milliseconds."""

    min_ms: float
    max_ms: float
    mean_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float


@dataclass
class ScenarioResult:
    """Benchmark outcome for an individual component scenario."""

    scenario_name: str
    description: str
    iterations: int
    warmup_iterations: int
    total_elapsed_seconds: float
    throughput_ops_per_sec: float
    latency_stats: ScenarioLatencyStats
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert scenario result to structured dictionary."""
        return {
            "scenario_name": self.scenario_name,
            "description": self.description,
            "iterations": self.iterations,
            "warmup_iterations": self.warmup_iterations,
            "total_elapsed_seconds": round(self.total_elapsed_seconds, 6),
            "throughput_ops_per_sec": round(self.throughput_ops_per_sec, 2),
            "latency_stats_ms": asdict(self.latency_stats),
            "metadata": dict(self.metadata),
        }


@dataclass
class OfflineEvaluationReport:
    """Overall report containing all evaluated scenarios and evaluation metadata."""

    evaluation_type: str = "local_component_performance"
    disclaimer: str = (
        "Evaluates local CPU/in-memory hot path latency and throughput offline. "
        "Does not measure live end-to-end provider latency (Groq, Gemini, Telegram) or model accuracy."
    )
    timestamp_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    total_scenarios: int = 0
    total_duration_seconds: float = 0.0
    scenarios: dict[str, ScenarioResult] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert full report to dictionary."""
        return {
            "evaluation_type": self.evaluation_type,
            "disclaimer": self.disclaimer,
            "timestamp_utc": self.timestamp_utc,
            "total_scenarios": self.total_scenarios,
            "total_duration_seconds": round(self.total_duration_seconds, 4),
            "scenarios": {k: v.to_dict() for k, v in self.scenarios.items()},
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize evaluation report to formatted JSON string."""
        import json

        return json.dumps(self.to_dict(), indent=indent)


def compute_latency_stats(latencies_sec: list[float]) -> ScenarioLatencyStats:
    """Compute min, max, mean, median, p95, and p99 from a sequence of latency times in seconds."""
    if not latencies_sec:
        return ScenarioLatencyStats(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    ms_values = sorted([t * 1000.0 for t in latencies_sec])
    n = len(ms_values)
    min_ms = ms_values[0]
    max_ms = ms_values[-1]
    mean_ms = sum(ms_values) / n

    def percentile(p: float) -> float:
        k = (n - 1) * (p / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return ms_values[int(k)]
        d0 = ms_values[int(f)] * (c - k)
        d1 = ms_values[int(c)] * (k - f)
        return d0 + d1

    median_ms = percentile(50.0)
    p95_ms = percentile(95.0)
    p99_ms = percentile(99.0)

    return ScenarioLatencyStats(
        min_ms=round(min_ms, 4),
        max_ms=round(max_ms, 4),
        mean_ms=round(mean_ms, 4),
        median_ms=round(median_ms, 4),
        p95_ms=round(p95_ms, 4),
        p99_ms=round(p99_ms, 4),
    )


# ---------------------------------------------------------------------------
# Synthetic deterministic 720p frame generator
# ---------------------------------------------------------------------------


def generate_synthetic_720p_frame(pattern: str = "blue_line") -> np.ndarray:
    """Generate a deterministic synthetic 720p (1280x720) BGR frame for CV benchmarking.

    Args:
        pattern: "blue_line", "red_line", "both_lines", or "blank_chart"

    Returns:
        np.ndarray: 720x1280x3 uint8 BGR image.
    """
    height, width = 720, 1280
    # Dark chart background (BGR approx: (30, 30, 30))
    frame = np.full((height, width, 3), 30, dtype=np.uint8)

    # Deterministic subtle grid lines
    for y in range(0, height, 80):
        cv2.line(frame, (0, y), (width, y), (45, 45, 45), 1)
    for x in range(0, width, 120):
        cv2.line(frame, (x, 0), (x, height), (45, 45, 45), 1)

    # Deterministic candlestick-like shapes
    for i, x in enumerate(range(100, 1100, 40)):
        y_center = 360 + int(40 * math.sin(i * 0.5))
        color = (60, 180, 75) if i % 2 == 0 else (60, 60, 220)  # Green or Red candle
        cv2.line(frame, (x, y_center - 30), (x, y_center + 30), color, 1)
        cv2.rectangle(
            frame,
            (x - 6, y_center - 15),
            (x + 6, y_center + 15),
            color,
            -1,
        )

    # Blue position entry line (BGR: (240, 130, 20) -> HSV falls in Blue range)
    if pattern in ("blue_line", "both_lines"):
        y_blue = 320
        cv2.line(frame, (150, y_blue), (1150, y_blue), (240, 130, 20), 3)

    # Red stop loss line (BGR: (30, 30, 235) -> HSV falls in Red range)
    if pattern in ("red_line", "both_lines"):
        y_red = 440
        cv2.line(frame, (150, y_red), (1150, y_red), (30, 30, 235), 3)

    return frame


# ---------------------------------------------------------------------------
# Evaluator Scenarios
# ---------------------------------------------------------------------------


def evaluate_audio_trigger_detection(
    iterations: int = 100,
    warmup: int = 10,
) -> ScenarioResult:
    """Benchmark RollingTranscriptBuffer ingestion and AudioTriggerDetector matching."""
    corpus = [
        "Yes brother here we entered in call option",
        "Market is moving sideways at resistance level 24500",
        "NIFTY 24500 CE buy executed target 150",
        "Volume is drying up now do not take any trade",
        "I have taken position on short side",
        "Breakout candidate is forming on 15m timeframe",
        "Order executed stop loss 20 points",
        "Banknifty 52000 put taken for fast scalp",
        "Daily support line is holding here",
        "Took position in index call spread",
    ]

    detector = AudioTriggerDetector(cooldown_seconds=0.0)
    buffer = RollingTranscriptBuffer(max_duration_seconds=120)

    # Warmup
    for i in range(warmup):
        text = corpus[i % len(corpus)]
        buffer.append(text)
        detector.reset_cooldown()
        detector.check_trigger(text)

    # Timed benchmark
    latencies: list[float] = []
    triggers_count = 0
    start_total = time.perf_counter()

    for i in range(iterations):
        text = corpus[i % len(corpus)]
        t0 = time.perf_counter()
        buffer.append(text)
        detector.reset_cooldown()
        is_trig, _ = detector.check_trigger(text)
        _ = buffer.get_formatted_context()
        t1 = time.perf_counter()

        latencies.append(t1 - t0)
        if is_trig:
            triggers_count += 1

    total_elapsed = max(time.perf_counter() - start_total, 1e-6)
    stats = compute_latency_stats(latencies)

    return ScenarioResult(
        scenario_name="audio_trigger_detection",
        description="RollingTranscriptBuffer ingestion, formatting, and regex trigger detection",
        iterations=iterations,
        warmup_iterations=warmup,
        total_elapsed_seconds=total_elapsed,
        throughput_ops_per_sec=iterations / total_elapsed,
        latency_stats=stats,
        metadata={
            "corpus_size": len(corpus),
            "triggers_detected_count": triggers_count,
            "buffer_entries_retained": buffer.entry_count,
        },
    )


def evaluate_trade_state_transitions(
    iterations: int = 100,
    warmup: int = 10,
) -> ScenarioResult:
    """Benchmark TradeStateTracker state transitions across trade lifecycles."""
    tracker = TradeStateTracker(miss_threshold=2)

    class MockVerification:
        def __init__(
            self,
            is_active: bool,
            direction: str = "BUY",
            instrument: str = "NIFTY 24500 CE",
            entry_price: str = "120",
            confidence: float = 0.95,
        ):
            self.is_trade_active = is_active
            self.direction = direction
            self.instrument = instrument
            self.entry_price = entry_price
            self.confidence = confidence
            self.reasoning = "Deterministic test verification"

    v_open = MockVerification(is_active=True, direction="BUY")
    v_cont = MockVerification(is_active=True, direction="BUY", entry_price="125")
    v_miss = MockVerification(is_active=False)

    channel = "BenchTrader"

    # Warmup
    for _ in range(warmup):
        tracker.reset_channel(channel)
        tracker.process_verification(channel, v_open)
        tracker.process_verification(channel, v_cont)
        tracker.process_verification(channel, v_miss)
        tracker.process_verification(channel, v_miss)

    # Timed benchmark: each iteration executes a full 4-step trade lifecycle
    latencies: list[float] = []
    transitions_recorded: dict[str, int] = {
        "NEW_TRADE": 0,
        "STILL_ACTIVE": 0,
        "TRADE_CLOSED": 0,
    }
    start_total = time.perf_counter()

    for _ in range(iterations):
        tracker.reset_channel(channel)
        t0 = time.perf_counter()

        # Step 1: IDLE -> NEW_TRADE
        t1, _trade1 = tracker.process_verification(channel, v_open)
        # Step 2: ACTIVE -> STILL_ACTIVE
        t2, _trade2 = tracker.process_verification(channel, v_cont)
        # Step 3: Miss 1 -> STILL_ACTIVE (tolerance)
        t3, _trade3 = tracker.process_verification(channel, v_miss)
        # Step 4: Miss 2 -> TRADE_CLOSED
        t4, _trade4 = tracker.process_verification(channel, v_miss)

        t_end = time.perf_counter()
        latencies.append(t_end - t0)

        transitions_recorded[t1.value] = (
            transitions_recorded.get(t1.value, 0) + 1
        )
        transitions_recorded[t2.value] = (
            transitions_recorded.get(t2.value, 0) + 1
        )
        transitions_recorded[t3.value] = (
            transitions_recorded.get(t3.value, 0) + 1
        )
        transitions_recorded[t4.value] = (
            transitions_recorded.get(t4.value, 0) + 1
        )

    total_elapsed = max(time.perf_counter() - start_total, 1e-6)
    stats = compute_latency_stats(latencies)

    return ScenarioResult(
        scenario_name="trade_state_transitions",
        description="TradeStateTracker 4-step lifecycle transition (NEW_TRADE -> STILL_ACTIVE -> TRADE_CLOSED)",
        iterations=iterations,
        warmup_iterations=warmup,
        total_elapsed_seconds=total_elapsed,
        throughput_ops_per_sec=iterations / total_elapsed,
        latency_stats=stats,
        metadata={
            "transitions_breakdown": transitions_recorded,
            "steps_per_iteration": 4,
        },
    )


def evaluate_cv_filter_720p(
    iterations: int = 100,
    warmup: int = 10,
) -> ScenarioResult:
    """Benchmark OpenCV CVFilter on deterministic synthetic 720p frames."""
    patterns = ["blue_line", "red_line", "both_lines", "blank_chart"]
    synthetic_frames = [generate_synthetic_720p_frame(p) for p in patterns]

    cv_filter = CVFilter(CVFilterConfig())

    # Warmup
    for i in range(warmup):
        frame = synthetic_frames[i % len(synthetic_frames)]
        cv_filter.filter_frame(frame)

    # Timed benchmark
    latencies: list[float] = []
    candidates_detected = 0
    lines_total = 0
    start_total = time.perf_counter()

    for i in range(iterations):
        frame = synthetic_frames[i % len(synthetic_frames)]
        t0 = time.perf_counter()
        res = cv_filter.filter_frame(frame)
        t1 = time.perf_counter()

        latencies.append(t1 - t0)
        if res.candidate_found:
            candidates_detected += 1
            lines_total += len(res.lines)

    total_elapsed = max(time.perf_counter() - start_total, 1e-6)
    stats = compute_latency_stats(latencies)

    return ScenarioResult(
        scenario_name="cv_filter_720p",
        description="CVFilter HSV masking and Hough line detection on synthetic 1280x720 BGR frames",
        iterations=iterations,
        warmup_iterations=warmup,
        total_elapsed_seconds=total_elapsed,
        throughput_ops_per_sec=iterations / total_elapsed,
        latency_stats=stats,
        metadata={
            "frame_resolution": "1280x720",
            "candidates_detected_count": candidates_detected,
            "total_lines_detected": lines_total,
        },
    )


def evaluate_session_metrics_persistence(
    iterations: int = 100,
    warmup: int = 10,
) -> ScenarioResult:
    """Benchmark LiveSessionLogger atomic disk persistence and summary calculation."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_file = Path(tmp_dir) / "eval_sessions.json"
        session_logger = LiveSessionLogger(storage_path=storage_file)

        # Warmup
        for i in range(warmup):
            rec = session_logger.start_session(
                f"WarmupChannel_{i}", "https://youtube.com/@warmup/live"
            )
            session_logger.record_chunk(rec.session_id, 2)
            session_logger.record_trigger(rec.session_id)
            session_logger.record_trade(rec.session_id)
            session_logger.end_session(rec.session_id)
            _ = session_logger.get_summary()

        # Timed benchmark: each iteration starts a session, records metrics, ends, and aggregates summary
        latencies: list[float] = []
        start_total = time.perf_counter()

        for i in range(iterations):
            t0 = time.perf_counter()
            rec = session_logger.start_session(
                f"Channel_{i % 5}", f"https://youtube.com/@channel_{i % 5}/live"
            )
            session_logger.record_chunk(rec.session_id, 5)
            session_logger.record_trigger(rec.session_id)
            session_logger.record_trade(rec.session_id, alerted=True)
            session_logger.end_session(rec.session_id, status="COMPLETED")
            _ = session_logger.get_summary()
            t1 = time.perf_counter()

            latencies.append(t1 - t0)

        total_elapsed = max(time.perf_counter() - start_total, 1e-6)
        stats = compute_latency_stats(latencies)
        final_summary = session_logger.get_summary()

        return ScenarioResult(
            scenario_name="session_metrics_persistence",
            description="LiveSessionLogger session start, increments, atomic JSON save, end, and summary",
            iterations=iterations,
            warmup_iterations=warmup,
            total_elapsed_seconds=total_elapsed,
            throughput_ops_per_sec=iterations / total_elapsed,
            latency_stats=stats,
            metadata={
                "total_sessions_in_storage": final_summary.get(
                    "total_lives_captured", 0
                ),
                "total_trades_in_storage": final_summary.get(
                    "total_trades_detected", 0
                ),
            },
        )


# ---------------------------------------------------------------------------
# Runner and Formatted Output
# ---------------------------------------------------------------------------


def run_offline_evaluator(
    iterations: int = 100,
    warmup: int = 10,
    selected_scenarios: list[str] | None = None,
) -> OfflineEvaluationReport:
    """Execute offline performance evaluation across all or selected scenarios.

    Args:
        iterations: Number of timed iterations per scenario.
        warmup: Number of untimed warmup iterations per scenario.
        selected_scenarios: Optional list of scenario names to run. Defaults to all.

    Returns:
        OfflineEvaluationReport: Structured machine-readable benchmark report.
    """
    all_evaluators: dict[str, Any] = {
        "audio_trigger_detection": evaluate_audio_trigger_detection,
        "trade_state_transitions": evaluate_trade_state_transitions,
        "cv_filter_720p": evaluate_cv_filter_720p,
        "session_metrics_persistence": evaluate_session_metrics_persistence,
    }

    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    if warmup < 0:
        raise ValueError("warmup must be at least 0")

    target_keys = selected_scenarios or list(all_evaluators.keys())
    unknown = sorted(set(target_keys) - set(all_evaluators))
    if unknown:
        raise ValueError(f"unknown scenario(s): {', '.join(unknown)}")

    start_eval = time.perf_counter()
    scenario_results: dict[str, ScenarioResult] = {}

    for name in target_keys:
        eval_fn = all_evaluators[name]
        scenario_results[name] = eval_fn(iterations=iterations, warmup=warmup)

    total_eval_time = max(time.perf_counter() - start_eval, 1e-6)

    return OfflineEvaluationReport(
        evaluation_type="local_component_performance",
        disclaimer=(
            "Evaluates local CPU/in-memory hot path latency and throughput offline. "
            "Does not measure live end-to-end provider latency (Groq, Gemini, Telegram) or model accuracy."
        ),
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        total_scenarios=len(scenario_results),
        total_duration_seconds=round(total_eval_time, 4),
        scenarios=scenario_results,
    )


def find_budget_regressions(
    report: OfflineEvaluationReport,
    budgets_p95_ms: dict[str, float] | None = None,
) -> list[str]:
    """Return human-readable p95 budget violations without performing I/O."""
    budgets = budgets_p95_ms or DEFAULT_P95_BUDGETS_MS
    failures: list[str] = []
    for scenario_name, budget_ms in budgets.items():
        result = report.scenarios.get(scenario_name)
        if result is None:
            continue
        actual_p95 = result.latency_stats.p95_ms
        if actual_p95 > budget_ms:
            failures.append(
                f"Scenario '{scenario_name}' p95 latency {actual_p95:.2f}ms "
                f"exceeded budget of {budget_ms:.2f}ms."
            )
    return failures


def print_terminal_summary(report: OfflineEvaluationReport) -> None:
    """Print a concise terminal summary table of the evaluation report."""
    border = "=" * 80
    sub_border = "-" * 80

    print()
    print(border)
    print("                    OFFLINE COMPONENT PERFORMANCE EVALUATOR")
    print(border)
    print(f"Benchmark Type:   {report.evaluation_type}")
    print(
        f"Total Scenarios:  {report.total_scenarios} | Total Time: {report.total_duration_seconds:.4f}s"
    )
    print(sub_border)
    print(
        f"{'Scenario':<28} {'Iter':>5} {'Throughput (ops/s)':>18} {'Mean (ms)':>11} {'P95 (ms)':>10} {'P99 (ms)':>10}"
    )
    print(sub_border)

    for name, res in report.scenarios.items():
        stats = res.latency_stats
        print(
            f"{name:<28} {res.iterations:>5} {res.throughput_ops_per_sec:>18.2f} "
            f"{stats.mean_ms:>11.3f} {stats.p95_ms:>10.3f} {stats.p99_ms:>10.3f}"
        )

    print(border)
    print(
        "[NOTE] This benchmark evaluates local component execution only. It does not"
    )
    print(
        "       measure end-to-end provider latency (Groq, Gemini, Telegram) or accuracy."
    )
    print(border)
    print()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments for the offline performance evaluator CLI."""
    parser = argparse.ArgumentParser(
        description="Offline performance evaluator for local trade bot component hot paths."
    )
    parser.add_argument(
        "--iterations",
        "-n",
        type=int,
        default=100,
        help="Number of timed iterations per scenario (default: 100)",
    )
    parser.add_argument(
        "--warmup",
        "-w",
        type=int,
        default=10,
        help="Number of warmup iterations per scenario (default: 10)",
    )
    parser.add_argument(
        "--scenarios",
        "-s",
        type=str,
        default="",
        help="Comma-separated list of scenarios to run (default: all)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Optional path to save machine-readable JSON evaluation report",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress terminal summary table and output only JSON report",
    )
    parser.add_argument(
        "--check-budget",
        action="store_true",
        help="Enforce conservative performance regression budgets (fails with exit code 1 on regression)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for standalone offline evaluator."""
    args = parse_args(argv)

    selected = [s.strip() for s in args.scenarios.split(",") if s.strip()] or None
    try:
        report = run_offline_evaluator(
            iterations=args.iterations,
            warmup=args.warmup,
            selected_scenarios=selected,
        )
    except ValueError as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return 2

    json_str = report.to_json(indent=2)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_str, encoding="utf-8")
        if not args.quiet:
            print(f"Evaluation report saved to: {out_path.resolve()}", file=sys.stderr)

    if not args.quiet:
        print_terminal_summary(report)

    # Always output JSON when quiet or stdout
    if args.quiet:
        print(json_str)

    # Performance budget verification
    if args.check_budget:
        regressions = find_budget_regressions(report)
        for regression in regressions:
            print(f"REGRESSION ERROR: {regression}", file=sys.stderr)
        if regressions:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
