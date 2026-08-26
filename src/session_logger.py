"""Persistent Live Session Tracker and Metrics Logger for YouTube Live Stream Monitoring.

Tracks each captured live broadcast: start/end times, stream duration, audio chunks
processed, trigger keywords detected, trades verified by Gemini, and Telegram alerts sent.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("trade_bot.session_logger")

DEFAULT_STORAGE_PATH = Path("data/live_sessions.json")


def _nonnegative_finite_float(value: object, default: float = 0.0) -> float:
    """Return a finite non-negative float for persisted metric input."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) and parsed >= 0.0 else default


@dataclass
class LiveSessionRecord:
    """Represents a recorded monitoring session for a live stream broadcast."""

    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    channel_name: str = ""
    channel_url: str = ""
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    ended_at: str | None = None
    duration_seconds: float = 0.0
    status: str = "ACTIVE"  # "ACTIVE", "COMPLETED", "INTERRUPTED", "ERROR"
    chunks_processed: int = 0
    triggers_detected: int = 0
    trades_detected: int = 0
    trades_alerted: int = 0
    management_detected: int = 0
    management_alerted: int = 0
    error_message: str | None = None
    audio_seconds_captured: float = 0.0
    coverage_percent: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LiveSessionRecord:
        kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        kwargs["duration_seconds"] = _nonnegative_finite_float(
            kwargs.get("duration_seconds")
        )
        kwargs["audio_seconds_captured"] = _nonnegative_finite_float(
            kwargs.get("audio_seconds_captured")
        )

        if "coverage_percent" in kwargs and kwargs["coverage_percent"] is not None:
            kwargs["coverage_percent"] = max(
                0.0,
                min(
                    100.0,
                    _nonnegative_finite_float(kwargs["coverage_percent"]),
                ),
            )
        else:
            # Backward-compatible calculation for older records
            duration = kwargs["duration_seconds"]
            captured = kwargs["audio_seconds_captured"]
            if duration > 0.0 and captured > 0.0:
                raw = (captured / duration) * 100.0
                kwargs["coverage_percent"] = max(0.0, min(100.0, round(raw, 2)))
            else:
                kwargs["coverage_percent"] = 0.0
        return cls(**kwargs)


class LiveSessionLogger:
    """Thread-safe persistent manager for live stream monitoring sessions and aggregated metrics."""

    def __init__(self, storage_path: str | Path = DEFAULT_STORAGE_PATH) -> None:
        self.storage_path = Path(storage_path)
        self._lock = threading.Lock()
        self._active_sessions: dict[str, LiveSessionRecord] = {}
        self._ensure_storage_exists()

    def _ensure_storage_exists(self) -> None:
        """Create parent directory and file if they do not exist."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.storage_path.exists():
                with open(self.storage_path, "w", encoding="utf-8") as f:
                    json.dump({"sessions": []}, f, indent=2)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error(f"Failed to initialize storage at {self.storage_path}: {exc}")

    def _load_records(self) -> list[dict[str, Any]]:
        """Load raw session records from disk."""
        if not self.storage_path.exists():
            return []
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("sessions", []) if isinstance(data, dict) else []
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"Could not read sessions from {self.storage_path}: {exc}")
            return []

    def _save_records(self, records: list[dict[str, Any]]) -> None:
        """Persist session records to disk atomically."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.storage_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"sessions": records}, f, indent=2)
            os.replace(tmp_path, self.storage_path)
        except (OSError, TypeError, ValueError) as exc:
            logger.error(f"Failed to persist sessions to {self.storage_path}: {exc}")

    def start_session(self, channel_name: str, channel_url: str) -> LiveSessionRecord:
        """Initialize and record a newly captured live broadcast."""
        record = LiveSessionRecord(
            channel_name=channel_name,
            channel_url=channel_url,
            started_at=datetime.now(timezone.utc).isoformat(),
            status="ACTIVE",
        )
        with self._lock:
            self._active_sessions[record.session_id] = record
            records = self._load_records()
            records.append(record.to_dict())
            self._save_records(records)

        logger.info(
            f"Logged live broadcast capture start [session={record.session_id}] for '{channel_name}'"
        )
        return record

    def record_chunk(
        self,
        session_id: str,
        count: int = 1,
        *,
        pcm_bytes: int = 0,
        bytes_per_second: int = 32000,
    ) -> None:
        """Increment processed audio/video chunk count and decoded audio seconds for an active session."""
        with self._lock:
            session = self._active_sessions.get(session_id)
            if session:
                session.chunks_processed += count
                if pcm_bytes > 0 and bytes_per_second > 0:
                    session.audio_seconds_captured += pcm_bytes / bytes_per_second

    def record_trigger(self, session_id: str) -> None:
        """Increment spoken keyword trigger count for an active session."""
        with self._lock:
            session = self._active_sessions.get(session_id)
            if session:
                session.triggers_detected += 1

    def record_trade(self, session_id: str, alerted: bool = True) -> None:
        """Increment confirmed trade count and alert count."""
        with self._lock:
            session = self._active_sessions.get(session_id)
            if session:
                session.trades_detected += 1
                if alerted:
                    session.trades_alerted += 1

    def record_management(self, session_id: str, alerted: bool = True) -> None:
        """Increment confirmed trade management action count and alert count."""
        with self._lock:
            session = self._active_sessions.get(session_id)
            if session:
                session.management_detected += 1
                if alerted:
                    session.management_alerted += 1

    def end_session(
        self,
        session_id: str,
        status: str = "COMPLETED",
        error: str | None = None,
        *,
        audio_seconds_captured: float | None = None,
        duration_seconds: float | None = None,
    ) -> LiveSessionRecord | None:
        """Conclude an active live stream monitoring session and update storage."""
        with self._lock:
            session = self._active_sessions.pop(session_id, None)
            if not session:
                records = self._load_records()
                for rec in records:
                    if rec.get("session_id") == session_id:
                        session = LiveSessionRecord.from_dict(rec)
                        break

            if not session:
                logger.warning(f"Session {session_id} not found for finalization.")
                return None

            now = datetime.now(timezone.utc)
            session.ended_at = now.isoformat()
            session.status = status
            session.error_message = error

            if duration_seconds is not None:
                session.duration_seconds = _nonnegative_finite_float(duration_seconds)
            else:
                try:
                    start_dt = datetime.fromisoformat(session.started_at)
                    session.duration_seconds = max(
                        0.0, (now - start_dt).total_seconds()
                    )
                except (ValueError, TypeError):
                    session.duration_seconds = 0.0

            if audio_seconds_captured is not None:
                session.audio_seconds_captured = _nonnegative_finite_float(
                    audio_seconds_captured
                )

            if session.duration_seconds > 0.0:
                raw_coverage = (
                    session.audio_seconds_captured / session.duration_seconds
                ) * 100.0
                session.coverage_percent = max(0.0, min(100.0, round(raw_coverage, 2)))
            else:
                session.coverage_percent = 0.0

            records = self._load_records()
            updated = False
            for i, rec in enumerate(records):
                if rec.get("session_id") == session_id:
                    records[i] = session.to_dict()
                    updated = True
                    break
            if not updated:
                records.append(session.to_dict())

            self._save_records(records)

        logger.info(
            f"Logged live broadcast completion [session={session.session_id}] for '{session.channel_name}' "
            f"(Duration: {session.duration_seconds:.1f}s, Audio: {session.audio_seconds_captured:.1f}s, "
            f"Coverage: {session.coverage_percent:.1f}%, Chunks: {session.chunks_processed}, "
            f"Triggers: {session.triggers_detected}, Trades: {session.trades_detected}, "
            f"Management: {session.management_detected})"
        )
        return session

    def get_total_sessions_count(self) -> int:
        """Return total number of recorded live stream sessions."""
        with self._lock:
            return len(self._load_records())

    def list_sessions(self) -> list[LiveSessionRecord]:
        """Return all recorded live sessions sorted by start time."""
        with self._lock:
            raw = self._load_records()
            return [LiveSessionRecord.from_dict(r) for r in raw]

    def get_summary(self) -> dict[str, Any]:
        """Calculate aggregated metrics across all recorded live sessions."""
        sessions = self.list_sessions()
        total_count = len(sessions)
        total_duration_sec = sum(s.duration_seconds for s in sessions)
        total_audio_sec = sum(s.audio_seconds_captured for s in sessions)
        total_chunks = sum(s.chunks_processed for s in sessions)
        total_triggers = sum(s.triggers_detected for s in sessions)
        total_trades = sum(s.trades_detected for s in sessions)
        total_alerts = sum(s.trades_alerted for s in sessions)
        total_mgmt_detected = sum(s.management_detected for s in sessions)
        total_mgmt_alerted = sum(s.management_alerted for s in sessions)

        if total_duration_sec > 0:
            captured_for_coverage = sum(
                min(s.audio_seconds_captured, s.duration_seconds) for s in sessions
            )
            aggregate_coverage_percent = max(
                0.0,
                min(
                    100.0,
                    round(captured_for_coverage / total_duration_sec * 100.0, 2),
                ),
            )
        else:
            aggregate_coverage_percent = 0.0

        channel_counts: dict[str, int] = {}
        for s in sessions:
            name = s.channel_name or "Unknown"
            channel_counts[name] = channel_counts.get(name, 0) + 1

        hours, remainder = divmod(int(total_duration_sec), 3600)
        minutes, seconds = divmod(remainder, 60)
        formatted_duration = (
            f"{hours}h {minutes}m {seconds}s" if hours > 0 else f"{minutes}m {seconds}s"
        )

        return {
            "total_lives_captured": total_count,
            "total_monitoring_duration_seconds": total_duration_sec,
            "total_duration_formatted": formatted_duration,
            "total_audio_seconds_captured": round(total_audio_sec, 2),
            "coverage_percent": aggregate_coverage_percent,
            "duration_weighted_coverage_percent": aggregate_coverage_percent,
            "total_audio_chunks_processed": total_chunks,
            "total_triggers_detected": total_triggers,
            "total_trades_detected": total_trades,
            "total_telegram_alerts_sent": total_alerts,
            "total_management_detected": total_mgmt_detected,
            "total_management_alerted": total_mgmt_alerted,
            "channels_breakdown": channel_counts,
        }

    def print_summary_report(self) -> None:
        """Display a formatted summary report of all recorded live streams in terminal."""
        summary = self.get_summary()
        sessions = self.list_sessions()

        lines = [
            "",
            "=" * 65,
            "[*] YOUTUBE LIVE STREAM CAPTURE & MONITORING REPORT",
            "=" * 65,
            f"Total Live Streams Captured:   {summary['total_lives_captured']}",
            f"Total Live Monitoring Time:    {summary['total_duration_formatted']}",
            f"Total Audio Captured:          {summary['total_audio_seconds_captured']:.1f}s ({summary['coverage_percent']:.1f}% coverage)",
            f"Total Audio Chunks Processed:  {summary['total_audio_chunks_processed']}",
            f"Total Spoken Triggers Fired:   {summary['total_triggers_detected']}",
            f"Total Active Trades Confirmed: {summary['total_trades_detected']}",
            f"Total Position Mgmt Actions:  {summary['total_management_detected']}",
            f"Total Telegram Alerts Sent:    {summary['total_telegram_alerts_sent'] + summary['total_management_alerted']}",
            "-" * 65,
            "Channels Breakdown:",
        ]
        if summary["channels_breakdown"]:
            for ch_name, count in summary["channels_breakdown"].items():
                lines.append(f"  * {ch_name}: {count} live session(s)")
        else:
            lines.append("  (No live broadcasts recorded yet)")

        if sessions:
            lines.append("")
            lines.append("Recent Sessions:")
            lines.append("-" * 65)
            for s in sessions[-5:]:
                dur_m = s.duration_seconds / 60
                lines.append(
                    f"[{s.session_id}] {s.channel_name} | {s.started_at[:19]} | "
                    f"Dur: {dur_m:.1f}m | Audio: {s.audio_seconds_captured:.1f}s ({s.coverage_percent:.1f}%) | "
                    f"Triggers: {s.triggers_detected} | "
                    f"Trades: {s.trades_detected} | Status: {s.status}"
                )
        lines.append("=" * 65)
        lines.append("")

        output = "\n".join(lines)
        try:
            print(output)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", "ascii") or "ascii"
            print(output.encode(encoding, errors="backslashreplace").decode(encoding))


if __name__ == "__main__":
    logger_inst = LiveSessionLogger()
    logger_inst.print_summary_report()
