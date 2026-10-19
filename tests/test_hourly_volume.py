import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import config
import hourly_volume as volume
from alerts import AlertManager


def candles(latest=120, *, baseline=100, shift=1800):
    return [
        {
            "source": volume.SOURCE,
            "start_timestamp": shift + i * 3600,
            "timestamp": str(shift + (i + 1) * 3600),
            "volume": baseline if i < 20 else latest,
        }
        for i in range(21)
    ]


class VolumeRuleTests(unittest.TestCase):
    def test_twenty_previous_bars_exclude_candidate_and_accept_exact_twenty_percent(
        self,
    ):
        result = volume.evaluate_hourly_volume(candles(), now=100000)
        self.assertEqual(result["average"], 100)
        self.assertTrue(result["qualifies"])
        self.assertEqual(result["timestamp"], 77400)

    def test_grazes_below_and_just_short_of_margin_do_not_trigger(self):
        for latest in (0, 99, 100, 100.01, 105, 119.999):
            with self.subTest(latest=latest):
                self.assertFalse(
                    volume.evaluate_hourly_volume(candles(latest), now=100000)[
                        "qualifies"
                    ]
                )

    def test_large_volume_qualifies_with_nonuniform_baseline(self):
        values = candles(300)
        for i, candle in enumerate(values[:-1]):
            candle["volume"] = 10 * (i + 1)
        result = volume.evaluate_hourly_volume(values, now=100000)
        self.assertEqual(result["average"], 105)
        self.assertTrue(result["qualifies"])

    def test_weekend_gaps_and_native_half_hour_starts_are_preserved(self):
        values = candles()
        for candle in values[10:]:
            candle["start_timestamp"] += 172800
            candle["timestamp"] = str(int(candle["timestamp"]) + 172800)
        self.assertTrue(volume.evaluate_hourly_volume(values, now=300000)["qualifies"])

    def test_insufficient_zero_baseline_and_forming_are_rejected(self):
        for values, now in (
            (candles()[:-1], 100000),
            (candles(baseline=0), 100000),
            (candles(), 77399),
        ):
            with self.subTest(now=now), self.assertRaises(ValueError):
                volume.evaluate_hourly_volume(values, now=now)

    def test_invalid_or_mixed_provider_history_is_rejected(self):
        for field, bad in (
            ("source", "Yahoo"),
            ("volume", -1),
            ("volume", float("nan")),
            ("volume", float("inf")),
            ("timestamp", "bad"),
            ("timestamp", "1"),
            ("start_timestamp", 0),
            ("start_timestamp", float("inf")),
        ):
            values = candles()
            values[10][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                volume.evaluate_hourly_volume(values, now=100000)
        values = candles()
        values[5], values[6] = values[6], values[5]
        with self.assertRaises(ValueError):
            volume.evaluate_hourly_volume(values, now=100000)


class AlarmTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "alerts.json"
        self.manager = AlertManager(self.path)
        self.telegram = SimpleNamespace(send_message=AsyncMock())

    def arm(self, at=77000):
        with patch("alerts.time.time", return_value=at):
            return self.manager.toggle_hourly_volume()

    async def check(self, values=None, *, started_at=76000, forming=None, now=78000):
        with (
            patch.object(
                volume,
                "fetch_tradingview",
                AsyncMock(
                    return_value=(candles() if values is None else values, forming)
                ),
            ) as fetch,
            patch.object(volume.time, "time", return_value=now),
        ):
            await volume.check_hourly_volume(
                self.telegram, self.manager, started_at=started_at
            )
        return fetch

    async def test_disabled_alarm_does_not_fetch(self):
        fetch = await self.check()
        fetch.assert_not_awaited()

    async def test_arm_persist_fire_once_and_rearm_without_replaying_same_close(self):
        self.assertTrue(self.arm())
        self.manager = AlertManager(self.path)
        self.assertTrue(self.manager.is_hourly_volume_enabled())
        fetch = await self.check(forming=dict(candles()[-1], volume=100000))
        fetch.assert_awaited_once_with("1h", max_closed=21)
        self.telegram.send_message.assert_awaited_once()
        text = self.telegram.send_message.call_args.kwargs["text"]
        self.assertIn("High-volume candle occurred", text)
        self.assertIn("20.00%", text)
        self.assertEqual(
            self.telegram.send_message.call_args.kwargs["chat_id"],
            config.ALLOWED_USER_ID,
        )
        self.assertFalse(self.manager.is_hourly_volume_enabled())
        self.assertTrue(self.arm(at=78000))
        await self.check()
        self.telegram.send_message.assert_awaited_once()
        self.assertTrue(self.manager.is_hourly_volume_enabled())

    async def test_grazing_closed_bar_and_large_forming_bar_leave_alarm_armed(self):
        self.arm()
        await self.check(candles(101), forming=dict(candles()[-1], volume=999999))
        self.telegram.send_message.assert_not_awaited()
        self.assertTrue(self.manager.is_hourly_volume_enabled())

    async def test_alarm_survives_graze_and_fires_on_next_qualifying_closed_hour(self):
        self.arm()
        await self.check(candles(101))
        await self.check(candles(121, shift=5400), now=82000)
        self.telegram.send_message.assert_awaited_once()
        self.assertFalse(self.manager.is_hourly_volume_enabled())

    async def test_unavailable_and_invalid_history_keep_alarm_armed_without_fallback(
        self,
    ):
        self.arm()
        for values in (
            [],
            candles()[:-1],
            [dict(c, source="Twelve Data") for c in candles()],
        ):
            await self.check(values)
            self.assertTrue(self.manager.is_hourly_volume_enabled())
        self.telegram.send_message.assert_not_awaited()

    async def test_pre_arm_pre_restart_equal_and_older_closes_are_not_replayed(self):
        for cutoff in (77400, 78000):
            self.arm(at=100)
            await self.check(started_at=cutoff)
            self.telegram.send_message.assert_not_awaited()
            self.arm(at=100)  # OFF
        self.arm(at=77400)
        await self.check(started_at=100)
        self.telegram.send_message.assert_not_awaited()
        self.assertTrue(self.manager.is_hourly_volume_enabled())

    async def test_duplicate_and_older_checks_cannot_consume_revised_volume(self):
        self.arm()
        await self.check(candles(101))
        await self.check(candles(1000))
        older = candles(1000, shift=0)
        await self.check(older)
        self.telegram.send_message.assert_not_awaited()

    async def test_off_or_rearm_while_fetching_cancels_stale_result(self):
        for rearm in (False, True):
            self.arm()

            async def fetch(*args, rearm=rearm, **kwargs):
                self.arm(at=78000)  # OFF
                if rearm:
                    self.arm(at=78000)
                return candles(), None

            with patch.object(volume, "fetch_tradingview", fetch):
                await volume.check_hourly_volume(
                    self.telegram, self.manager, started_at=76000
                )
            self.telegram.send_message.assert_not_awaited()
            if rearm:
                self.arm()  # OFF

    async def test_failed_persistence_cannot_dispatch_or_report_successful_toggle(self):
        with (
            patch.object(self.manager, "_write", return_value=False),
            self.assertRaises(OSError),
        ):
            self.arm()
        self.arm()
        with (
            patch.object(self.manager, "_write", return_value=False),
            self.assertRaises(OSError),
        ):
            await self.check()
        self.telegram.send_message.assert_not_awaited()
        self.assertTrue(self.manager.is_hourly_volume_enabled())

    async def test_old_state_migrates_and_corrupt_file_recovers_without_enabling_alarm(
        self,
    ):
        for content in (
            '{"settings":{"two_green_candles":true},"price_alerts":[],"next_alert_id":9}',
            "broken",
            "[]",
        ):
            self.path.write_text(content, encoding="utf-8")
            self.assertFalse(self.manager.is_hourly_volume_enabled())
            self.arm()
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.assertTrue(saved["hourly_volume"]["enabled"])
            self.assertNotIn("hourly_volume", saved["settings"])
            self.assertFalse(self.path.with_suffix(".tmp").exists())
            if content.startswith("{"):
                self.assertTrue(saved["settings"]["two_green_candles"])
                self.assertEqual(saved["next_alert_id"], 9)

    async def test_malformed_alarm_state_recovers_disabled_preserving_other_settings(
        self,
    ):
        for alarm in (
            None,
            {},
            {"enabled": "true", "armed_after": 0, "last_processed": 0},
            {"enabled": True, "armed_after": "bad", "last_processed": 0},
            {"enabled": True, "armed_after": float("nan"), "last_processed": 0},
        ):
            self.path.write_text(
                json.dumps(
                    {"settings": {"two_red_candles": True}, "hourly_volume": alarm}
                ),
                encoding="utf-8",
            )
            self.assertFalse(self.manager.is_hourly_volume_enabled())
            self.assertTrue(self.manager.is_setting_enabled("two_red_candles"))

    async def test_authorized_button_and_command_toggle_without_changing_other_alerts(
        self,
    ):
        self.manager.set_setting("two_green_candles", True)
        self.manager.add_price_alert(4318)
        state = config.BotState()
        query = SimpleNamespace(
            data="btn_hourly_volume", answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=config.ALLOWED_USER_ID),
            callback_query=query,
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        with (
            patch.object(bot, "alert_manager", self.manager),
            patch.object(config, "state", state),
        ):
            before_tf = state.current_timeframe
            await bot.button_callback(update, SimpleNamespace())
            self.assertTrue(self.manager.is_hourly_volume_enabled())
            self.assertIn("ON", query.edit_message_text.call_args.kwargs["text"])
            buttons = [
                button for row in bot.get_keyboard().inline_keyboard for button in row
            ]
            self.assertIn(
                "ON",
                next(b.text for b in buttons if b.callback_data == "btn_hourly_volume"),
            )
            self.assertIn("1H Volume", bot.build_status_text())
            await bot.hourly_volume_command(update, SimpleNamespace())
            self.assertFalse(self.manager.is_hourly_volume_enabled())
            self.assertEqual(state.current_timeframe, before_tf)
        self.assertTrue(self.manager.is_setting_enabled("two_green_candles"))
        self.assertEqual(len(self.manager.get_active_price_alerts()), 1)

    async def test_unauthorized_button_and_command_are_silent(self):
        query = SimpleNamespace(data="btn_hourly_volume", answer=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=config.ALLOWED_USER_ID + 1),
            callback_query=query,
            message=SimpleNamespace(reply_text=AsyncMock()),
        )
        with patch.object(bot, "alert_manager", self.manager):
            await bot.button_callback(update, SimpleNamespace())
            await bot.hourly_volume_command(update, SimpleNamespace())
        query.answer.assert_not_awaited()
        update.message.reply_text.assert_not_awaited()
        self.assertFalse(self.manager.is_hourly_volume_enabled())

    async def test_hourly_loop_uses_hour_boundary_survives_failure_and_cancels(self):
        with (
            patch.object(bot, "calculate_next_wait_seconds", return_value=123) as wait,
            patch.object(
                bot.asyncio,
                "sleep",
                AsyncMock(side_effect=[None, None, asyncio.CancelledError]),
            ),
            patch.object(
                bot,
                "check_hourly_volume",
                AsyncMock(side_effect=[OSError("secret"), None]),
            ) as check,
            self.assertLogs("xauusd_bot", "WARNING") as logs,
        ):
            await bot.hourly_volume_scheduler(
                SimpleNamespace(bot=self.telegram), started_at=76000
            )
        self.assertNotIn("secret", " ".join(logs.output))
        self.assertEqual(check.await_count, 2)
        self.assertTrue(all(call.args == ("1h",) for call in wait.call_args_list))


if __name__ == "__main__":
    unittest.main()
