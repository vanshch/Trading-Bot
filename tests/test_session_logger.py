"""Unit tests for LiveSessionLogger and persistent metrics recording."""

import json
from pathlib import Path

import pytest

from src.session_logger import LiveSessionLogger


@pytest.fixture
def temp_storage(tmp_path: Path) -> Path:
    return tmp_path / "data" / "test_sessions.json"


def test_session_lifecycle(temp_storage: Path) -> None:
    logger = LiveSessionLogger(storage_path=temp_storage)

    # 1. Initially empty
    assert logger.get_total_sessions_count() == 0
    summary = logger.get_summary()
    assert summary["total_lives_captured"] == 0

    # 2. Start session
    session = logger.start_session(
        channel_name="Mehak Trades",
        channel_url="https://www.youtube.com/@tradewithmehaktrades/live",
        broadcast_id="broadcast-123",
    )
    assert session.session_id is not None
    assert session.channel_name == "Mehak Trades"
    assert session.broadcast_id == "broadcast-123"
    assert session.status == "ACTIVE"
    assert logger.get_total_sessions_count() == 1

    # 3. Record chunks, triggers, trades, and management
    logger.record_chunk(session.session_id, count=5)
    logger.record_trigger(session.session_id)
    logger.record_trigger(session.session_id)
    logger.record_trade(session.session_id, alerted=True)
    logger.record_management(session.session_id, alerted=True)
    logger.record_management(session.session_id, alerted=False)

    # 4. End session
    ended = logger.end_session(
        session.session_id,
        status="COMPLETED",
        internal_transport_restarts=2,
    )
    assert ended is not None
    assert ended.status == "COMPLETED"
    assert ended.chunks_processed == 5
    assert ended.triggers_detected == 2
    assert ended.trades_detected == 1
    assert ended.trades_alerted == 1
    assert ended.management_detected == 2
    assert ended.management_alerted == 1
    assert ended.ended_at is not None
    assert ended.internal_transport_restarts == 2

    # 5. Check persistent disk format
    assert temp_storage.exists()
    with open(temp_storage, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["channel_name"] == "Mehak Trades"
    assert data["sessions"][0]["trades_detected"] == 1
    assert data["sessions"][0]["management_detected"] == 2
    assert data["sessions"][0]["management_alerted"] == 1
    assert data["sessions"][0]["broadcast_id"] == "broadcast-123"
    assert data["sessions"][0]["internal_transport_restarts"] == 2


def test_multiple_sessions_summary(temp_storage: Path) -> None:
    logger = LiveSessionLogger(storage_path=temp_storage)

    # Session 1
    s1 = logger.start_session("Channel A", "https://youtube.com/a")
    logger.record_chunk(s1.session_id, 10)
    logger.record_trigger(s1.session_id)
    logger.end_session(s1.session_id)

    # Session 2
    s2 = logger.start_session("Channel B", "https://youtube.com/b")
    logger.record_chunk(s2.session_id, 20)
    logger.record_trade(s2.session_id, alerted=True)
    logger.end_session(s2.session_id)

    # Session 3 on Channel A
    s3 = logger.start_session("Channel A", "https://youtube.com/a")
    logger.record_chunk(s3.session_id, 15)
    logger.end_session(s3.session_id, status="INTERRUPTED", error="Stream disconnected")

    summary = logger.get_summary()
    assert summary["total_lives_captured"] == 3
    assert summary["total_audio_chunks_processed"] == 45
    assert summary["total_triggers_detected"] == 1
    assert summary["total_trades_detected"] == 1
    assert summary["channels_breakdown"]["Channel A"] == 2
    assert summary["channels_breakdown"]["Channel B"] == 1

    sessions = logger.list_sessions()
    assert len(sessions) == 3
    assert sessions[2].status == "INTERRUPTED"
    assert sessions[2].error_message == "Stream disconnected"


def test_corrupted_file_recovery(temp_storage: Path) -> None:
    temp_storage.parent.mkdir(parents=True, exist_ok=True)
    with open(temp_storage, "w", encoding="utf-8") as f:
        f.write("{invalid json format")

    logger = LiveSessionLogger(storage_path=temp_storage)
    assert logger.get_total_sessions_count() == 0

    s = logger.start_session("Recovered Channel", "https://youtube.com/rec")
    logger.end_session(s.session_id)
    assert logger.get_total_sessions_count() == 1


def test_print_summary_report(
    temp_storage: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logger = LiveSessionLogger(storage_path=temp_storage)
    s = logger.start_session("Mehak Trades", "https://youtube.com/mehak")
    logger.record_chunk(s.session_id, 12)
    logger.record_trigger(s.session_id)
    logger.record_trade(s.session_id, alerted=True)
    logger.end_session(s.session_id)

    logger.print_summary_report()
    captured = capsys.readouterr()
    assert "YOUTUBE LIVE STREAM CAPTURE & MONITORING REPORT" in captured.out
    assert "Mehak Trades" in captured.out
    assert "Total Live Streams Captured:   1" in captured.out


def test_exact_pcm_decoded_audio_and_partial_chunks(temp_storage: Path) -> None:
    logger = LiveSessionLogger(storage_path=temp_storage)
    session = logger.start_session("Channel PCM", "https://youtube.com/pcm")

    # 16kHz mono 16-bit = 32000 bytes/sec
    # Full 5s chunk: 160,000 bytes
    logger.record_chunk(session.session_id, count=1, pcm_bytes=160000)
    # Partial chunk: 16,000 bytes = 0.5s
    logger.record_chunk(session.session_id, count=1, pcm_bytes=16000)

    ended = logger.end_session(
        session.session_id,
        status="COMPLETED",
        duration_seconds=10.0,
    )
    assert ended is not None
    assert ended.chunks_processed == 2
    assert ended.audio_seconds_captured == 5.5
    assert ended.coverage_percent == 55.0


def test_interrupted_session_coverage(temp_storage: Path) -> None:
    logger = LiveSessionLogger(storage_path=temp_storage)
    session = logger.start_session("Channel Interrupted", "https://youtube.com/int")

    # Record 10s of audio, then end as INTERRUPTED
    logger.record_chunk(session.session_id, count=2, pcm_bytes=320000)
    ended = logger.end_session(
        session.session_id,
        status="INTERRUPTED",
        error="Provider rate limit (HTTP 429)",
        audio_seconds_captured=10.0,
        duration_seconds=20.0,
    )
    assert ended is not None
    assert ended.status == "INTERRUPTED"
    assert ended.error_message == "Provider rate limit (HTTP 429)"
    assert ended.audio_seconds_captured == 10.0
    assert ended.coverage_percent == 50.0


def test_zero_duration_session_clamped_to_zero(temp_storage: Path) -> None:
    from datetime import datetime, timezone

    from src.session_logger import LiveSessionRecord

    now_iso = datetime.now(timezone.utc).isoformat()
    # Force duration to be 0.0
    rec_dict = {
        "session_id": "zero01",
        "channel_name": "Zero Duration",
        "started_at": now_iso,
        "ended_at": now_iso,
        "duration_seconds": 0.0,
        "status": "COMPLETED",
        "audio_seconds_captured": 10.0,
    }
    rec = LiveSessionRecord.from_dict(rec_dict)
    assert rec.duration_seconds == 0.0
    assert rec.coverage_percent == 0.0

    # Also via end_session with an explicit zero capture duration.
    logger = LiveSessionLogger(storage_path=temp_storage)
    s = logger.start_session("Zero Channel", "https://youtube.com/zero")
    ended = logger.end_session(
        s.session_id,
        status="COMPLETED",
        audio_seconds_captured=10.0,
        duration_seconds=0.0,
    )
    assert ended is not None
    assert ended.coverage_percent == 0.0


def test_backward_compatible_loading_old_records(temp_storage: Path) -> None:
    from src.session_logger import LiveSessionRecord

    # Older record without audio_seconds_captured or coverage_percent
    old_dict_1 = {
        "session_id": "old01",
        "channel_name": "Old Channel",
        "channel_url": "https://youtube.com/old",
        "started_at": "2026-08-01T00:00:00+00:00",
        "ended_at": "2026-08-01T01:00:00+00:00",
        "duration_seconds": 3600.0,
        "status": "COMPLETED",
        "chunks_processed": 360,
    }
    rec1 = LiveSessionRecord.from_dict(old_dict_1)
    assert rec1.audio_seconds_captured == 0.0
    assert rec1.coverage_percent == 0.0
    assert rec1.chunks_processed == 360

    # Older record with audio_seconds_captured but without coverage_percent
    old_dict_2 = {
        "session_id": "old02",
        "channel_name": "Old Channel 2",
        "duration_seconds": 100.0,
        "status": "COMPLETED",
        "audio_seconds_captured": 75.0,
        "unknown_legacy_field": "ignore_me",
    }
    rec2 = LiveSessionRecord.from_dict(old_dict_2)
    assert rec2.audio_seconds_captured == 75.0
    assert rec2.coverage_percent == 75.0

    # Older record with coverage_percent exceeding 100% gets clamped
    old_dict_3 = {
        "session_id": "old03",
        "duration_seconds": 50.0,
        "audio_seconds_captured": 60.0,
        "coverage_percent": 120.0,
    }
    rec3 = LiveSessionRecord.from_dict(old_dict_3)
    assert rec3.coverage_percent == 100.0

    malformed = LiveSessionRecord.from_dict(
        {
            "session_id": "old04",
            "duration_seconds": "not-a-number",
            "audio_seconds_captured": float("inf"),
            "coverage_percent": float("nan"),
        }
    )
    assert malformed.duration_seconds == 0.0
    assert malformed.audio_seconds_captured == 0.0
    assert malformed.coverage_percent == 0.0


def test_duration_weighted_aggregate_coverage(temp_storage: Path) -> None:
    logger = LiveSessionLogger(storage_path=temp_storage)

    # Directly save custom sessions with known durations and coverage
    records = [
        # Session 1: 100s duration, 80s audio, 80% coverage
        {
            "session_id": "s1",
            "channel_name": "Channel A",
            "channel_url": "https://youtube.com/a",
            "started_at": "2026-08-01T00:00:00+00:00",
            "ended_at": "2026-08-01T00:01:40+00:00",
            "duration_seconds": 100.0,
            "status": "COMPLETED",
            "chunks_processed": 10,
            "audio_seconds_captured": 80.0,
            "coverage_percent": 80.0,
        },
        # Session 2: 300s duration, 300s audio, 100% coverage
        {
            "session_id": "s2",
            "channel_name": "Channel B",
            "channel_url": "https://youtube.com/b",
            "started_at": "2026-08-01T01:00:00+00:00",
            "ended_at": "2026-08-01T01:05:00+00:00",
            "duration_seconds": 300.0,
            "status": "COMPLETED",
            "chunks_processed": 30,
            "audio_seconds_captured": 300.0,
            "coverage_percent": 100.0,
        },
    ]
    logger._save_records(records)

    summary = logger.get_summary()
    assert summary["total_monitoring_duration_seconds"] == 400.0
    assert summary["total_audio_seconds_captured"] == 380.0
    # Weighted coverage: (100 * 80 + 300 * 100) / 400 = (8000 + 30000) / 400 = 38000 / 400 = 95.0%
    assert summary["coverage_percent"] == 95.0
    assert summary["duration_weighted_coverage_percent"] == 95.0
    assert summary["coverage_percent"] <= 100.0


def test_broadcast_summary_includes_gaps_between_fragments(temp_storage: Path) -> None:
    logger = LiveSessionLogger(storage_path=temp_storage)
    logger._save_records(
        [
            {
                "session_id": "frag1",
                "broadcast_id": "video123",
                "channel_name": "Trader",
                "started_at": "2026-09-02T09:00:00+00:00",
                "ended_at": "2026-09-02T09:01:40+00:00",
                "duration_seconds": 100.0,
                "audio_seconds_captured": 90.0,
                "internal_transport_restarts": 1,
            },
            {
                "session_id": "frag2",
                "broadcast_id": "video123",
                "channel_name": "Trader",
                "started_at": "2026-09-02T09:02:00+00:00",
                "ended_at": "2026-09-02T09:03:00+00:00",
                "duration_seconds": 60.0,
                "audio_seconds_captured": 60.0,
                "internal_transport_restarts": 2,
            },
        ]
    )

    summary = logger.get_broadcast_summary("video123")
    assert summary is not None
    assert summary["fragment_count"] == 2
    assert summary["wall_duration_seconds"] == 180.0
    assert summary["audio_seconds_captured"] == 150.0
    assert summary["coverage_percent"] == 83.33
    assert summary["internal_transport_restarts"] == 3
