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
    )
    assert session.session_id is not None
    assert session.channel_name == "Mehak Trades"
    assert session.status == "ACTIVE"
    assert logger.get_total_sessions_count() == 1

    # 3. Record chunks, triggers, and trades
    logger.record_chunk(session.session_id, count=5)
    logger.record_trigger(session.session_id)
    logger.record_trigger(session.session_id)
    logger.record_trade(session.session_id, alerted=True)

    # 4. End session
    ended = logger.end_session(session.session_id, status="COMPLETED")
    assert ended is not None
    assert ended.status == "COMPLETED"
    assert ended.chunks_processed == 5
    assert ended.triggers_detected == 2
    assert ended.trades_detected == 1
    assert ended.trades_alerted == 1
    assert ended.ended_at is not None

    # 5. Check persistent disk format
    assert temp_storage.exists()
    with open(temp_storage, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["channel_name"] == "Mehak Trades"
    assert data["sessions"][0]["trades_detected"] == 1


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


def test_print_summary_report(temp_storage: Path, capsys: pytest.CaptureFixture[str]) -> None:
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
