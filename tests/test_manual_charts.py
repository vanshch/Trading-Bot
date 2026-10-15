import asyncio
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
import bot
import config
import hourly_chart as chart
import manual_charts as manual
import tradingview_feed as tv


def candles(count=20, *, flat=False, volume=100):
    bars = [SimpleNamespace(timestamp=3600 * (i + 1), open=4200,
            high=4200 if flat else 4210, low=4200 if flat else 4190,
            close=4200 if flat else (4205 if i % 2 else 4195), volume=volume)
            for i in range(count)]
    return tv.normalize_bars(bars, '1h', count, 3600 * (count + 2))[0]


class ChartTests(unittest.TestCase):
    def test_tick_volume_and_native_start_retained_without_offset(self):
        bars = [SimpleNamespace(timestamp=1800, open=4200, high=4210,
                                low=4190, close=4205, volume=7820)]
        closed, forming = tv.normalize_bars(bars, '1h', 1, 5400)
        self.assertEqual(closed[0]['start_timestamp'], 1800)
        self.assertEqual(closed[0]['timestamp'], '5400')
        self.assertEqual(closed[0]['volume'], 7820)
        self.assertEqual(closed[0]['close'], 4205)
        self.assertIsNone(forming)
        _, forming = tv.normalize_bars(bars, '1h', 1, 5399)
        self.assertEqual(forming['volume'], 7820)

    def test_png_size_colors_and_flat_zero_volume(self):
        for values in (candles(), candles(flat=True, volume=0)):
            image = Image.open(BytesIO(chart.render_hourly_chart(values)))
            self.assertEqual(image.size, chart.SIZE)
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.getpixel((0, 0)), (16, 16, 16))
            colors = {color for _, color in image.getcolors(image.width * image.height)}
            self.assertIn((8, 153, 129), colors)
            if values[0]['volume']:
                self.assertIn((242, 54, 69), colors)

    def test_bad_count_source_volume_and_order_rejected(self):
        for field, value in (('source', 'Yahoo'), ('volume', -1), ('high', 1),
                             ('close', float('nan')), ('start_timestamp', -1)):
            values = candles()
            values[-1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                chart.render_hourly_chart(values)
        with self.assertRaises(ValueError):
            chart.render_hourly_chart(candles(19))


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_twenty_including_latest_forming_or_closed(self):
        closed = candles()
        forming = dict(closed[-1], start_timestamp=closed[-1]['start_timestamp'] + 3600)
        for live in (None, forming):
            with patch.object(chart, 'fetch_tradingview', AsyncMock(return_value=(closed, live))) as fetch:
                values, has_live = await chart.get_hourly_candles()
            fetch.assert_awaited_once_with('1h', max_closed=20)
            self.assertEqual(len(values), 20)
            self.assertEqual(values[-1], live or closed[-1])
            self.assertEqual(has_live, live is not None)
            self.assertEqual(values[0], closed[1] if live else closed[0])

    async def test_missing_history_has_no_fallback(self):
        for values in ([], candles(19)):
            with patch.object(chart, 'fetch_tradingview', AsyncMock(return_value=(values, None))):
                with self.assertRaises(ValueError):
                    await chart.get_hourly_candles()

    async def test_caption_has_actual_last_start_and_snapshot_status(self):
        with patch.object(chart, 'get_hourly_candles', AsyncMock(return_value=(candles(), True))):
            image, caption = await chart.capture_hourly_chart()
        self.assertTrue(image.startswith(b'\x89PNG'))
        self.assertIn('1970-01-01 20:00 UTC (forming)', caption)
        self.assertIn('tick volume', caption)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        pending = []
        application = SimpleNamespace(bot_data={}, create_task=lambda coro: pending.append(coro))
        return SimpleNamespace(application=application,
                               bot=SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())), pending

    async def test_background_reservation_blocks_overlapping_requests(self):
        context, pending = self.context()
        try:
            await manual.request_chart(context, 'hourly', '1h')
            await manual.request_chart(context, 'screenshot', '15m')
            self.assertEqual(len(pending), 1)
            self.assertIn('already', context.bot.send_message.call_args.args[1])
        finally:
            for coro in pending:
                coro.close()

    async def test_manual_requests_can_repeat_immediately_after_delivery(self):
        context, pending = self.context()
        with patch.object(manual, 'capture_chart', AsyncMock(return_value=b'png')) as capture, \
             patch.object(manual, 'capture_hourly_chart', AsyncMock(return_value=(b'png', 'caption'))) as hourly:
            try:
                for kind in ('screenshot', 'screenshot', 'hourly', 'screenshot'):
                    await manual.request_chart(context, kind, '1h' if kind == 'hourly' else '15m')
                    self.assertEqual(len(pending), 1)
                    await pending.pop()
                self.assertEqual(capture.await_count, 3)
                hourly.assert_awaited_once()
                self.assertEqual(context.bot.send_photo.await_count, 4)
            finally:
                for coro in pending:
                    coro.close()

    async def test_screenshot_current_timeframe_even_auto_flag_off(self):
        context, _ = self.context()
        with patch.object(config, 'CHART_SCREENSHOTS_ENABLED', False), \
             patch.object(manual, 'capture_chart', AsyncMock(return_value=b'png')) as capture:
            await manual.deliver_chart(context.bot, context.application.bot_data, 'screenshot', '15m')
        self.assertEqual(capture.call_args.args[1], '15m')
        self.assertFalse(capture.call_args.args[0].enabled)
        self.assertEqual(context.bot.send_photo.call_args.kwargs['chat_id'], config.ALLOWED_USER_ID)
        self.assertFalse(context.application.bot_data['manual_chart_busy'])

    async def test_hourly_send_and_failures_release_busy_and_redact_logs(self):
        for failure in (None, OSError('private-token'), asyncio.TimeoutError()):
            context, _ = self.context()
            state = context.application.bot_data
            state['manual_chart_busy'] = True
            with patch.object(manual, 'capture_hourly_chart', AsyncMock(return_value=(b'png', 'caption'), side_effect=failure)):
                if failure:
                    with self.assertLogs('xauusd_bot', 'WARNING') as logs:
                        await manual.deliver_chart(context.bot, state, 'hourly', '1h')
                    self.assertNotIn('private-token', ' '.join(logs.output))
                    context.bot.send_message.assert_awaited_once()
                    context.bot.send_photo.assert_not_awaited()
                else:
                    await manual.deliver_chart(context.bot, state, 'hourly', '1h')
                    self.assertEqual(context.bot.send_photo.call_args.kwargs['filename'], 'oanda-xauusd-1h-volume.png')
            self.assertFalse(state['manual_chart_busy'])

    async def test_send_failure_and_cancellation_release_busy(self):
        context, _ = self.context()
        context.bot.send_photo.side_effect = OSError('secret')
        with patch.object(manual, 'capture_hourly_chart', AsyncMock(return_value=(b'png', 'caption'))):
            await manual.deliver_chart(context.bot, context.application.bot_data, 'hourly', '1h')
        self.assertFalse(context.application.bot_data['manual_chart_busy'])
        with patch.object(manual, 'capture_hourly_chart', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await manual.deliver_chart(context.bot, context.application.bot_data, 'hourly', '1h')
        self.assertFalse(context.application.bot_data['manual_chart_busy'])

    async def test_preparation_notice_failure_releases_reservation(self):
        context, pending = self.context()
        context.bot.send_message.side_effect = OSError()
        with self.assertRaises(OSError):
            await manual.request_chart(context, 'hourly', '1h')
        self.assertFalse(context.application.bot_data['manual_chart_busy'])
        self.assertFalse(pending)


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_buttons_and_commands_use_auth_gate(self):
        callbacks = {b.callback_data for row in bot.get_keyboard().inline_keyboard for b in row}
        self.assertTrue({'btn_screenshot', 'btn_hourly_chart'}.issubset(callbacks))
        query = SimpleNamespace(data='btn_hourly_chart', answer=AsyncMock(), edit_message_text=AsyncMock())
        for authorized in (False, True):
            update = SimpleNamespace(effective_user=SimpleNamespace(id=config.ALLOWED_USER_ID if authorized else -1), callback_query=query)
            with patch.object(bot, 'request_chart', AsyncMock()) as request:
                for handler in (bot.screenshot_command, bot.hourly_chart_command, bot.button_callback):
                    await handler(update, SimpleNamespace())
                self.assertEqual(request.await_count, 3 if authorized else 0)
        query.answer.assert_awaited_once()
        query.edit_message_text.assert_not_awaited()

    async def test_manual_callbacks_do_not_change_alert_timeframe_or_feed(self):
        before = (config.state.current_timeframe, config.state.last_feed_source, config.state.last_alerted_candle_time)
        for data, kind in (('btn_screenshot', 'screenshot'), ('btn_hourly_chart', 'hourly')):
            query = SimpleNamespace(data=data, answer=AsyncMock())
            update = SimpleNamespace(effective_user=SimpleNamespace(id=config.ALLOWED_USER_ID), callback_query=query)
            with patch.object(bot, 'request_chart', AsyncMock()) as request:
                await bot.button_callback(update, SimpleNamespace())
            self.assertEqual(request.call_args.args[1], kind)
            query.answer.assert_awaited_once()
        self.assertEqual(before, (config.state.current_timeframe, config.state.last_feed_source, config.state.last_alerted_candle_time))
