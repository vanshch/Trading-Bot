import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import config
from alerts import AlertManager


class SchedulerChartTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_seeds_last_close_without_replaying_alerts(self):
        state = config.BotState()
        telegram = AsyncMock()
        def discard_task(coro):
            coro.close()
        with patch.object(config, "state", state), \
                patch.object(bot.feed, "get_latest_candle", AsyncMock(return_value={"timestamp": "100000"})), \
                patch.object(bot.asyncio, "create_task", side_effect=discard_task), \
                patch.object(bot.alert_manager, "check_price_alerts") as evaluate:
            await bot.post_init(SimpleNamespace(bot=telegram))
        self.assertEqual(state.last_alerted_candle_time, "15m_100000")
        evaluate.assert_not_called()

    async def test_startup_without_history_still_starts_scheduler(self):
        state = config.BotState()
        def discard_task(coro):
            coro.close()
        with patch.object(config, "state", state), \
                patch.object(bot.feed, "get_latest_candle", AsyncMock(return_value=None)), \
                patch.object(bot.asyncio, "create_task", side_effect=discard_task) as create:
            await bot.post_init(SimpleNamespace(bot=AsyncMock()))
        self.assertEqual(state.last_alerted_candle_time, "")
        self.assertEqual(create.call_count, 1)

    async def test_older_fallback_candle_does_not_replay_price_alert_evaluation(self):
        latest = dict(timestamp="100000", is_green=True, pips=10)
        older = dict(timestamp="99100", is_green=False, pips=-10)
        waits = 0
        async def boundary_wait(coro, *, timeout):
            nonlocal waits
            coro.close()
            waits += 1
            if waits <= 2:
                raise asyncio.TimeoutError
            raise asyncio.CancelledError
        with tempfile.TemporaryDirectory() as temp:
            manager = AlertManager(Path(temp) / "alerts.json")
            state = config.BotState()
            with patch.object(config, "state", state), patch.object(bot, "alert_manager", manager), \
                    patch.object(bot.feed, "get_latest_closed_candles", AsyncMock(side_effect=[[latest], [older]])), \
                    patch.object(bot.asyncio, "wait_for", boundary_wait), \
                    patch.object(manager, "check_price_alerts", return_value=[]) as evaluate:
                await bot.candle_scheduler(SimpleNamespace(bot=AsyncMock()))
            self.assertEqual(evaluate.call_count, 1)
            self.assertEqual(state.last_alerted_candle_time, "15m_100000")

    async def run_cycle(self, *, momentum=False, price_alerts=False, duplicate=False):
        candle = dict(open=100.0, close=101.0, low=99.0, high=103.0, timestamp="100000",
                      is_green=True, pips=10.0, sign="+", emoji="green", time_str="12:00 UTC")
        candles = [dict(candle, is_green=False, timestamp="98200"),
                   dict(candle, timestamp="99100"), candle]
        if momentum == "red":
            candles = [dict(candle, timestamp="98200"),
                       dict(candle, is_green=False, timestamp="99100"),
                       dict(candle, is_green=False)]
        events = []
        async def text(**kwargs):
            events.append("text")
        async def chart(*args):
            events.append("chart")
        count = 0
        async def boundary_wait(coro, *, timeout):
            nonlocal count
            coro.close()
            count += 1
            if count <= (2 if duplicate else 1):
                raise asyncio.TimeoutError
            raise asyncio.CancelledError
        with tempfile.TemporaryDirectory() as temp:
            manager = AlertManager(Path(temp) / "alerts.json")
            if price_alerts:
                manager.add_price_alert(102.0, current_price=101.0)
                manager.add_price_alert(102.5, current_price=101.0)
            if momentum:
                manager.set_setting("two_red_candles" if momentum == "red" else "two_green_candles", True)
            state = config.BotState()
            with patch.object(config, "state", state), patch.object(bot, "alert_manager", manager), \
                    patch.object(bot.feed, "get_latest_closed_candles", AsyncMock(return_value=candles)), \
                    patch.object(bot.asyncio, "wait_for", boundary_wait), \
                    patch.object(bot, "send_alert_chart", AsyncMock(side_effect=chart)) as screenshot:
                await bot.candle_scheduler(SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(side_effect=text))))
            setting = "two_red_candles" if momentum == "red" else "two_green_candles"
            return events, screenshot.await_count, manager.is_setting_enabled(setting)

    async def test_multiple_alerts_all_send_text_before_single_shared_chart(self):
        events, captures, enabled = await self.run_cycle(momentum=True, price_alerts=True, duplicate=True)
        self.assertEqual(events, ["text", "text", "text", "chart"])
        self.assertEqual(captures, 1)
        self.assertFalse(enabled)

    async def test_red_momentum_sends_chart_and_resets_one_shot(self):
        events, captures, enabled = await self.run_cycle(momentum="red", duplicate=True)
        self.assertEqual(events, ["text", "chart"])
        self.assertEqual(captures, 1)
        self.assertFalse(enabled)

    async def test_no_trigger_never_requests_chart(self):
        events, captures, _ = await self.run_cycle()
        self.assertEqual(events, [])
        self.assertEqual(captures, 0)

    async def test_third_matching_candle_does_not_retrigger_or_capture(self):
        candle = dict(is_green=True, pips=10, timestamp="100000")
        with tempfile.TemporaryDirectory() as temp:
            manager = AlertManager(Path(temp) / "alerts.json")
            manager.set_setting("two_green_candles", True)
            calls = 0
            async def boundary_wait(coro, *, timeout):
                nonlocal calls
                coro.close()
                calls += 1
                if calls == 1:
                    raise asyncio.TimeoutError
                raise asyncio.CancelledError
            with patch.object(config, "state", config.BotState()), patch.object(bot, "alert_manager", manager), \
                    patch.object(bot.feed, "get_latest_closed_candles", AsyncMock(return_value=[candle.copy() for _ in range(3)])), \
                    patch.object(bot.asyncio, "wait_for", boundary_wait), \
                    patch.object(bot, "send_alert_chart", AsyncMock()) as capture:
                telegram = AsyncMock()
                await bot.candle_scheduler(SimpleNamespace(bot=telegram))
                capture.assert_not_awaited()
                telegram.send_message.assert_not_awaited()
