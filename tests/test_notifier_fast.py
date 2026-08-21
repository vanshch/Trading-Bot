"""Unit tests for TelegramNotifier fast audio alerts."""

from src.config import TelegramConfig
from src.multimodal_verifier import MultimodalTradeVerification
from src.notifier import TelegramNotifier


def test_format_fast_audio_trade_message():
    notifier = TelegramNotifier(
        bot_token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        chat_id="987654321",
        config=TelegramConfig(),
    )

    verification = MultimodalTradeVerification(
        is_trade_active=True,
        direction="CALL",
        instrument="NIFTY 24500 CE",
        entry_price="120",
        stop_loss="95",
        target="160",
        trigger_quote="Haan entry bana lo yahan pe call mein",
        confidence=0.95,
        reasoning="Spoken confirmation + chart breakout",
    )

    msg = notifier.format_fast_audio_trade_message(
        channel_name="Scalper Pro",
        verification=verification,
        stream_url="https://youtube.com/watch?v=sample123",
        elapsed_seconds=3665,  # 1h 1m 5s
    )

    assert "LIVE TRADE SIGNAL: Scalper Pro" in msg
    assert "NIFTY 24500 CE" in msg
    assert "CALL (CE)" in msg
    assert "120" in msg
    assert "SL: 95" in msg
    assert "TGT: 160" in msg
    assert "Haan entry bana lo yahan pe call mein" in msg
    assert "t=1h1m5s" in msg
    assert "Conf: 95%" in msg
