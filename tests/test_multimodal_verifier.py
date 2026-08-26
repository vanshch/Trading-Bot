from unittest.mock import MagicMock, patch

import pytest

from src.config import AIConfig
from src.multimodal_verifier import (
    MultimodalTradeVerification,
    MultimodalVerifier,
)


def test_multimodal_trade_verification_validation():
    # Valid active trade with CALL direction normalization
    v = MultimodalTradeVerification(
        is_trade_active=True,
        direction="CALL",
        instrument="NIFTY 24500 CE",
        entry_price="120-125",
        stop_loss="100",
        target="160",
        trigger_quote="Humne 24500 CE buy kar liya hai",
        confidence=0.92,
        reasoning="Trader confirmed call entry on breakout",
    )
    assert v.is_trade_active is True
    assert v.direction == "CALL"
    assert v.instrument == "NIFTY 24500 CE"
    assert v.confidence == 0.92


def test_multimodal_trade_verification_direction_normalization():
    v1 = MultimodalTradeVerification(
        is_trade_active=True,
        direction="CE",
        instrument="BANKNIFTY",
    )
    assert v1.direction == "CALL"

    v2 = MultimodalTradeVerification(
        is_trade_active=True,
        direction="PE",
        instrument="BANKNIFTY",
    )
    assert v2.direction == "PUT"

    v3 = MultimodalTradeVerification(
        is_trade_active=False,
        direction="BUY",
        instrument="UNKNOWN",
    )
    assert v3.direction == "NONE"


def test_multimodal_verifier_parse_response():
    verifier = MultimodalVerifier(api_key="mock-key", config=AIConfig())

    json_str = """
    {
      "is_trade_active": true,
      "direction": "PUT",
      "instrument": "BANKNIFTY 51000 PE",
      "entry_price": "340",
      "stop_loss": "300",
      "target": "420",
      "trigger_quote": "Put mein entry le li hai guys",
      "confidence": 0.88,
      "reasoning": "Breakdown on 5m candle confirmed with spoken entry"
    }
    """
    result = verifier._parse_response(json_str)
    assert result.is_trade_active is True
    assert result.direction == "PUT"
    assert result.instrument == "BANKNIFTY 51000 PE"
    assert result.entry_price == "340"
    assert result.confidence == 0.88


def test_multimodal_verifier_suppresses_low_confidence_trade():
    verifier = MultimodalVerifier(
        api_key="mock-key",
        config=AIConfig(confidence_threshold=0.70),
    )

    result = verifier._parse_response(
        '{"is_trade_active": true, "direction": "BUY", "confidence": 0.40}'
    )

    assert result.is_trade_active is False
    assert result.direction == "NONE"


@pytest.mark.asyncio
async def test_multimodal_verifier_backoff_cap_at_30_seconds():
    verifier = MultimodalVerifier(
        api_key="mock-key",
        config=AIConfig(max_retries=4, retry_delay_seconds=20.0),
    )
    sleep_calls = []

    async def mock_sleep(duration):
        sleep_calls.append(duration)

    dummy_frame = b"\xff\xd8\xff\xe0"

    with patch("asyncio.sleep", side_effect=mock_sleep), patch.object(
        verifier, "_call_gemini_multimodal_sync", side_effect=RuntimeError("API error")
    ), patch.object(verifier, "_get_client", return_value=MagicMock()):
        res = await verifier.verify_multimodal_trade(
            frame=dummy_frame,
            transcript_context="Context",
        )

    assert res.is_trade_active is False
    assert res.confidence == 0.0
    assert res.direction == "NONE"
    assert sleep_calls == [20.0, 30.0, 30.0]


@pytest.mark.asyncio
async def test_multimodal_verifier_model_fallback_sequence():
    verifier = MultimodalVerifier(
        api_key="mock-key",
        config=AIConfig(model_name="gemini-3.6-flash", max_retries=3, retry_delay_seconds=0.01),
    )
    models_called = []

    def mock_call(client, jpeg_bytes, prompt_text, model_name):
        models_called.append(model_name)
        if len(models_called) < 3:
            raise RuntimeError("Model temporary overload")
        return '{"is_trade_active": true, "direction": "BUY", "instrument": "GOLD", "confidence": 0.85}'

    dummy_frame = b"\xff\xd8\xff\xe0"

    with patch.object(verifier, "_call_gemini_multimodal_sync", side_effect=mock_call), patch.object(
        verifier, "_get_client", return_value=MagicMock()
    ):
        res = await verifier.verify_multimodal_trade(
            frame=dummy_frame,
            transcript_context="Context",
        )

    assert res.is_trade_active is True
    assert res.direction == "BUY"
    assert res.confidence == 0.85
    assert models_called == ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-3.7-flash"]


@pytest.mark.asyncio
async def test_multimodal_verifier_supports_transcript_only_mode():
    verifier = MultimodalVerifier(
        api_key="mock-key",
        config=AIConfig(max_retries=1),
    )
    observed = {}

    def mock_call(client, jpeg_bytes, prompt_text, model_name):
        observed["jpeg_bytes"] = jpeg_bytes
        observed["prompt_text"] = prompt_text
        return '{"signal_type": "NONE", "confidence": 0.1}'

    with patch.object(
        verifier, "_call_gemini_multimodal_sync", side_effect=mock_call
    ), patch.object(verifier, "_get_client", return_value=MagicMock()):
        result = await verifier.verify_multimodal_trade(
            frame=None,
            transcript_context="Spoken context only",
        )

    assert result.signal_type == "NONE"
    assert observed["jpeg_bytes"] is None
    assert "Screenshot capture is disabled" in observed["prompt_text"]
    assert "Do not infer chart state" in observed["prompt_text"]


def test_multimodal_verifier_signal_type_normalization():
    # ENTRY aliases
    v_entry = MultimodalTradeVerification(signal_type="NEW_TRADE", is_trade_active=True, direction="BUY")
    assert v_entry.signal_type == "ENTRY"
    assert v_entry.is_trade_active is True

    # PARTIAL_EXIT aliases
    v_partial = MultimodalTradeVerification(signal_type="BOOK_PROFIT", direction="BUY")
    assert v_partial.signal_type == "PARTIAL_EXIT"
    assert v_partial.is_trade_active is True

    # MOVE_STOP aliases
    v_sl = MultimodalTradeVerification(signal_type="SL_TO_CTC", direction="BUY")
    assert v_sl.signal_type == "MOVE_STOP"
    assert v_sl.is_trade_active is True

    # EXIT aliases
    v_exit = MultimodalTradeVerification(signal_type="CLOSED", direction="BUY")
    assert v_exit.signal_type == "EXIT"
    assert v_exit.is_trade_active is False

    # NONE
    v_none = MultimodalTradeVerification(signal_type="NONE")
    assert v_none.signal_type == "NONE"
    assert v_none.is_trade_active is False
    assert v_none.direction == "NONE"


def test_explicit_none_with_inconsistent_active_flag_fails_closed():
    verification = MultimodalTradeVerification(
        signal_type="NONE",
        is_trade_active=True,
        direction="BUY",
        confidence=0.95,
    )

    assert verification.signal_type == "NONE"
    assert verification.is_trade_active is False
    assert verification.direction == "NONE"


def test_legacy_response_without_signal_type_remains_backward_compatible():
    verification = MultimodalTradeVerification(
        is_trade_active=True,
        direction="SELL",
        confidence=0.95,
    )

    assert verification.signal_type == "ENTRY"
    assert verification.is_trade_active is True
    assert verification.direction == "SELL"


def test_management_signal_does_not_fabricate_unknown_direction():
    verification = MultimodalTradeVerification(
        signal_type="MOVE_STOP",
        direction="NONE",
        confidence=0.95,
    )

    assert verification.is_trade_active is True
    assert verification.direction == "NONE"


def test_multimodal_verifier_audited_entry_phrase():
    """Audited entry: 'You can enter with one lot. I am taking a small scalp.'"""
    verifier = MultimodalVerifier(api_key="mock-key", config=AIConfig())
    json_str = """
    {
      "signal_type": "ENTRY",
      "is_trade_active": true,
      "direction": "BUY",
      "instrument": "GOLD",
      "entry_price": "2650.50",
      "stop_loss": "2645.00",
      "target": "2660.00",
      "trigger_quote": "You can enter with one lot. I am taking a small scalp.",
      "confidence": 0.94,
      "reasoning": "Trader directly instructed entry with one lot on gold scalp"
    }
    """
    res = verifier._parse_response(json_str)
    assert res.signal_type == "ENTRY"
    assert res.is_trade_active is True
    assert res.direction == "BUY"
    assert res.instrument == "GOLD"
    assert res.confidence == 0.94


def test_multimodal_verifier_audited_management_phrases():
    """Audited management: 'Book some profit and move SL to CTC.' and 'My remaining trade is closed/cut.'"""
    verifier = MultimodalVerifier(api_key="mock-key", config=AIConfig())

    # Partial exit & stop to CTC
    json_mgmt = """
    {
      "signal_type": "PARTIAL_EXIT",
      "is_trade_active": true,
      "direction": "BUY",
      "instrument": "GOLD",
      "stop_loss": "CTC",
      "trigger_quote": "Book some profit and move SL to CTC.",
      "confidence": 0.91,
      "reasoning": "Trader instructed partial booking and trailing stop loss to entry price"
    }
    """
    res_mgmt = verifier._parse_response(json_mgmt)
    assert res_mgmt.signal_type == "PARTIAL_EXIT"
    assert res_mgmt.is_trade_active is True
    assert res_mgmt.stop_loss == "CTC"

    # Explicit exit
    json_exit = """
    {
      "signal_type": "EXIT",
      "is_trade_active": false,
      "direction": "BUY",
      "instrument": "GOLD",
      "trigger_quote": "My remaining trade is closed/cut.",
      "confidence": 0.93,
      "reasoning": "Trader confirmed closing/cutting the remaining active position"
    }
    """
    res_exit = verifier._parse_response(json_exit)
    assert res_exit.signal_type == "EXIT"
    assert res_exit.is_trade_active is False


def test_multimodal_verifier_negative_conditional_and_viewer_statements():
    """Negative examples around level 37, conditional statements, and viewer requests must be rejected."""
    verifier = MultimodalVerifier(api_key="mock-key", config=AIConfig(confidence_threshold=0.70))

    # Conditional statement
    json_cond = """
    {
      "signal_type": "NONE",
      "is_trade_active": false,
      "direction": "NONE",
      "instrument": "GOLD",
      "trigger_quote": "If 37 level breaks then we will look to buy.",
      "confidence": 0.10,
      "reasoning": "Hypothetical condition, no active order execution"
    }
    """
    res_cond = verifier._parse_response(json_cond)
    assert res_cond.signal_type == "NONE"
    assert res_cond.is_trade_active is False

    # Educational / retrospective analysis around 37
    json_edu = """
    {
      "signal_type": "NONE",
      "is_trade_active": false,
      "direction": "NONE",
      "instrument": "NIFTY",
      "trigger_quote": "Yesterday 37 level was acting as major resistance.",
      "confidence": 0.05,
      "reasoning": "Retrospective discussion of historical level, not a live trade"
    }
    """
    res_edu = verifier._parse_response(json_edu)
    assert res_edu.signal_type == "NONE"
    assert res_edu.is_trade_active is False

    # Viewer request in chat
    json_viewer = """
    {
      "signal_type": "NONE",
      "is_trade_active": false,
      "direction": "NONE",
      "instrument": "TATA MOTORS",
      "trigger_quote": "Viewer in chat is asking about what to do at 37.",
      "confidence": 0.0,
      "reasoning": "Responding to viewer request, no live trade taken by trader"
    }
    """
    res_viewer = verifier._parse_response(json_viewer)
    assert res_viewer.signal_type == "NONE"
    assert res_viewer.is_trade_active is False


def test_multimodal_prompt_explicitly_rejects_negated_entry_language():
    from src.multimodal_verifier import MULTIMODAL_PROMPT_TEMPLATE

    assert "Do not enter" in MULTIMODAL_PROMPT_TEMPLATE
    assert "Don't buy yet" in MULTIMODAL_PROMPT_TEMPLATE
