"""Deterministic unit and integration tests for recorded-livestream offline benchmark."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from benchmark import main as cli_main
from src.benchmark import (
    BenchmarkError,
    BenchmarkReport,
    SourceMetadata,
    main,
    parse_benchmark_args,
    run_video_benchmark,
)
from src.config import (
    ConfigurationError,
    CVFilterConfig,
    load_benchmark_config,
)


def create_synthetic_frame(
    width: int = 200,
    height: int = 100,
    line_color: str | None = None,
) -> np.ndarray:
    """Create a synthetic RGB/BGR frame for testing CV filter line detection."""
    frame = np.full((height, width, 3), 40, dtype=np.uint8)  # dark background

    if line_color == "BLUE":
        # Draw horizontal blue line in ROI center
        cv2.line(frame, (20, 50), (180, 50), (255, 0, 0), thickness=2)
    elif line_color == "RED":
        # Draw horizontal red line in ROI center
        cv2.line(frame, (20, 50), (180, 50), (0, 0, 255), thickness=2)
    elif line_color == "BOTH":
        # Draw both horizontal blue and red lines
        cv2.line(frame, (20, 40), (180, 40), (255, 0, 0), thickness=2)
        cv2.line(frame, (20, 60), (180, 60), (0, 0, 255), thickness=2)

    return frame


def create_test_video(
    file_path: str,
    frames: list[np.ndarray],
    fps: float = 10.0,
) -> None:
    """Write a list of frames to a temporary video file."""
    if not frames:
        return
    h, w = frames[0].shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(file_path, fourcc, fps, (w, h))
    for f in frames:
        writer.write(f)
    writer.release()


class TestBenchmarkReportingDataStructures(unittest.TestCase):
    """Tests for BenchmarkReport and SourceMetadata data structures and serialization."""

    def test_benchmark_report_to_dict_and_to_json(self) -> None:
        meta = SourceMetadata(
            video_path="/path/to/video.mp4",
            duration_seconds=60.0,
            fps=30.0,
            total_video_frames=1800,
            width=1920,
            height=1080,
            frame_interval_seconds=5.0,
        )
        report = BenchmarkReport(
            source_metadata=meta,
            total_sampled_frames=12,
            flagged_frames=3,
            not_flagged_frames=9,
            flagged_percentage=25.0,
            not_flagged_percentage=75.0,
            color_breakdown={"BLUE": 2, "RED": 1, "BOTH": 0, "NONE": 9},
            elapsed_time_seconds=0.5,
            effective_throughput_fps=24.0,
        )

        d = report.to_dict()
        self.assertEqual(d["total_sampled_frames"], 12)
        self.assertEqual(d["flagged_frames"], 3)
        self.assertEqual(d["not_flagged_frames"], 9)
        self.assertEqual(d["flagged_percentage"], 25.0)
        self.assertEqual(d["not_flagged_percentage"], 75.0)
        self.assertEqual(d["color_breakdown"]["BLUE"], 2)
        self.assertEqual(d["source_metadata"]["frame_interval_seconds"], 5.0)

        json_str = report.to_json(indent=2)
        parsed = json.loads(json_str)
        self.assertEqual(parsed["flagged_frames"], 3)
        self.assertEqual(parsed["source_metadata"]["width"], 1920)


class TestBenchmarkConfigLoading(unittest.TestCase):
    """Tests for load_benchmark_config without requiring environment secrets."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_load_benchmark_config_with_valid_yaml(self) -> None:
        yaml_content = """
capture:
  frame_interval_seconds: 2.5
cv_filter:
  min_line_length_ratio: 0.15
"""
        yaml_path = os.path.join(self.temp_dir.name, "valid_config.yaml")
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)

        cap_cfg, cv_cfg = load_benchmark_config(yaml_path)
        self.assertEqual(cap_cfg.frame_interval_seconds, 2.5)
        self.assertEqual(cv_cfg.min_line_length_ratio, 0.15)

    def test_load_benchmark_config_missing_file_raises(self) -> None:
        with self.assertRaises(ConfigurationError) as ctx:
            load_benchmark_config(os.path.join(self.temp_dir.name, "nonexistent.yaml"))
        self.assertIn("not found", str(ctx.exception).lower())

    def test_load_benchmark_config_invalid_yaml_raises(self) -> None:
        yaml_path = os.path.join(self.temp_dir.name, "malformed.yaml")
        with open(yaml_path, "w", encoding="utf-8") as f:
            f.write("invalid: [yaml: broken")

        with self.assertRaises(ConfigurationError):
            load_benchmark_config(yaml_path)

    def test_load_benchmark_config_defaults_when_yaml_path_none(self) -> None:
        # When no path given and config.yaml not in current directory, returns defaults
        with patch("pathlib.Path.exists", return_value=False):
            cap_cfg, cv_cfg = load_benchmark_config(None)
            self.assertEqual(cap_cfg.frame_interval_seconds, 5.0)
            self.assertEqual(cv_cfg.min_line_length_ratio, 0.10)


class TestBenchmarkExecution(unittest.TestCase):
    """Tests for frame extraction from timestamp zero, counting, and candidate aggregation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.video_path = os.path.join(self.temp_dir.name, "test_recording.mp4")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_sampling_starts_at_timestamp_zero(self) -> None:
        # Create a video of 20 frames at 10 fps (2.0 seconds duration)
        # Frame 0 (t=0.0s): BLUE line
        # Frame 10 (t=1.0s): Blank
        frames = []
        for i in range(20):
            if i == 0:
                frames.append(create_synthetic_frame(line_color="BLUE"))
            else:
                frames.append(create_synthetic_frame(line_color=None))

        create_test_video(self.video_path, frames, fps=10.0)

        # Sample every 1.0 second: sample 0 at t=0.0s (frame 0), sample 1 at t=1.0s (frame 10)
        report = run_video_benchmark(
            video_path=self.video_path,
            cv_config=CVFilterConfig(min_line_length_ratio=0.1),
            frame_interval_seconds=1.0,
        )

        self.assertEqual(report.total_sampled_frames, 2)
        # Frame at t=0.0 was flagged as BLUE
        self.assertEqual(report.flagged_frames, 1)
        self.assertEqual(report.not_flagged_frames, 1)
        self.assertEqual(report.color_breakdown["BLUE"], 1)
        self.assertEqual(report.color_breakdown["NONE"], 1)
        self.assertEqual(report.flagged_percentage, 50.0)
        self.assertEqual(report.not_flagged_percentage, 50.0)

    def test_counting_percentages_and_color_aggregation(self) -> None:
        # 30 frames at 10 fps (3.0 seconds duration)
        # Sample interval 0.5s -> target timestamps: 0.0, 0.5, 1.0, 1.5, 2.0, 2.5 (6 samples)
        # Frame 0 (t=0.0): BLUE
        # Frame 5 (t=0.5): RED
        # Frame 10 (t=1.0): BOTH
        # Frame 15 (t=1.5): NONE
        # Frame 20 (t=2.0): BLUE
        # Frame 25 (t=2.5): NONE
        frames = []
        for i in range(30):
            if i == 0 or i == 20:
                frames.append(create_synthetic_frame(line_color="BLUE"))
            elif i == 5:
                frames.append(create_synthetic_frame(line_color="RED"))
            elif i == 10:
                frames.append(create_synthetic_frame(line_color="BOTH"))
            else:
                frames.append(create_synthetic_frame(line_color=None))

        create_test_video(self.video_path, frames, fps=10.0)

        report = run_video_benchmark(
            video_path=self.video_path,
            cv_config=CVFilterConfig(min_line_length_ratio=0.1),
            frame_interval_seconds=0.5,
        )

        self.assertEqual(report.total_sampled_frames, 6)
        self.assertEqual(report.flagged_frames, 4)  # 2 Blue, 1 Red, 1 Both
        self.assertEqual(report.not_flagged_frames, 2)  # 2 None
        self.assertEqual(report.color_breakdown["BLUE"], 2)
        self.assertEqual(report.color_breakdown["RED"], 1)
        self.assertEqual(report.color_breakdown["BOTH"], 1)
        self.assertEqual(report.color_breakdown["NONE"], 2)

        # Percentages: 4/6 = 66.67%, 2/6 = 33.33%
        self.assertEqual(report.flagged_percentage, 66.67)
        self.assertEqual(report.not_flagged_percentage, 33.33)

        # Elapsed time and throughput
        self.assertGreater(report.elapsed_time_seconds, 0.0)
        self.assertGreater(report.effective_throughput_fps, 0.0)
        self.assertEqual(report.source_metadata.total_video_frames, 30)
        self.assertEqual(report.source_metadata.fps, 10.0)
        self.assertEqual(report.source_metadata.frame_interval_seconds, 0.5)

    def test_all_frames_unflagged(self) -> None:
        frames = [create_synthetic_frame(line_color=None) for _ in range(20)]
        create_test_video(self.video_path, frames, fps=10.0)

        report = run_video_benchmark(
            video_path=self.video_path,
            cv_config=CVFilterConfig(),
            frame_interval_seconds=1.0,
        )

        self.assertEqual(report.total_sampled_frames, 2)
        self.assertEqual(report.flagged_frames, 0)
        self.assertEqual(report.not_flagged_frames, 2)
        self.assertEqual(report.flagged_percentage, 0.0)
        self.assertEqual(report.not_flagged_percentage, 100.0)
        self.assertEqual(report.color_breakdown["NONE"], 2)

    def test_all_frames_flagged(self) -> None:
        frames = [create_synthetic_frame(line_color="RED") for _ in range(20)]
        create_test_video(self.video_path, frames, fps=10.0)

        report = run_video_benchmark(
            video_path=self.video_path,
            cv_config=CVFilterConfig(),
            frame_interval_seconds=1.0,
        )

        self.assertEqual(report.total_sampled_frames, 2)
        self.assertEqual(report.flagged_frames, 2)
        self.assertEqual(report.not_flagged_frames, 0)
        self.assertEqual(report.flagged_percentage, 100.0)
        self.assertEqual(report.not_flagged_percentage, 0.0)
        self.assertEqual(report.color_breakdown["RED"], 2)


class TestBenchmarkFailuresAndValidation(unittest.TestCase):
    """Tests for invalid/missing/unreadable/empty inputs and error handling."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_missing_video_file_raises(self) -> None:
        missing_path = os.path.join(self.temp_dir.name, "missing.mp4")
        with self.assertRaises(BenchmarkError) as ctx:
            run_video_benchmark(missing_path)
        self.assertIn("not found", str(ctx.exception).lower())

    def test_empty_zero_byte_video_file_raises(self) -> None:
        empty_path = os.path.join(self.temp_dir.name, "empty.mp4")
        with open(empty_path, "wb"):
            pass  # 0 bytes

        with self.assertRaises(BenchmarkError) as ctx:
            run_video_benchmark(empty_path)
        self.assertIn("empty", str(ctx.exception).lower())

    def test_unreadable_corrupted_video_raises(self) -> None:
        corrupt_path = os.path.join(self.temp_dir.name, "corrupt.mp4")
        with open(corrupt_path, "wb") as f:
            f.write(b"not a valid mp4 header video content")

        with self.assertRaises(BenchmarkError) as ctx:
            run_video_benchmark(corrupt_path)
        self.assertTrue(
            "failed to open" in str(ctx.exception).lower()
            or "invalid" in str(ctx.exception).lower()
            or "no readable frames" in str(ctx.exception).lower()
        )

    def test_invalid_negative_or_zero_interval_raises(self) -> None:
        valid_video = os.path.join(self.temp_dir.name, "valid.mp4")
        create_test_video(valid_video, [create_synthetic_frame()], fps=10.0)

        with self.assertRaises(BenchmarkError) as ctx:
            run_video_benchmark(valid_video, frame_interval_seconds=0.0)
        self.assertIn("interval", str(ctx.exception).lower())

        with self.assertRaises(BenchmarkError) as ctx:
            run_video_benchmark(valid_video, frame_interval_seconds=-2.5)
        self.assertIn("interval", str(ctx.exception).lower())

    def test_non_finite_interval_raises(self) -> None:
        valid_video = os.path.join(self.temp_dir.name, "valid.mp4")
        create_test_video(valid_video, [create_synthetic_frame()], fps=10.0)

        for interval in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(interval=interval):
                with self.assertRaises(BenchmarkError) as ctx:
                    run_video_benchmark(valid_video, frame_interval_seconds=interval)
                self.assertIn("finite", str(ctx.exception).lower())


class TestBenchmarkCLI(unittest.TestCase):
    """Tests for CLI arguments, execution, file output, and exit codes."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.video_path = os.path.join(self.temp_dir.name, "cli_test.mp4")
        frames = [
            create_synthetic_frame(line_color="BLUE"),
            create_synthetic_frame(line_color=None),
            create_synthetic_frame(line_color="RED"),
        ]
        create_test_video(self.video_path, frames, fps=1.0)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_parse_benchmark_args_defaults(self) -> None:
        args = parse_benchmark_args(["video.mp4"])
        self.assertEqual(args.positional_video, "video.mp4")
        self.assertIsNone(args.video)
        self.assertIsNone(args.config)
        self.assertIsNone(args.interval)
        self.assertIsNone(args.output)
        self.assertFalse(args.quiet)

    def test_parse_benchmark_args_named_flags(self) -> None:
        args = parse_benchmark_args(
            [
                "--video",
                "video.mp4",
                "--config",
                "cfg.yaml",
                "--interval",
                "2.5",
                "--output",
                "out.json",
                "--quiet",
            ]
        )
        self.assertEqual(args.video, "video.mp4")
        self.assertEqual(args.config, "cfg.yaml")
        self.assertEqual(args.interval, 2.5)
        self.assertEqual(args.output, "out.json")
        self.assertTrue(args.quiet)

    def test_cli_execution_success(self) -> None:
        out_json_path = os.path.join(self.temp_dir.name, "output_report.json")
        exit_code = main(
            [
                "--video",
                self.video_path,
                "--interval",
                "1.0",
                "--output",
                out_json_path,
                "--quiet",
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.exists(out_json_path))

        with open(out_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["total_sampled_frames"], 3)
        self.assertEqual(data["flagged_frames"], 2)
        self.assertEqual(data["not_flagged_frames"], 1)

    def test_cli_execution_missing_video_arg(self) -> None:
        exit_code = main([])
        self.assertEqual(exit_code, 1)

    def test_cli_execution_missing_video_file_fails(self) -> None:
        missing = os.path.join(self.temp_dir.name, "nonexistent.mp4")
        exit_code = main(["--video", missing])
        self.assertEqual(exit_code, 1)

    def test_root_cli_main_wrapper(self) -> None:
        exit_code = cli_main(["--video", self.video_path, "--quiet"])
        self.assertEqual(exit_code, 0)


class TestBenchmarkCredentialIsolation(unittest.TestCase):
    """Ensure benchmark never loads or requires provider credentials."""

    def test_runs_without_any_credentials(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        try:
            video_path = os.path.join(temp_dir.name, "clean_test.mp4")
            frames = [create_synthetic_frame(line_color="BLUE") for _ in range(10)]
            create_test_video(video_path, frames, fps=5.0)

            # Scrub all environment credentials
            with patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "",
                    "TELEGRAM_BOT_TOKEN": "",
                    "TELEGRAM_CHAT_ID": "",
                },
                clear=False,
            ):
                report = run_video_benchmark(
                    video_path=video_path,
                    frame_interval_seconds=1.0,
                )
                self.assertEqual(report.total_sampled_frames, 2)
                self.assertEqual(report.flagged_frames, 2)
        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
