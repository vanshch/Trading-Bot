"""Unit tests for MultimodalTradeVerification and MultimodalVerifier."""

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
