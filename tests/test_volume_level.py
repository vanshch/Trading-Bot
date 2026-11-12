import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import config
import volume_level
from alerts import AlertManager
from tradingview_feed import SOURCE


def make_candle(start_ts: int, volume: float, source: str = SOURCE):
    return {
        "source": source,
        "start_timestamp": start_ts,
        "timestamp": str(start_ts + 3600),
        "volume": volume,
    }


class VolumeLevelUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.alerts_file = Path(self.temp_dir.name) / "alerts.json"
        self.manager = AlertManager(file_path=self.alerts_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_parse_ist_time_formats(self):
        self.assertEqual(volume_level.parse_ist_time("14:00"), (14, 0))
        self.assertEqual(volume_level.parse_ist_time("14:30"), (14, 30))
        self.assertEqual(volume_level.parse_ist_time("14"), (14, 0))
        self.assertEqual(volume_level.parse_ist_time("2"), (2, 0))
        self.assertEqual(volume_level.parse_ist_time("02:15"), (2, 15))
        self.assertEqual(volume_level.parse_ist_time("00:00"), (0, 0))
        self.assertEqual(volume_level.parse_ist_time("23:59"), (23, 59))

    def test_parse_ist_time_invalid(self):
        for invalid in ["24:00", "-1", "abc", "14:60", "14:00:00", ""]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    volume_level.parse_ist_time(invalid)

    def test_find_target_ist_candle_matching(self):
        base_utc_ts = 1794474000  # 09:00 UTC = 14:30 IST
        c1 = make_candle(base_utc_ts - 7200, 1000.0)  # 07:00 UTC = 12:30 IST
        c2 = make_candle(base_utc_ts - 3600, 2000.0)  # 08:00 UTC = 13:30 IST (covers 13:30 - 14:30 IST)
        c3 = make_candle(base_utc_ts, 3500.0)         # 09:00 UTC = 14:30 IST (covers 14:30 - 15:30 IST)

        candles = [c1, c2, c3]

        # Exact match 14:30 -> c3
        matched = volume_level.find_target_ist_candle(candles, 14, 30)
        self.assertEqual(matched["start_timestamp"], c3["start_timestamp"])
        self.assertEqual(matched["volume"], 3500.0)

        # 14:00 -> spans 13:30 - 14:30 IST, so c2 spans 14:00
        matched_1400 = volume_level.find_target_ist_candle(candles, 14, 0)
        self.assertEqual(matched_1400["start_timestamp"], c2["start_timestamp"])
        self.assertEqual(matched_1400["volume"], 2000.0)

        # Fallback when only c3 exists and user asks for 14
        matched_c3_only = volume_level.find_target_ist_candle([c1, c3], 14, 0)
        self.assertEqual(matched_c3_only["start_timestamp"], c3["start_timestamp"])

    def test_alert_manager_set_and_clear_volume_level(self):
        self.assertFalse(self.manager.get_volume_level()["enabled"])

        self.manager.set_volume_level(
            target_hour_ist="14:30",
            target_volume=1000.0,
            threshold_volume=950.0,
            candle_start_utc=1700000000,
            candle_time_str="14:30 – 15:30 IST (09:00 UTC)",
        )

        state = self.manager.get_volume_level()
        self.assertTrue(state["enabled"])
        self.assertEqual(state["target_volume"], 1000.0)
        self.assertEqual(state["threshold_volume"], 950.0)
        self.assertEqual(state["target_hour_ist"], "14:30")

        # Atomic clear
        self.manager.clear_volume_level()
        state_cleared = self.manager.get_volume_level()
        self.assertFalse(state_cleared["enabled"])

    def test_alert_manager_consume_volume_level_one_shot(self):
        self.manager.set_volume_level(
            target_hour_ist="14:30",
            target_volume=1000.0,
            threshold_volume=950.0,
            candle_start_utc=1700000000,
            candle_time_str="14:30 – 15:30 IST",
        )
        started_at = self.manager.get_volume_level()["armed_after"]

        # First trigger consumption: should succeed and disarm
        res1 = self.manager.consume_volume_level("1h_1700003600", not_before=started_at)
        self.assertTrue(res1)
        self.assertFalse(self.manager.get_volume_level()["enabled"])

        # Second trigger attempt with same or different candle: should fail (already disarmed)
        res2 = self.manager.consume_volume_level("1h_1700003600", not_before=started_at)
        self.assertFalse(res2)


class VolumeLevelAsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.alerts_file = Path(self.temp_dir.name) / "alerts.json"
        self.manager = AlertManager(file_path=self.alerts_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_check_volume_level_triggers_at_95_percent(self):
        self.manager.set_volume_level(
            target_hour_ist="14:30",
            target_volume=1000.0,
            threshold_volume=950.0,
            candle_start_utc=1700000000,
            candle_time_str="14:30 – 15:30 IST",
        )
        started_at = self.manager.get_volume_level()["armed_after"]

        mock_bot = AsyncMock()

        # Forming candle at 949 (below 95%) -> no alert
        forming_below = {
            "source": SOURCE,
            "start_timestamp": 1700003600,
            "volume": 949.0,
        }
        with patch("volume_level.fetch_tradingview", AsyncMock(return_value=([], forming_below))):
            await volume_level.check_volume_level(mock_bot, self.manager, started_at=started_at)
            mock_bot.send_message.assert_not_awaited()
            self.assertTrue(self.manager.get_volume_level()["enabled"])

        # Forming candle at 950 (exactly 95%) -> triggers alert once and disarms
        forming_hit = {
            "source": SOURCE,
            "start_timestamp": 1700003600,
            "volume": 950.0,
        }
        with patch("volume_level.fetch_tradingview", AsyncMock(return_value=([], forming_hit))):
            await volume_level.check_volume_level(mock_bot, self.manager, started_at=started_at)
            mock_bot.send_message.assert_awaited_once()
            self.assertFalse(self.manager.get_volume_level()["enabled"])

        # Subsequent check -> no duplicate alert
        mock_bot.send_message.reset_mock()
        with patch("volume_level.fetch_tradingview", AsyncMock(return_value=([], forming_hit))):
            await volume_level.check_volume_level(mock_bot, self.manager, started_at=started_at)
            mock_bot.send_message.assert_not_awaited()

    async def test_bot_vol_level_command_flow(self):
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=config.ALLOWED_USER_ID),
            message=AsyncMock(),
        )
        context = SimpleNamespace(args=["14:30"])

        c = make_candle(1794474000, 2500.0)
        with patch("bot.alert_manager", self.manager), \
             patch("volume_level.fetch_tradingview", AsyncMock(return_value=([c], None))):
            await bot.vol_level_command(update, context)

            update.message.reply_text.assert_awaited_once()
            call_text = update.message.reply_text.call_args[0][0]
            self.assertIn("Volume Level Alert Armed", call_text)
            self.assertIn("2,500", call_text)
            self.assertIn("2,375", call_text)  # 95% of 2500 is 2375

            state = self.manager.get_volume_level()
            self.assertTrue(state["enabled"])
            self.assertEqual(state["target_volume"], 2500.0)
            self.assertEqual(state["threshold_volume"], 2375.0)

            # Test vol_level_clear
            update.message.reply_text.reset_mock()
            await bot.vol_level_clear_command(update, context)
            update.message.reply_text.assert_awaited_once()
            clear_text = update.message.reply_text.call_args[0][0]
            self.assertIn("Cleared", clear_text)
            self.assertFalse(self.manager.get_volume_level()["enabled"])
