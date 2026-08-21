"""Unit tests for RollingTranscriptBuffer and AudioTriggerDetector."""

import time

from src.audio_trigger import (
    AudioTriggerDetector,
    RollingTranscriptBuffer,
)


def test_rolling_transcript_buffer_append_and_retrieve():
    buffer = RollingTranscriptBuffer(max_duration_seconds=10)
    buffer.append("Hello world")
    buffer.append("Nifty is breaking 22400")

    assert buffer.entry_count == 2
    context = buffer.get_context_text()
    assert "Hello world Nifty is breaking 22400" == context

    formatted = buffer.get_formatted_context()
    assert "Hello world" in formatted
    assert "Nifty is breaking 22400" in formatted


def test_rolling_transcript_buffer_pruning():
    buffer = RollingTranscriptBuffer(max_duration_seconds=1)
    buffer.append("Old commentary")
    # Simulate time lapse
    time.sleep(1.2)
    buffer.append("New commentary")

    context = buffer.get_context_text()
    assert "Old commentary" not in context
    assert "New commentary" in context


def test_rolling_transcript_buffer_empty_and_clear():
    buffer = RollingTranscriptBuffer(max_duration_seconds=60)
    buffer.append("")  # Empty should be ignored
    assert buffer.entry_count == 0

    buffer.append("Valid chunk")
    assert buffer.entry_count == 1
    buffer.clear()
    assert buffer.entry_count == 0
    assert buffer.get_context_text() == ""


def test_audio_trigger_detector_hindi_and_hinglish():
    detector = AudioTriggerDetector(
        trigger_keywords=["enter kiya", "entry banayi", "call buy"],
        cooldown_seconds=5.0,
    )

    # Positive matches
    is_trig, kw = detector.check_trigger("Haan bhai yahan pe enter kiya humne")
    assert is_trig is True
    assert kw == "enter kiya"

    detector.reset_cooldown()
    is_trig, kw = detector.check_trigger("48000 pe entry banayi hai")
    assert is_trig is True
    assert kw == "entry banayi"

    detector.reset_cooldown()
    is_trig, kw = detector.check_trigger("CALL BUY karo yahan")
    assert is_trig is True
    assert kw == "call buy"


def test_audio_trigger_detector_negative_matches():
    detector = AudioTriggerDetector(
        trigger_keywords=["enter kiya", "buy now"],
        cooldown_seconds=5.0,
    )

    is_trig, kw = detector.check_trigger(
        "Market sideways chal raha hai koi trade mat lena"
    )
    assert is_trig is False
    assert kw is None


def test_audio_trigger_detector_cooldown():
    detector = AudioTriggerDetector(
        trigger_keywords=["enter kiya"],
        cooldown_seconds=10.0,
    )

    # First trigger fires
    is_trig, _kw = detector.check_trigger("maine enter kiya")
    assert is_trig is True

    # Immediate repetition within cooldown is suppressed
    is_trig2, kw2 = detector.check_trigger("maine enter kiya fir se")
    assert is_trig2 is False
    assert kw2 is None

    # After reset, fires again
    detector.reset_cooldown()
    is_trig3, _kw3 = detector.check_trigger("maine enter kiya abhi")
    assert is_trig3 is True


def test_audio_trigger_detector_mehak_active_position_phrases():
    """Mehak's observed live-position wording must trigger trade evaluation."""
    detector = AudioTriggerDetector(
        trigger_keywords=["long active", "short active"], cooldown_seconds=5.0
    )

    assert detector.check_trigger("Yes, I am also long active") == (
        True,
        "long active",
    )

    detector.reset_cooldown()
    assert detector.check_trigger("Now the short active position is running") == (
        True,
        "short active",
    )
