"""Unit tests for Gemini Vision AI verifier with mock responses and structured JSON parsing."""

import unittest
from unittest.mock import MagicMock

import numpy as np

from src.config import AIConfig
from src.vision_verifier import (
    TRADE_VERIFICATION_PROMPT,
    TradeVerification,
    VisionVerifier,
    extract_json_from_text,
)


class TestExtractJson(unittest.TestCase):
    """Tests for deterministic JSON extraction from LLM responses."""

    def test_extract_pure_json(self) -> None:
        raw = '{"is_trade_active": true, "direction": "BUY", "instrument": "BTCUSDT", "confidence": 0.9}'
        res = extract_json_from_text(raw)
        self.assertTrue(res["is_trade_active"])
        self.assertEqual(res["direction"], "BUY")

    def test_extract_markdown_fenced_json(self) -> None:
        raw = """```json
{
  "is_trade_active": true,
  "direction": "SELL",
  "instrument": "NIFTY 50",
  "confidence": 0.85
}
```"""
        res = extract_json_from_text(raw)
        self.assertTrue(res["is_trade_active"])
        self.assertEqual(res["direction"], "SELL")

    def test_extract_json_with_surrounding_commentary(self) -> None:
        raw = """Here is the trade analysis from the frame:
```json
{
  "is_trade_active": false,
  "direction": "NONE",
  "instrument": "EURUSD",
  "confidence": 0.2
}
```
Hope this helps!"""
        res = extract_json_from_text(raw)
        self.assertFalse(res["is_trade_active"])
        self.assertEqual(res["direction"], "NONE")


class TestTradeVerificationModel(unittest.TestCase):
    """Tests for Pydantic TradeVerification schema."""

    def test_direction_normalization(self) -> None:
        v1 = TradeVerification(is_trade_active=True, direction="Long", confidence=0.8)
        self.assertEqual(v1.direction, "BUY")

        v2 = TradeVerification(is_trade_active=True, direction="short", confidence=0.8)
        self.assertEqual(v2.direction, "SELL")

        v3 = TradeVerification(
            is_trade_active=False, direction="unknown", confidence=0.1
        )
        self.assertEqual(v3.direction, "NONE")

    def test_confidence_clamping(self) -> None:
        v_high = TradeVerification(
            is_trade_active=True, direction="BUY", confidence=1.5
        )
        self.assertEqual(v_high.confidence, 1.0)

        v_low = TradeVerification(is_trade_active=False, confidence=-0.5)
        self.assertEqual(v_low.confidence, 0.0)


class TestVisionVerifierAsync(unittest.IsolatedAsyncioTestCase):
    """Tests for VisionVerifier API calls with mock client."""

    def setUp(self) -> None:
        self.config = AIConfig(
            model_name="gemini-3.6-flash",
            confidence_threshold=0.70,
            max_retries=2,
            retry_delay_seconds=0.01,
            timeout_seconds=5.0,
        )
        self.mock_client = MagicMock()
        self.verifier = VisionVerifier(
            api_key="mock_key_test",
            config=self.config,
            client=self.mock_client,
        )
        # Synthetic test frame
        self.frame = np.zeros((100, 100, 3), dtype=np.uint8)

    async def test_successful_buy_verification(self) -> None:
        mock_response = MagicMock()
        mock_response.text = """{
            "is_trade_active": true,
            "direction": "BUY",
            "instrument": "NIFTY",
            "entry_or_pnl": "Entry at 24500",
            "confidence": 0.92,
            "reasoning": "Clear blue buy horizontal line"
        }"""
        self.mock_client.models.generate_content.return_value = mock_response

        res = await self.verifier.verify_frame(self.frame)
        self.assertTrue(res.is_trade_active)
        self.assertEqual(res.direction, "BUY")
        self.assertEqual(res.instrument, "NIFTY")
        self.assertAlmostEqual(res.confidence, 0.92)
        call_config = self.mock_client.models.generate_content.call_args.kwargs[
            "config"
        ]
        self.assertIs(call_config.response_schema, TradeVerification)
        self.assertEqual(call_config.http_options.timeout, 5000)

    async def test_confidence_below_threshold_suppressed(self) -> None:
        mock_response = MagicMock()
        mock_response.text = """{
            "is_trade_active": true,
            "direction": "SELL",
            "instrument": "BANKNIFTY",
            "confidence": 0.55,
            "reasoning": "Uncertain faint line"
        }"""
        self.mock_client.models.generate_content.return_value = mock_response

        res = await self.verifier.verify_frame(self.frame)
        # Suppressed because 0.55 < threshold (0.70)
        self.assertFalse(res.is_trade_active)
        self.assertEqual(res.direction, "NONE")

    async def test_retry_on_transient_failure(self) -> None:
        # First call fails, second call succeeds
        mock_response = MagicMock()
        mock_response.text = (
            '{"is_trade_active": true, "direction": "BUY", "confidence": 0.85}'
        )

        self.mock_client.models.generate_content.side_effect = [
            Exception("503 Service Unavailable"),
            mock_response,
        ]

        res = await self.verifier.verify_frame(self.frame)
        self.assertTrue(res.is_trade_active)
        self.assertEqual(self.mock_client.models.generate_content.call_count, 2)

    async def test_exhausted_retries_fail_closed(self) -> None:
        self.mock_client.models.generate_content.side_effect = Exception(
            "429 Rate limit exceeded"
        )

        res = await self.verifier.verify_frame(self.frame)
        self.assertFalse(res.is_trade_active)
        self.assertEqual(res.confidence, 0.0)
        self.assertIn("unavailable", res.reasoning)
        self.assertEqual(self.mock_client.models.generate_content.call_count, 2)

    async def test_permanent_error_is_not_retried(self) -> None:
        self.mock_client.models.generate_content.side_effect = Exception(
            "401 invalid API key"
        )

        res = await self.verifier.verify_frame(self.frame)

        self.assertFalse(res.is_trade_active)
        self.assertEqual(self.mock_client.models.generate_content.call_count, 1)

    async def test_malformed_response_handled_gracefully(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "This is not valid JSON content"
        self.mock_client.models.generate_content.return_value = mock_response

        res = await self.verifier.verify_frame(self.frame)
        self.assertFalse(res.is_trade_active)
        self.assertEqual(res.direction, "NONE")

    def test_trade_verification_prompt_calibration_rules(self) -> None:
        """Prompt explicitly enforces active filled position rules, pending order exclusions, and overlay ignoring."""
        prompt_lower = TRADE_VERIFICATION_PROMPT.lower()
        # Active filled position / PnL / close X
        self.assertIn("filled", prompt_lower)
        self.assertIn("pnl", prompt_lower)
        self.assertIn("close", prompt_lower)
        # Pending limit orders do not count alone
        self.assertIn("pending", prompt_lower)
        self.assertIn("limit", prompt_lower)
        # TradingView risk/reward drawings alone do not prove active trade
        self.assertIn("risk/reward", prompt_lower)
        self.assertIn("drawing", prompt_lower)
        # Overlays to ignore: player chrome, presenter, QR, disclaimer
        self.assertIn("player", prompt_lower)
        self.assertIn("presenter", prompt_lower)
        self.assertIn("qr", prompt_lower)
        self.assertIn("disclaimer", prompt_lower)

    async def test_successful_short_trade_reference_verification(self) -> None:
        """Verify active short trade matching the live reference fixture structure."""
        mock_response = MagicMock()
        mock_response.text = """{
            "is_trade_active": true,
            "direction": "SELL",
            "instrument": "ES",
            "entry_or_pnl": "Short -100 @ 4353.26, PnL +40.00 USD",
            "confidence": 0.95,
            "reasoning": "Genuine active TradingView short position with filled red row, -100 quantity, +40.00 USD PnL and close X"
        }"""
        self.mock_client.models.generate_content.return_value = mock_response

        res = await self.verifier.verify_frame(self.frame)
        self.assertTrue(res.is_trade_active)
        self.assertEqual(res.direction, "SELL")
        self.assertEqual(res.instrument, "ES")
        self.assertIn("4353.26", res.entry_or_pnl)
        self.assertGreaterEqual(res.confidence, 0.70)

    async def test_pending_limit_order_alone_is_not_active_trade(self) -> None:
        """Pending limit order without an active filled position must not count as active trade."""
        mock_response = MagicMock()
        mock_response.text = """{
            "is_trade_active": false,
            "direction": "NONE",
            "instrument": "ES",
            "entry_or_pnl": "Pending Sell Limit 4359.79",
            "confidence": 0.20,
            "reasoning": "Only a resting pending limit order is visible; no filled active position"
        }"""
        self.mock_client.models.generate_content.return_value = mock_response

        res = await self.verifier.verify_frame(self.frame)
        self.assertFalse(res.is_trade_active)
        self.assertEqual(res.direction, "NONE")

    async def test_risk_reward_drawing_alone_is_not_active_trade(self) -> None:
        """TradingView risk/reward projection box alone without filled position is not active trade."""
        mock_response = MagicMock()
        mock_response.text = """{
            "is_trade_active": false,
            "direction": "NONE",
            "instrument": "NQ",
            "entry_or_pnl": "",
            "confidence": 0.15,
            "reasoning": "TradingView short position risk/reward drawing only; no live filled trade badge or PnL"
        }"""
        self.mock_client.models.generate_content.return_value = mock_response

        res = await self.verifier.verify_frame(self.frame)
        self.assertFalse(res.is_trade_active)
        self.assertEqual(res.direction, "NONE")


if __name__ == "__main__":
    unittest.main()
