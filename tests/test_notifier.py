"""Unit tests for Telegram alert notification formatting, photo dispatch, and retries."""

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import TelegramConfig
from src.notifier import TelegramNotifier
from src.state_tracker import TradeInfo


class TestTelegramNotifier(unittest.IsolatedAsyncioTestCase):
    """Tests for TelegramNotifier message formatting and dispatch."""

    def setUp(self) -> None:
        self.bot_token = "123456789:ABCDefGhIjKlMnOpQrStUvWxYz_12345678"
        self.chat_id = "987654321"
        self.config = TelegramConfig(
            include_screenshot=True,
            caption_timezone="UTC",
            max_retries=2,
            retry_delay_seconds=0.01,
            timeout_seconds=5.0,
        )
        self.notifier = TelegramNotifier(
            bot_token=self.bot_token,
            chat_id=self.chat_id,
            config=self.config,
        )

        self.sample_trade = TradeInfo(
            channel_name="Sample Trader",
            direction="BUY",
            instrument="NIFTY 50",
            entry_or_pnl="Entry: 24,500 | Target: 24,600",
            confidence=0.92,
            reasoning="Open long position on 5m chart",
            opened_at=datetime(2026, 8, 15, 13, 0, 0, tzinfo=timezone.utc),
            last_seen_at=datetime(2026, 8, 15, 13, 15, 0, tzinfo=timezone.utc),
            closed_at=datetime(2026, 8, 15, 13, 15, 0, tzinfo=timezone.utc),
            last_frame_bytes=b"fake_jpeg_bytes",
        )

    async def asyncTearDown(self) -> None:
        await self.notifier.close()

    def test_format_new_trade_message(self) -> None:
        msg = self.notifier.format_new_trade_message(
            self.sample_trade, "https://youtube.com/live/xyz"
        )
        self.assertIn("TRADE DETECTED: Sample Trader", msg)
        self.assertIn("NIFTY 50", msg)
        self.assertIn("BUY (Long)", msg)
        self.assertIn("Entry: 24,500", msg)
        self.assertIn("https://youtube.com/live/xyz", msg)

    def test_format_trade_closed_message(self) -> None:
        msg = self.notifier.format_trade_closed_message(
            self.sample_trade, "https://youtube.com/live/xyz"
        )
        self.assertIn("TRADE CLOSED: Sample Trader", msg)
        self.assertIn("NIFTY 50", msg)
        self.assertIn("Duration:", msg)
        self.assertIn("15m 0s", msg)

    def test_untrusted_trade_text_is_html_escaped(self) -> None:
        self.sample_trade.instrument = "<script>alert(1)</script>"
        self.sample_trade.reasoning = "<b>not formatting</b>"

        msg = self.notifier.format_new_trade_message(
            self.sample_trade, "https://youtube.com/live/xyz"
        )

        self.assertNotIn("<script>", msg)
        self.assertIn("&lt;script&gt;", msg)
        self.assertIn("&lt;b&gt;not formatting&lt;/b&gt;", msg)

    @patch("aiohttp.ClientSession.post")
    async def test_send_new_trade_with_photo_success(
        self, mock_post: MagicMock
    ) -> None:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__.return_value = mock_response
        mock_post.return_value = mock_response

        result = await self.notifier.send_new_trade_alert(
            trade=self.sample_trade,
            stream_url="https://youtube.com/live/xyz",
            frame_bytes=b"jpeg_data",
        )
        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 1)
        # Check endpoint was sendPhoto
        call_url = mock_post.call_args[0][0]
        self.assertIn("sendPhoto", call_url)

    @patch("aiohttp.ClientSession.post")
    async def test_send_trade_closed_alert_success(self, mock_post: MagicMock) -> None:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__.return_value = mock_response
        mock_post.return_value = mock_response

        result = await self.notifier.send_trade_closed_alert(
            trade=self.sample_trade,
            stream_url="https://youtube.com/live/xyz",
        )
        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 1)
        call_url = mock_post.call_args[0][0]
        self.assertIn("sendMessage", call_url)

    @patch("aiohttp.ClientSession.post")
    async def test_retry_on_network_error(self, mock_post: MagicMock) -> None:
        mock_resp_fail = AsyncMock()
        mock_resp_fail.status = 502
        mock_resp_fail.text.return_value = "Bad Gateway"
        mock_resp_fail.__aenter__.return_value = mock_resp_fail

        mock_resp_ok = AsyncMock()
        mock_resp_ok.status = 200
        mock_resp_ok.__aenter__.return_value = mock_resp_ok

        mock_post.side_effect = [mock_resp_fail, mock_resp_ok]

        result = await self.notifier.send_trade_closed_alert(
            trade=self.sample_trade,
            stream_url="https://youtube.com/live/xyz",
        )
        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 2)

    @patch("aiohttp.ClientSession.post")
    async def test_exhausted_retries_fail_closed(self, mock_post: MagicMock) -> None:
        mock_resp_fail = AsyncMock()
        mock_resp_fail.status = 500
        mock_resp_fail.text.return_value = "Internal Server Error"
        mock_resp_fail.__aenter__.return_value = mock_resp_fail

        mock_post.return_value = mock_resp_fail

        result = await self.notifier.send_trade_closed_alert(
            trade=self.sample_trade,
            stream_url="https://youtube.com/live/xyz",
        )
        self.assertFalse(result)
        self.assertEqual(mock_post.call_count, 2)

    def test_format_stream_live_message(self) -> None:
        msg = self.notifier.format_stream_live_message(
            channel_name="Mehak Trades",
            stream_url="https://youtube.com/@tradewithmehaktrades/live",
            title="Live Scalping & Gold Analysis",
        )
        self.assertIn("STREAM IS LIVE: Mehak Trades", msg)
        self.assertIn("Live Scalping &amp; Gold Analysis", msg)
        self.assertIn("https://youtube.com/@tradewithmehaktrades/live", msg)
        self.assertIn("Trade bot listener is actively monitoring", msg)

    def test_format_stream_ended_message(self) -> None:
        msg = self.notifier.format_stream_ended_message(
            channel_name="Mehak Trades",
            stream_url="https://youtube.com/@tradewithmehaktrades/live",
        )
        self.assertIn("STREAM ENDED: Mehak Trades", msg)
        self.assertIn("https://youtube.com/@tradewithmehaktrades/live", msg)

    @patch("aiohttp.ClientSession.post")
    async def test_send_stream_live_alert_success(self, mock_post: MagicMock) -> None:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__.return_value = mock_response
        mock_post.return_value = mock_response

        result = await self.notifier.send_stream_live_alert(
            channel_name="Mehak Trades",
            stream_url="https://youtube.com/@tradewithmehaktrades/live",
        )
        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 1)
        call_url = mock_post.call_args[0][0]
        self.assertIn("sendMessage", call_url)

    @patch("aiohttp.ClientSession.post")
    async def test_send_stream_ended_alert_success(self, mock_post: MagicMock) -> None:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__.return_value = mock_response
        mock_post.return_value = mock_response

        result = await self.notifier.send_stream_ended_alert(
            channel_name="Mehak Trades",
            stream_url="https://youtube.com/@tradewithmehaktrades/live",
        )
        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
