import asyncio
import math
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import config
import feed
import tradingview_feed as tv


def bar(ts, **overrides):
    return SimpleNamespace(**(dict(timestamp=ts, open=2735.0, high=2737.0,
                                   low=2734.0, close=2736.0, volume=1.0) | overrides))


class NormalizationTests(unittest.TestCase):
    def test_closed_end_timestamp_forming_exclusion_and_no_offset(self):
        closed, forming = tv.normalize_bars([bar(900), bar(1800), bar(2700)], "15m", 1, 2710)
        self.assertEqual([c["timestamp"] for c in closed], ["2700"])
        self.assertEqual(forming["timestamp"], "2700")
        self.assertEqual(closed[0]["close"], 2736.0)
        self.assertEqual(closed[0]["pips"], 10.0)
        self.assertEqual(closed[0]["source"], tv.SOURCE)

    def test_session_aligned_four_hour_bar_uses_own_start(self):
        closed, forming = tv.normalize_bars([bar(3600)], "4h", 2, 18000)
        self.assertEqual(closed[0]["timestamp"], "18000")
        self.assertIsNone(forming)

    def test_invalid_ohlcv_timestamps_and_order_rejected(self):
        cases = ([bar(900), bar(900)], [bar(1800), bar(900)], [bar(900.5)],
                 [bar(4000)], [bar(900, close=math.nan)], [bar(900, low=2738)],
                 [bar(900, volume=-1)], [bar(900, open=-1)], [bar(math.inf)])
        for bars in cases:
            with self.subTest(bars=bars), self.assertRaises(ValueError):
                tv.normalize_bars(bars, "15m", 2, 3000)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def fake_client(self, *, identity=None, bars=None, error=None, wait=False):
        class Client:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.identities = [identity or {"full_name": tv.SYMBOL, "source_id": "OANDA"}]
                self.closed = False
                instances.append(self)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            async def get_historical_ohlcv(self, symbol, interval, bars_count):
                self.request = (symbol, interval, bars_count)
                if wait:
                    await asyncio.Event().wait()
                if error:
                    raise error
                return bars if bars is not None else [bar(900), bar(1800), bar(2700)]
        instances = []
        return Client, instances

    async def test_source_resolution_all_timeframes_and_anonymous_cleanup(self):
        for tf, interval in tv.INTERVALS.items():
            client, instances = self.fake_client(bars=[bar(3600)])
            with patch.object(tv, "_client_type", return_value=client), patch.object(tv.time, "time", return_value=36000):
                closed, _ = await tv.fetch_tradingview(tf)
            self.assertTrue(closed)
            self.assertEqual(instances[0].request, (tv.SYMBOL, interval, 60))
            self.assertEqual(instances[0].kwargs["auth_token"], "unauthorized_user_token")
            self.assertTrue(instances[0].closed)

    async def test_wrong_or_missing_identity_rejected(self):
        for identity in ({"full_name": "BINANCE:BTCUSDT", "source_id": "BINANCE"},
                         {"full_name": tv.SYMBOL, "source_id": "OTHER"}):
            client, _ = self.fake_client(identity=identity)
            with patch.object(tv, "_client_type", return_value=client):
                self.assertEqual(await tv.fetch_tradingview(), ([], None))
        client, instances = self.fake_client()
        original = client.__aenter__
        async def unverified(instance):
            await original(instance)
            instance.identities = []
            return instance
        client.__aenter__ = unverified
        with patch.object(tv, "_client_type", return_value=client):
            self.assertEqual(await tv.fetch_tradingview(), ([], None))
        self.assertTrue(instances[0].closed)

    async def test_timeout_and_provider_error_cleanup_and_redacted_logging(self):
        for settings in (dict(wait=True), dict(error=OSError("secret-token-in-url"))):
            client, instances = self.fake_client(**settings)
            with patch.object(tv, "_client_type", return_value=client), \
                    patch.object(tv, "REQUEST_TIMEOUT_SECONDS", 0.01), self.assertLogs("xauusd_bot", "WARNING") as logs:
                self.assertEqual(await tv.fetch_tradingview(), ([], None))
            self.assertTrue(instances[0].closed)
            self.assertNotIn("secret-token", " ".join(logs.output))

    async def test_cancellation_propagates_and_closes_client(self):
        client, instances = self.fake_client(wait=True)
        with patch.object(tv, "_client_type", return_value=client):
            task = asyncio.create_task(tv.fetch_tradingview())
            while not instances:
                await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(instances[0].closed)

    async def test_missing_dependency_and_old_python_return_fallback(self):
        with patch.object(tv, "_client_type", side_effect=ImportError):
            self.assertEqual(await tv.fetch_tradingview(), ([], None))
        with patch.object(tv.sys, "version_info", (3, 10)):
            self.assertEqual(await tv.fetch_tradingview(), ([], None))

    async def test_empty_or_invalid_data_return_fallback(self):
        for bars in ([], [bar(900, high=1)]):
            client, _ = self.fake_client(bars=bars)
            with patch.object(tv, "_client_type", return_value=client):
                self.assertEqual(await tv.fetch_tradingview(), ([], None))


class FeedRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_provider_keeps_original_route(self):
        candle = {"source": "Twelve Data"}
        with patch.object(config, "TRADINGVIEW_FEED_ENABLED", False), \
                patch.object(tv, "fetch_tradingview", AsyncMock()) as provider, \
                patch.object(feed, "fetch_twelve_data", AsyncMock(return_value=([candle], None))):
            self.assertEqual(await feed.get_latest_closed_candles(), [candle])
            provider.assert_not_awaited()

    async def test_enabled_primary_then_twelve_data_then_yahoo(self):
        for primary, twelve in ((True, False), (False, True), (False, False)):
            candle = {"source": tv.SOURCE if primary else ("Twelve Data" if twelve else "Yahoo Finance (GC=F)")}
            with patch.object(config, "TRADINGVIEW_FEED_ENABLED", True), \
                    patch.object(tv, "fetch_tradingview", AsyncMock(return_value=([candle] if primary else [], None))), \
                    patch.object(feed, "fetch_twelve_data", AsyncMock(return_value=([candle] if twelve else [], None))) as td, \
                    patch.object(feed, "fetch_yahoo_finance", AsyncMock(return_value=([candle], None))) as yahoo:
                self.assertEqual(await feed.get_latest_closed_candles(count=3), [candle])
                self.assertEqual(config.state.last_feed_source, candle["source"])
                self.assertEqual(td.await_count, 0 if primary else 1)
                self.assertEqual(yahoo.await_count, 0 if primary or twelve else 1)

    async def test_forming_bar_falls_back_separately_from_closed_history(self):
        forming = {"source": "Twelve Data"}
        with patch.object(config, "TRADINGVIEW_FEED_ENABLED", True), \
                patch.object(tv, "fetch_tradingview", AsyncMock(return_value=([{"source": tv.SOURCE}], None))), \
                patch.object(feed, "fetch_twelve_data", AsyncMock(return_value=([], forming))):
            self.assertEqual(await feed.get_forming_candle(), forming)
