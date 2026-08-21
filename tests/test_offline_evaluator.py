"""Deterministic offline unit tests for the standalone offline performance evaluator."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from src.config import CVFilterConfig
from src.cv_filter import CVFilter
from src.offline_evaluator import (
    OfflineEvaluationReport,
    ScenarioLatencyStats,
    ScenarioResult,
    compute_latency_stats,
    evaluate_audio_trigger_detection,
    evaluate_cv_filter_720p,
    evaluate_session_metrics_persistence,
    evaluate_trade_state_transitions,
    find_budget_regressions,
    generate_synthetic_720p_frame,
    main,
    run_offline_evaluator,
)


class TestSyntheticFrameGeneration(unittest.TestCase):
    """Tests for deterministic synthetic 720p frame creation and CVFilter compatibility."""

    def test_synthetic_frame_dimensions_and_types(self) -> None:
        patterns = ["blue_line", "red_line", "both_lines", "blank_chart"]
        for p in patterns:
            frame = generate_synthetic_720p_frame(p)
            self.assertEqual(frame.shape, (720, 1280, 3))
            self.assertEqual(frame.dtype, "uint8")

    def test_synthetic_frames_cv_detection(self) -> None:
        cv_filter = CVFilter(CVFilterConfig())

        # Blue line pattern should trigger BLUE candidate
        blue_frame = generate_synthetic_720p_frame("blue_line")
        blue_res = cv_filter.filter_frame(blue_frame)
        self.assertTrue(blue_res.candidate_found)
        self.assertIn(blue_res.color_detected, ("BLUE", "BOTH"))
        self.assertGreater(len(blue_res.lines), 0)

        # Red line pattern should trigger RED candidate
        red_frame = generate_synthetic_720p_frame("red_line")
        red_res = cv_filter.filter_frame(red_frame)
        self.assertTrue(red_res.candidate_found)
        self.assertIn(red_res.color_detected, ("RED", "BOTH"))
        self.assertGreater(len(red_res.lines), 0)

        # Blank chart pattern should NOT trigger any lines
        blank_frame = generate_synthetic_720p_frame("blank_chart")
        blank_res = cv_filter.filter_frame(blank_frame)
        self.assertFalse(blank_res.candidate_found)
        self.assertEqual(blank_res.color_detected, "NONE")


class TestLatencyStatsComputation(unittest.TestCase):
    """Tests for percentile and latency statistic calculations."""

    def test_compute_latency_stats_empty(self) -> None:
        stats = compute_latency_stats([])
        self.assertEqual(stats.mean_ms, 0.0)
        self.assertEqual(stats.p95_ms, 0.0)

    def test_compute_latency_stats_values(self) -> None:
        # 10 samples from 0.001s to 0.010s (1ms to 10ms)
        latencies = [i * 0.001 for i in range(1, 11)]
        stats = compute_latency_stats(latencies)

        self.assertAlmostEqual(stats.min_ms, 1.0, places=2)
        self.assertAlmostEqual(stats.max_ms, 10.0, places=2)
        self.assertAlmostEqual(stats.mean_ms, 5.5, places=2)
        self.assertAlmostEqual(stats.median_ms, 5.5, places=2)
        self.assertGreaterEqual(stats.p95_ms, 9.0)
        self.assertLessEqual(stats.p95_ms, 10.0)


class TestScenarioExecution(unittest.TestCase):
    """Tests verifying scenario iteration accounting, metadata, and deterministic execution."""

    def test_evaluate_audio_trigger_detection(self) -> None:
        res = evaluate_audio_trigger_detection(iterations=10, warmup=2)
        self.assertEqual(res.scenario_name, "audio_trigger_detection")
        self.assertEqual(res.iterations, 10)
        self.assertEqual(res.warmup_iterations, 2)
        self.assertGreater(res.total_elapsed_seconds, 0.0)
        self.assertGreater(res.throughput_ops_per_sec, 0.0)
        self.assertGreater(res.metadata["triggers_detected_count"], 0)
        self.assertEqual(res.metadata["corpus_size"], 10)

    def test_evaluate_trade_state_transitions(self) -> None:
        res = evaluate_trade_state_transitions(iterations=8, warmup=2)
        self.assertEqual(res.scenario_name, "trade_state_transitions")
        self.assertEqual(res.iterations, 8)
        self.assertEqual(res.warmup_iterations, 2)
        breakdown = res.metadata["transitions_breakdown"]
        self.assertEqual(breakdown["NEW_TRADE"], 8)
        self.assertEqual(breakdown["TRADE_CLOSED"], 8)
        self.assertEqual(breakdown["STILL_ACTIVE"], 16)  # 2 active steps per iter

    def test_evaluate_cv_filter_720p(self) -> None:
        res = evaluate_cv_filter_720p(iterations=8, warmup=2)
        self.assertEqual(res.scenario_name, "cv_filter_720p")
        self.assertEqual(res.iterations, 8)
        self.assertEqual(res.warmup_iterations, 2)
        self.assertEqual(res.metadata["frame_resolution"], "1280x720")
        self.assertGreater(res.metadata["candidates_detected_count"], 0)

    def test_evaluate_session_metrics_persistence(self) -> None:
        res = evaluate_session_metrics_persistence(iterations=6, warmup=2)
        self.assertEqual(res.scenario_name, "session_metrics_persistence")
        self.assertEqual(res.iterations, 6)
        self.assertEqual(res.warmup_iterations, 2)
        self.assertEqual(res.metadata["total_sessions_in_storage"], 8)  # 2 warmup + 6 iter


class TestOfflineEvaluationReport(unittest.TestCase):
    """Tests for structured report generation, dictionary conversions, and JSON serialization."""

    def test_run_offline_evaluator_all_scenarios(self) -> None:
        report = run_offline_evaluator(iterations=5, warmup=1)
        self.assertEqual(report.evaluation_type, "local_component_performance")
        self.assertIn("Does not measure live end-to-end", report.disclaimer)
        self.assertEqual(report.total_scenarios, 4)
        self.assertIn("audio_trigger_detection", report.scenarios)
        self.assertIn("trade_state_transitions", report.scenarios)
        self.assertIn("cv_filter_720p", report.scenarios)
        self.assertIn("session_metrics_persistence", report.scenarios)

        d = report.to_dict()
        self.assertEqual(d["evaluation_type"], "local_component_performance")
        self.assertEqual(len(d["scenarios"]), 4)

        json_str = report.to_json(indent=2)
        parsed = json.loads(json_str)
        self.assertEqual(parsed["total_scenarios"], 4)
        self.assertIn("audio_trigger_detection", parsed["scenarios"])

    def test_run_offline_evaluator_selected_scenario(self) -> None:
        report = run_offline_evaluator(
            iterations=5,
            warmup=1,
            selected_scenarios=["cv_filter_720p"],
        )
        self.assertEqual(report.total_scenarios, 1)
        self.assertIn("cv_filter_720p", report.scenarios)
        self.assertNotIn("audio_trigger_detection", report.scenarios)

    def test_invalid_workload_inputs_fail_clearly(self) -> None:
        with self.assertRaisesRegex(ValueError, "iterations"):
            run_offline_evaluator(iterations=0)
        with self.assertRaisesRegex(ValueError, "warmup"):
            run_offline_evaluator(warmup=-1)
        with self.assertRaisesRegex(ValueError, "unknown scenario"):
            run_offline_evaluator(selected_scenarios=["does_not_exist"])

    def test_budget_regressions_are_evaluated_deterministically(self) -> None:
        result = ScenarioResult(
            scenario_name="cv_filter_720p",
            description="fixture",
            iterations=10,
            warmup_iterations=1,
            total_elapsed_seconds=0.1,
            throughput_ops_per_sec=100.0,
            latency_stats=ScenarioLatencyStats(1, 60, 10, 8, 55, 59),
        )
        report = OfflineEvaluationReport(
            total_scenarios=1,
            scenarios={"cv_filter_720p": result},
        )
        self.assertEqual(find_budget_regressions(report, {"cv_filter_720p": 55.0}), [])
        failures = find_budget_regressions(report, {"cv_filter_720p": 50.0})
        self.assertEqual(len(failures), 1)
        self.assertIn("55.00ms", failures[0])


class TestOfflineEvaluatorCLI(unittest.TestCase):
    """Tests for CLI flags, JSON file output, quiet mode, and performance budgets."""

    def test_cli_quiet_json_output(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main(["--quiet", "-n", "5", "-w", "1", "-s", "audio_trigger_detection"])
        self.assertEqual(exit_code, 0)
        parsed = json.loads(output.getvalue())
        self.assertEqual(parsed["total_scenarios"], 1)
        self.assertIn("audio_trigger_detection", parsed["scenarios"])

    def test_cli_file_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_file = Path(tmp_dir) / "test_report.json"
            exit_code = main([
                "--quiet",
                "-n", "5",
                "-w", "1",
                "-s", "trade_state_transitions",
                "--output", str(out_file),
            ])
            self.assertEqual(exit_code, 0)
            self.assertTrue(out_file.exists())
            data = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertEqual(data["total_scenarios"], 1)
            self.assertIn("trade_state_transitions", data["scenarios"])

    def test_cli_invalid_scenario_returns_configuration_error(self) -> None:
        exit_code = main(["--quiet", "-s", "does_not_exist"])
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
