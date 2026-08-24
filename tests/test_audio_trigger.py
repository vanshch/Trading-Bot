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


def test_audio_trigger_detector_english_trade_phrases():
    """Whisper-translated English trade phrases must trigger detection."""
    detector = AudioTriggerDetector(
        trigger_keywords=["active", "activated", "entry", "buy", "sell", "short", "sold"],
        cooldown_seconds=5.0,
    )

    # "मैंने एक्टिव करी है" → Whisper translates to something like:
    assert detector.check_trigger("I have activated it and safe traders can wait for closing")[0] is True

    detector.reset_cooldown()
    assert detector.check_trigger("I took the entry at 45.50 level")[0] is True

    detector.reset_cooldown()
    assert detector.check_trigger("We will buy from this support zone")[0] is True

    detector.reset_cooldown()
    assert detector.check_trigger("I sold at the order block level")[0] is True


def test_audio_trigger_detector_negative_english():
    """Analysis phrases should NOT trigger."""
    detector = AudioTriggerDetector(
        trigger_keywords=["active", "entry", "buy", "sell"],
        cooldown_seconds=5.0,
    )
    # Discussion about price levels without execution
    assert detector.check_trigger("The price is near support but let us wait")[0] is False
    assert detector.check_trigger("We are watching the candle close")[0] is False
    assert detector.check_trigger("")[0] is False
    assert detector.check_trigger("   ")[0] is False


def test_audio_trigger_detector_default_fallback_keywords():
    detector = AudioTriggerDetector(cooldown_seconds=5.0)
    assert detector.check_trigger("Order placed for Gold long")[0] is True
    detector.reset_cooldown()
    assert detector.check_trigger("Position executed now")[0] is True
    detector.reset_cooldown()
    assert detector.check_trigger("We entered at this candle")[0] is True
    detector.reset_cooldown()
    assert detector.check_trigger("Bought 2 lots here")[0] is True


def test_audio_trigger_detector_word_boundaries():
    detector = AudioTriggerDetector(
        trigger_keywords=["buy", "sell", "active"],
        cooldown_seconds=5.0,
    )
    # Substrings like "buyer", "reseller", "inactive" should not trigger \b boundaries
    assert detector.check_trigger("The buyers are stepping in")[0] is False
    assert detector.check_trigger("Contact the reseller")[0] is False
    assert detector.check_trigger("The state is inactive")[0] is False
    # Exact words with punctuation should trigger
    assert detector.check_trigger("Let's buy!")[0] is True


def test_audio_trigger_detector_cooldown_and_update():
    detector = AudioTriggerDetector(
        trigger_keywords=["entry"],
        cooldown_seconds=10.0,
    )
    # First trigger fires
    is_trig, kw = detector.check_trigger("Taking entry now")
    assert is_trig is True
    assert kw == "entry"

    # Immediate repetition within cooldown is suppressed
    is_trig2, kw2 = detector.check_trigger("Taking entry now again")
    assert is_trig2 is False
    assert kw2 is None

    # After reset, fires again
    detector.reset_cooldown()
    is_trig3, kw3 = detector.check_trigger("Taking entry now again")
    assert is_trig3 is True
    assert kw3 == "entry"

    # Update keywords dynamically
    detector.reset_cooldown()
    detector.update_keywords(["breakout", "scalp"])
    assert detector.check_trigger("Breakout confirmed")[0] is True
    detector.reset_cooldown()
    assert detector.check_trigger("Taking entry now")[0] is False


def test_audio_trigger_cooldown_suppresses_synonyms_but_not_different_actions():
    detector = AudioTriggerDetector(
        trigger_keywords=["book profit", "partial booking", "move sl", "closed"],
        cooldown_seconds=30.0,
    )

    assert detector.check_trigger("Book profit now") == (True, "book profit")
    assert detector.check_trigger("Partial booking is done") == (False, None)
    assert detector.check_trigger("Move SL to cost") == (True, "move sl")
    assert detector.check_trigger("The remaining trade is closed") == (True, "closed")


def test_audio_trigger_detector_audited_phrases():
    """Audited phrases from August 24 broadcast must trigger default fallback keywords."""
    detector = AudioTriggerDetector(cooldown_seconds=1.0)

    # 1. Audited entry phrase: "You can enter with one lot. I am taking a small scalp."
    trig, kw = detector.check_trigger("You can enter with one lot. I am taking a small scalp.")
    assert trig is True
    assert kw in ("enter", "scalp", "taking a scalp")

    # 2. Audited management phrase: "Book some profit and move SL to CTC."
    detector.reset_cooldown()
    trig, kw = detector.check_trigger("Book some profit and move SL to CTC.")
    assert trig is True
    assert kw in ("book profit", "book some profit", "sl to ctc", "ctc", "move sl")

    # 3. Audited exit phrase: "My remaining trade is closed/cut."
    detector.reset_cooldown()
    trig, kw = detector.check_trigger("My remaining trade is closed.")
    assert trig is True
    assert kw in ("closed", "close", "trade closed", "trade is closed")

    detector.reset_cooldown()
    trig, kw = detector.check_trigger("My remaining trade is cut.")
    assert trig is True
    assert kw in ("cut", "trade cut")


def test_audio_trigger_detector_position_management_keywords():
    """Verify various position-management phrases trigger detection."""
    detector = AudioTriggerDetector(cooldown_seconds=1.0)

    management_phrases = [
        ("Booked partial quantity at first target", ["booked partial", "partial book", "partial booking"]),
        ("Cost-to-cost stop loss shifted", ["cost-to-cost", "cost to cost", "sl shifted", "shifted sl"]),
        ("Move stop to breakeven now", ["move stop", "breakeven"]),
        ("Trailing sl to 24500", ["trailing sl", "trail sl"]),
        ("Target hit for 1:2 risk reward", ["target hit"]),
        ("SL hit on the spike", ["sl hit"]),
        ("Exited the position completely", ["exited", "exit", "position"]),
    ]

    for phrase, expected_keywords in management_phrases:
        detector.reset_cooldown()
        trig, kw = detector.check_trigger(phrase)
        assert trig is True, f"Failed to trigger on '{phrase}'"
        assert kw is not None
