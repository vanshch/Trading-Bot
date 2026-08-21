"""Standalone offline benchmark runner for recorded livestreams.

Processes local video recordings, sampling frames at configured intervals
starting at timestamp zero, and evaluates them with OpenCV CVFilter without
invoking Gemini Vision or Telegram.
"""

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2

from src.config import (
    ConfigurationError,
    CVFilterConfig,
    load_benchmark_config,
)
from src.cv_filter import CVFilter


class BenchmarkError(Exception):
    """Raised when offline video benchmark encounters invalid inputs or processing errors."""


@dataclass
class SourceMetadata:
    """Metadata regarding the source video file and extraction settings."""

    video_path: str
    duration_seconds: float
    fps: float
    total_video_frames: int
    width: int
    height: int
    frame_interval_seconds: float


@dataclass
class BenchmarkReport:
    """Machine-readable benchmark report of the offline run."""

    source_metadata: SourceMetadata
    total_sampled_frames: int
    flagged_frames: int
    not_flagged_frames: int
    flagged_percentage: float
    not_flagged_percentage: float
    color_breakdown: dict[str, int]
    elapsed_time_seconds: float
    effective_throughput_fps: float

    def to_dict(self) -> dict[str, Any]:
        """Convert benchmark report to a Python dictionary."""
        return {
            "source_metadata": asdict(self.source_metadata),
            "total_sampled_frames": self.total_sampled_frames,
            "flagged_frames": self.flagged_frames,
            "not_flagged_frames": self.not_flagged_frames,
            "flagged_percentage": self.flagged_percentage,
            "not_flagged_percentage": self.not_flagged_percentage,
            "color_breakdown": dict(self.color_breakdown),
            "elapsed_time_seconds": self.elapsed_time_seconds,
            "effective_throughput_fps": self.effective_throughput_fps,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize benchmark report to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


def run_video_benchmark(
    video_path: str | Path,
    cv_config: CVFilterConfig | None = None,
    frame_interval_seconds: float | None = None,
) -> BenchmarkReport:
    """Run offline benchmark on a local video recording.

    Args:
        video_path: Path to the local video file.
        cv_config: Optional CVFilterConfig. Defaults to default CVFilterConfig().
        frame_interval_seconds: Sampling interval in seconds. Defaults to 5.0s.

    Returns:
        BenchmarkReport: Structured benchmark results.

    Raises:
        BenchmarkError: If video is missing, empty, unreadable, or configuration is invalid.
    """
    path = Path(video_path)
    if not path.exists():
        raise BenchmarkError(f"Video file not found at: {path.resolve()}")

    if not path.is_file():
        raise BenchmarkError(f"Video path is not a regular file: {path.resolve()}")

    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise BenchmarkError(f"Cannot inspect video file: {exc}") from exc

    if file_size == 0:
        raise BenchmarkError(f"Video file is empty (0 bytes): {path.resolve()}")

    interval = frame_interval_seconds if frame_interval_seconds is not None else 5.0
    if not math.isfinite(interval) or interval <= 0:
        raise BenchmarkError(
            f"Frame interval must be a finite number greater than 0, got {interval}"
        )

    cv_filter = CVFilter(cv_config or CVFilterConfig())

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise BenchmarkError(f"Failed to open video file: {path.resolve()}")

    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if fps <= 0 or math.isnan(fps) or math.isinf(fps):
            raise BenchmarkError(
                f"Invalid or unreadable video FPS ({fps}) in file: {path.resolve()}"
            )

        duration_seconds = total_frames / fps if (fps > 0 and total_frames > 0) else 0.0

        total_sampled = 0
        flagged_count = 0
        not_flagged_count = 0
        color_breakdown: dict[str, int] = {
            "BLUE": 0,
            "RED": 0,
            "BOTH": 0,
            "NONE": 0,
        }

        start_time = time.perf_counter()
        sample_index = 0

        while True:
            target_time_sec = sample_index * interval
            target_frame_idx = round(target_time_sec * fps)

            if total_frames > 0 and target_frame_idx >= total_frames:
                break

            cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame_idx)
            ret, frame = cap.read()
            if not ret or frame is None or frame.size == 0:
                if sample_index == 0:
                    raise BenchmarkError(
                        f"Video file contains no readable frames: {path.resolve()}"
                    )
                break

            cv_result = cv_filter.filter_frame(frame)
            total_sampled += 1

            if cv_result.candidate_found:
                flagged_count += 1
                color = cv_result.color_detected
                if color in color_breakdown:
                    color_breakdown[color] += 1
                else:
                    color_breakdown[color] = 1
            else:
                not_flagged_count += 1
                color_breakdown["NONE"] += 1

            sample_index += 1

        if total_sampled == 0:
            raise BenchmarkError(
                f"No frames could be sampled from video: {path.resolve()}"
            )

        elapsed_seconds = max(time.perf_counter() - start_time, 1e-6)
        flagged_percentage = (flagged_count / total_sampled) * 100.0
        not_flagged_percentage = (not_flagged_count / total_sampled) * 100.0
        throughput_fps = total_sampled / elapsed_seconds

        return BenchmarkReport(
            source_metadata=SourceMetadata(
                video_path=str(path),
                duration_seconds=round(duration_seconds, 2),
                fps=round(fps, 2),
                total_video_frames=total_frames,
                width=width,
                height=height,
                frame_interval_seconds=interval,
            ),
            total_sampled_frames=total_sampled,
            flagged_frames=flagged_count,
            not_flagged_frames=not_flagged_count,
            flagged_percentage=round(flagged_percentage, 2),
            not_flagged_percentage=round(not_flagged_percentage, 2),
            color_breakdown=color_breakdown,
            elapsed_time_seconds=round(elapsed_seconds, 4),
            effective_throughput_fps=round(throughput_fps, 2),
        )
    finally:
        cap.release()


def parse_benchmark_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments for the offline benchmark CLI."""
    parser = argparse.ArgumentParser(
        description="Recorded-livestream offline CV benchmark runner"
    )
    parser.add_argument(
        "positional_video",
        nargs="?",
        type=str,
        default=None,
        help="Path to local video file (positional argument)",
    )
    parser.add_argument(
        "--video",
        "-v",
        type=str,
        default=None,
        help="Path to local video file (named argument)",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default=None,
        help="Path to YAML configuration file (default: config.yaml if present)",
    )
    parser.add_argument(
        "--interval",
        "-i",
        "--frame-interval",
        dest="interval",
        type=float,
        default=None,
        help="Override frame sampling interval in seconds (default: from config or 5.0s)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Optional path to write JSON benchmark report",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Only output the JSON report without progress notes",
    )
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> int:
    """CLI entrypoint for standalone offline benchmark."""
    parsed_args = parse_benchmark_args(args)

    video_target = parsed_args.video or parsed_args.positional_video
    if not video_target:
        print(
            "ERROR: Missing video file path. Specify via positional argument or --video.",
            file=sys.stderr,
        )
        return 1

    try:
        capture_cfg, cv_cfg = load_benchmark_config(yaml_path=parsed_args.config)
        interval = (
            parsed_args.interval
            if parsed_args.interval is not None
            else capture_cfg.frame_interval_seconds
        )

        if not parsed_args.quiet:
            print(
                f"Starting offline benchmark for '{video_target}' (interval: {interval}s)...",
                file=sys.stderr,
            )

        report = run_video_benchmark(
            video_path=video_target,
            cv_config=cv_cfg,
            frame_interval_seconds=interval,
        )

        json_output = report.to_json(indent=2)

        if parsed_args.output:
            out_path = Path(parsed_args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(json_output)
            if not parsed_args.quiet:
                print(f"Report saved to: {out_path.resolve()}", file=sys.stderr)

        print(json_output)
        return 0

    except (
        BenchmarkError,
        ConfigurationError,
        ValueError,
        FileNotFoundError,
        OSError,
    ) as exc:
        print(f"BENCHMARK ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"UNEXPECTED ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
