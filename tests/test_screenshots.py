import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import config
from screenshots import (ScreenshotSettings, capture_chart, chart_url_for_timeframe,
                         send_alert_chart, PNG_SIGNATURE, MAX_IMAGE_BYTES)


class ScreenshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = ScreenshotSettings(enabled=True, token="private-test-token")

    async def test_profile_authenticated_request_returns_png_without_token_in_url(self):
        def respond(request):
            self.assertEqual(request.headers["authorization"], "Bearer private-test-token")
            self.assertNotIn("private-test-token", str(request.url))
            self.assertEqual(request.url.params["profile"], "tradingview-test")
            body = json.loads(request.content)
            self.assertIn("symbol=OANDA%3AXAUUSD&interval=60", body["context"]["url"])
            self.assertEqual(body["context"]["intervalLabels"], ["60", "1h"])
            return httpx.Response(200, headers={"content-type": "image/png"}, content=PNG_SIGNATURE + b"test")
        image = await capture_chart(self.settings, "1h", transport=httpx.MockTransport(respond))
        self.assertTrue(image.startswith(PNG_SIGNATURE))
        self.assertNotIn("private-test-token", repr(self.settings))

    async def test_http_errors_and_invalid_images_fail_without_retry(self):
        responses = [
            httpx.Response(401), httpx.Response(429), httpx.Response(503),
            httpx.Response(200, headers={"content-type": "text/html"}, content=b"login"),
            httpx.Response(200, headers={"content-type": "image/png"}, content=b"not PNG"),
            httpx.Response(200, headers={"content-type": "image/png"}, content=PNG_SIGNATURE),
            httpx.Response(200, headers={"content-type": "image/png"}, content=PNG_SIGNATURE + b"x" * MAX_IMAGE_BYTES),
        ]
        for response in responses:
            with self.subTest(status=response.status_code, size=len(response.content)):
                calls = []
                def respond(request):
                    calls.append(request)
                    return response
                with self.assertRaises(ValueError):
                    await capture_chart(self.settings, "15m", transport=httpx.MockTransport(respond))
                self.assertEqual(len(calls), 1)

    async def test_network_timeout_is_contained_and_log_does_not_expose_secret(self):
        bot = AsyncMock()
        with patch("screenshots.capture_chart", side_effect=httpx.ReadTimeout("private-test-token")):
            with self.assertLogs("xauusd_bot", level="WARNING") as logs:
                self.assertFalse(await send_alert_chart(bot, "15m", "12:00 UTC", "Twelve Data", settings=self.settings))
        self.assertNotIn("private-test-token", "".join(logs.output))
        bot.send_photo.assert_not_awaited()

    async def test_disabled_never_contacts_provider(self):
        with patch("screenshots.capture_chart", new_callable=AsyncMock) as capture:
            self.assertFalse(await send_alert_chart(AsyncMock(), "15m", "12:00 UTC", "feed", settings=ScreenshotSettings()))
            capture.assert_not_awaited()

    async def test_photo_is_restricted_and_caption_plain_text(self):
        bot = AsyncMock()
        with patch("screenshots.capture_chart", return_value=PNG_SIGNATURE + b"test"):
            self.assertTrue(await send_alert_chart(bot, "15m", "12:00 UTC", "feed_[]" * 1000, settings=self.settings))
        kwargs = bot.send_photo.call_args.kwargs
        self.assertEqual(kwargs["chat_id"], config.ALLOWED_USER_ID)
        self.assertNotIn("parse_mode", kwargs)
        self.assertLessEqual(len(kwargs["caption"]), 500)

    async def test_photo_failure_and_cancellation(self):
        bot = AsyncMock()
        bot.send_photo.side_effect = RuntimeError("private-test-token")
        with patch("screenshots.capture_chart", return_value=PNG_SIGNATURE + b"test"):
            self.assertFalse(await send_alert_chart(bot, "15m", "12:00 UTC", "feed", settings=self.settings))
        with patch("screenshots.capture_chart", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await send_alert_chart(bot, "15m", "12:00 UTC", "feed", settings=self.settings)

    def test_saved_layout_is_preserved_but_symbol_and_interval_are_forced(self):
        url = chart_url_for_timeframe("https://www.tradingview.com/chart/example/?symbol=BTCUSD&interval=1", "4h")
        self.assertEqual(url, "https://www.tradingview.com/chart/example/?symbol=OANDA%3AXAUUSD&interval=240")
        with self.assertRaises(KeyError):
            chart_url_for_timeframe(self.settings.chart_url, "unknown")

    def test_invalid_destination_rejected_before_credentials_are_sent(self):
        for endpoint in ("http://production-sfo.browserless.io", "https://example.com", "https://browserless.io.evil.test", "https://production-sfo.browserless.io/?token=oops"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                ScreenshotSettings(token="secret", endpoint=endpoint).validate()


if __name__ == "__main__":
    unittest.main()
