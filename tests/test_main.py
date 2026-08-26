"""End-to-end integration and orchestration tests for main TradeMonitoringService."""

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import numpy as np
from pydantic import SecretStr

from main import TradeMonitoringService, async_main, parse_args
from src.config import (
    AIConfig,
    AppConfig,
    CaptureConfig,
    ChannelConfig,
    ChannelMode,
    CVFilterConfig,
    TelegramConfig,
)
from src.session_logger import LiveSessionLogger
from src.stream_manager import StreamStatus


class TestTradeMonitoringService(unittest.IsolatedAsyncioTestCase):
    """Tests for TradeMonitoringService orchestration loop and alert pipeline."""

    def setUp(self) -> None:
        async def wait_for_test_shutdown(
            _listener: object, stop_event: asyncio.Event
        ) -> None:
            await stop_event.wait()

        self._listener_poller_patch = patch(
            "main.LiveChannelListener.listen",
            new=wait_for_test_shutdown,
        )
        self._listener_close_patch = patch(
            "main.LiveChannelListener.close",
            new_callable=AsyncMock,
        )
        self._listener_poller_patch.start()
        self._listener_close_patch.start()
        self.addCleanup(self._listener_poller_patch.stop)
        self.addCleanup(self._listener_close_patch.stop)

        self.config = AppConfig(
            channels=[
                ChannelConfig(
                    name="Live Trader",
                    url="https://www.youtube.com/@LiveTrader/live",
                    enabled=True,
                )
            ],
            capture=CaptureConfig(
                frame_interval_seconds=0.01,
                live_poll_interval_seconds=0.01,
            ),
            cv_filter=CVFilterConfig(min_line_length_ratio=0.1),
            ai=AIConfig(confidence_threshold=0.7),
            telegram=TelegramConfig(include_screenshot=True),
            groq_api_key=SecretStr("mock_groq_api_key_value"),
            gemini_api_key=SecretStr("mock_gemini_api_key_value"),
            telegram_bot_token=SecretStr("123456789:mock_telegram_token_xyz"),
            telegram_chat_id="123456789",
            log_level="DEBUG",
        )
        self._temp_dir = tempfile.TemporaryDirectory()
        self.service = TradeMonitoringService(
            self.config,
            session_logger=LiveSessionLogger(
                Path(self._temp_dir.name) / "live_sessions.json"
            ),
        )

    async def asyncTearDown(self) -> None:
        self.service.stop()
        await self.service.notifier.close()
        self._temp_dir.cleanup()

    @patch("src.stream_manager.StreamManager.check_live_status", new_callable=AsyncMock)
    @patch("src.stream_manager.StreamManager.capture_frame", new_callable=AsyncMock)
    @patch("src.audio_stream.AudioStreamManager.stream_audio_chunks")
    @patch(
        "src.audio_transcriber.AudioTranscriber.transcribe_pcm_chunk",
        new_callable=AsyncMock,
    )
    @patch(
        "src.multimodal_verifier.MultimodalVerifier.verify_multimodal_trade",
        new_callable=AsyncMock,
    )
    async def test_screenshot_capture_can_be_disabled(
        self,
        mock_multimodal: AsyncMock,
        mock_transcribe: AsyncMock,
        mock_stream_chunks: MagicMock,
        mock_capture: AsyncMock,
        mock_check_live: AsyncMock,
    ) -> None:
        from src.multimodal_verifier import MultimodalTradeVerification

        self.config.capture.screenshots_enabled = False
        self.config.channels[0].trigger_keywords = ["active"]
        mock_check_live.return_value = (
            StreamStatus.LIVE,
            "https://live.stream/manifest.m3u8",
        )

        async def fake_audio_generator(stop_event=None):
            yield b"\x00\x00" * 80000

        mock_stream_chunks.side_effect = fake_audio_generator
        mock_transcribe.return_value = "Trade is active"
        mock_multimodal.return_value = MultimodalTradeVerification(
            signal_type="NONE",
            confidence=0.1,
        )

        monitor_task = asyncio.create_task(
            self.service.monitor_channel(self.config.channels[0])
        )
        for _ in range(50):
            if mock_multimodal.call_count:
                break
            await asyncio.sleep(0.02)

        self.service.stop()
        await asyncio.wait_for(monitor_task, timeout=1.0)

        mock_capture.assert_not_awaited()
        self.assertIsNone(mock_multimodal.call_args.kwargs["frame"])

    @patch(
        "src.notifier.TelegramNotifier.send_fast_audio_trade_alert",
        new_callable=AsyncMock,
    )
    @patch("src.stream_manager.StreamManager.check_live_status", new_callable=AsyncMock)
    @patch("src.stream_manager.StreamManager.capture_frame", new_callable=AsyncMock)
    @patch("src.audio_stream.AudioStreamManager.stream_audio_chunks")
    @patch(
        "src.audio_transcriber.AudioTranscriber.transcribe_pcm_chunk",
        new_callable=AsyncMock,
    )
    @patch(
        "src.multimodal_verifier.MultimodalVerifier.verify_multimodal_trade",
        new_callable=AsyncMock,
    )
    async def test_full_trade_lifecycle_orchestration(
        self,
        mock_multimodal: AsyncMock,
        mock_transcribe: MagicMock,
        mock_stream_chunks: MagicMock,
        mock_capture: AsyncMock,
        mock_check_live: AsyncMock,
        mock_send_alert: AsyncMock,
    ) -> None:
        # Mock stream becoming live
        mock_check_live.return_value = (
            StreamStatus.LIVE,
            "https://live.stream/manifest.m3u8",
        )
        fake_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_capture.return_value = fake_frame

        # Mock audio stream yielding chunks
        async def fake_audio_generator(stop_event=None):
            yield b"\x00\x00" * 80000
            if stop_event:
                await stop_event.wait()
            else:
                while True:
                    await asyncio.sleep(0.1)

        mock_stream_chunks.side_effect = fake_audio_generator

        # Step 1: Whisper transcribes spoken entry trigger
        mock_transcribe.return_value = "Haan yahan pe enter kiya humne call mein"

        # Step 2: Gemini multimodal verifies active trade
        from src.multimodal_verifier import MultimodalTradeVerification

        mock_multimodal.return_value = MultimodalTradeVerification(
            is_trade_active=True,
            direction="CALL",
            instrument="NIFTY 24500 CE",
            entry_price="120",
            stop_loss="95",
            target="160",
            trigger_quote="Haan yahan pe enter kiya humne call mein",
            confidence=0.95,
            reasoning="Trader confirmed live call breakout execution",
        )
        mock_send_alert.return_value = True

        # Run channel monitor in background task
        channel = self.config.channels[0]
        channel.trigger_keywords = ["enter kiya"]
        monitor_task = asyncio.create_task(self.service.monitor_channel(channel))

        # Give loop time to process the audio chunk and verification
        for _ in range(50):
            if mock_send_alert.call_count >= 1:
                break
            await asyncio.sleep(0.02)

        # Assert FAST AUDIO TRADE alert was dispatched
        self.assertGreaterEqual(mock_send_alert.call_count, 1)
        mock_send_alert.assert_called()

        # Stop service and cancel task
        self.service.stop()
        await asyncio.sleep(0.02)
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            self.assertTrue(monitor_task.cancelled())

        sessions = self.service.session_logger.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].status, "INTERRUPTED")
        self.assertEqual(sessions[0].trades_detected, 1)
        self.assertEqual(sessions[0].trades_alerted, 1)

    async def test_run_propagates_monitor_failure_and_closes_clients(self) -> None:
        self.service.monitor_channel = AsyncMock(
            side_effect=RuntimeError("monitor failed")
        )
        self.service.notifier.close = AsyncMock()
        self.service.multimodal_verifier.close = AsyncMock()
        self.service.transcriber.close = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "monitor failed"):
            await self.service.run()

        self.service.notifier.close.assert_awaited_once()
        self.service.multimodal_verifier.close.assert_awaited_once()
        self.service.transcriber.close.assert_awaited_once()

    @patch("main.StreamManager")
    async def test_unknown_live_status_does_not_close_trade_state(
        self, mock_stream_manager_class: MagicMock
    ) -> None:
        """Transient resolver failures must not be processed as stream closure."""
        mock_stream_manager = MagicMock()
        mock_stream_manager.is_live = False
        mock_stream_manager.check_live_status = AsyncMock(
            return_value=(StreamStatus.UNKNOWN, None)
        )
        mock_stream_manager.close = AsyncMock()
        mock_stream_manager_class.return_value = mock_stream_manager
        self.service.state_tracker.process_verification = MagicMock()

        monitor_task = asyncio.create_task(
            self.service.monitor_channel(self.config.channels[0])
        )
        for _ in range(25):
            if mock_stream_manager.check_live_status.await_count:
                break
            await asyncio.sleep(0.01)

        self.service.stop()
        await asyncio.wait_for(monitor_task, timeout=0.5)

        mock_stream_manager.check_live_status.assert_awaited()
        self.service.state_tracker.process_verification.assert_not_called()
        mock_stream_manager.close.assert_awaited_once()

    async def test_test_mode_runs_preflight_and_stops_after_duration(self) -> None:
        service = TradeMonitoringService(
            self.config,
            test_duration_seconds=0.02,
        )
        service.notifier.check_connection = AsyncMock(return_value=True)
        service.multimodal_verifier.check_connection = AsyncMock(return_value=True)
        service.transcriber.check_connection = AsyncMock(return_value=True)
        service.notifier.close = AsyncMock()
        service.multimodal_verifier.close = AsyncMock()
        service.transcriber.close = AsyncMock()

        async def monitor_until_stopped(_channel: ChannelConfig) -> None:
            await service._stop_event.wait()

        service.monitor_channel = AsyncMock(side_effect=monitor_until_stopped)

        await asyncio.wait_for(service.run(), timeout=1.0)

        service.notifier.check_connection.assert_awaited_once()
        service.multimodal_verifier.check_connection.assert_awaited_once()
        service.transcriber.check_connection.assert_awaited_once()
        service.notifier.close.assert_awaited_once()
        service.multimodal_verifier.close.assert_awaited_once()
        service.transcriber.close.assert_awaited_once()

    async def test_test_mode_fails_when_gemini_preflight_fails(self) -> None:
        service = TradeMonitoringService(
            self.config,
            test_duration_seconds=1.0,
        )
        service.notifier.check_connection = AsyncMock(return_value=True)
        service.multimodal_verifier.check_connection = AsyncMock(return_value=False)
        service.transcriber.check_connection = AsyncMock(return_value=True)
        service.notifier.close = AsyncMock()
        service.multimodal_verifier.close = AsyncMock()
        service.transcriber.close = AsyncMock()
        service.monitor_channel = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "Gemini"):
            await service.run()

        service.monitor_channel.assert_not_called()
        service.notifier.close.assert_awaited_once()
        service.multimodal_verifier.close.assert_awaited_once()
        service.transcriber.close.assert_awaited_once()

    async def test_test_mode_fails_when_groq_preflight_fails(self) -> None:
        service = TradeMonitoringService(
            self.config,
            test_duration_seconds=1.0,
        )
        service.notifier.check_connection = AsyncMock(return_value=True)
        service.multimodal_verifier.check_connection = AsyncMock(return_value=True)
        service.transcriber.check_connection = AsyncMock(return_value=False)
        service.notifier.close = AsyncMock()
        service.multimodal_verifier.close = AsyncMock()
        service.transcriber.close = AsyncMock()
        service.monitor_channel = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "Groq"):
            await service.run()

        service.monitor_channel.assert_not_called()
        service.notifier.close.assert_awaited_once()
        service.multimodal_verifier.close.assert_awaited_once()
        service.transcriber.close.assert_awaited_once()

    async def test_test_mode_fails_when_telegram_preflight_fails(self) -> None:
        service = TradeMonitoringService(
            self.config,
            test_duration_seconds=1.0,
        )
        service.notifier.check_connection = AsyncMock(return_value=False)
        service.multimodal_verifier.check_connection = AsyncMock(return_value=True)
        service.transcriber.check_connection = AsyncMock(return_value=True)
        service.notifier.close = AsyncMock()
        service.multimodal_verifier.close = AsyncMock()
        service.transcriber.close = AsyncMock()
        service.monitor_channel = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "Telegram"):
            await service.run()

        service.monitor_channel.assert_not_called()
        service.notifier.close.assert_awaited_once()
        service.multimodal_verifier.close.assert_awaited_once()
        service.transcriber.close.assert_awaited_once()

    async def test_test_mode_fails_when_both_preflights_fail(self) -> None:
        service = TradeMonitoringService(
            self.config,
            test_duration_seconds=1.0,
        )
        service.notifier.check_connection = AsyncMock(return_value=False)
        service.multimodal_verifier.check_connection = AsyncMock(return_value=False)
        service.transcriber.check_connection = AsyncMock(return_value=False)
        service.notifier.close = AsyncMock()
        service.multimodal_verifier.close = AsyncMock()
        service.transcriber.close = AsyncMock()
        service.monitor_channel = AsyncMock()

        with self.assertRaises(RuntimeError) as ctx:
            await service.run()

        self.assertIn("Telegram", str(ctx.exception))
        self.assertIn("Gemini", str(ctx.exception))
        self.assertIn("Groq", str(ctx.exception))
        service.monitor_channel.assert_not_called()
        service.notifier.close.assert_awaited_once()
        service.multimodal_verifier.close.assert_awaited_once()
        service.transcriber.close.assert_awaited_once()

    async def test_test_mode_propagates_monitor_failure_before_timeout(self) -> None:
        service = TradeMonitoringService(
            self.config,
            test_duration_seconds=10.0,
        )
        service.notifier.check_connection = AsyncMock(return_value=True)
        service.multimodal_verifier.check_connection = AsyncMock(return_value=True)
        service.transcriber.check_connection = AsyncMock(return_value=True)
        service.notifier.close = AsyncMock()
        service.multimodal_verifier.close = AsyncMock()
        service.transcriber.close = AsyncMock()

        async def failing_monitor(_channel: ChannelConfig) -> None:
            await asyncio.sleep(0.01)
            raise RuntimeError("Fatal stream read error")

        service.monitor_channel = AsyncMock(side_effect=failing_monitor)

        with self.assertRaisesRegex(RuntimeError, "Fatal stream read error"):
            await service.run()

        service.notifier.close.assert_awaited_once()
        service.multimodal_verifier.close.assert_awaited_once()
        service.transcriber.close.assert_awaited_once()

    async def test_shutdown_race_monitor_completes_as_stop_event_set(self) -> None:
        service = TradeMonitoringService(
            self.config,
            test_duration_seconds=0.01,
        )
        service.notifier.check_connection = AsyncMock(return_value=True)
        service.multimodal_verifier.check_connection = AsyncMock(return_value=True)
        service.transcriber.check_connection = AsyncMock(return_value=True)
        service.notifier.close = AsyncMock()
        service.multimodal_verifier.close = AsyncMock()
        service.transcriber.close = AsyncMock()

        async def monitor_on_stop(_channel: ChannelConfig) -> None:
            await service._stop_event.wait()

        service.monitor_channel = AsyncMock(side_effect=monitor_on_stop)

        await asyncio.wait_for(service.run(), timeout=1.0)

        service.notifier.close.assert_awaited_once()
        service.multimodal_verifier.close.assert_awaited_once()
        service.transcriber.close.assert_awaited_once()

    async def test_monitor_stopped_unexpectedly_when_stop_event_not_set(self) -> None:
        service = TradeMonitoringService(self.config)
        service.notifier.close = AsyncMock()
        service.multimodal_verifier.close = AsyncMock()
        service.transcriber.close = AsyncMock()

        async def premature_exit(_channel: ChannelConfig) -> None:
            return

        service.monitor_channel = AsyncMock(side_effect=premature_exit)

        with self.assertRaisesRegex(RuntimeError, "stopped unexpectedly"):
            await service.run()

        service.notifier.close.assert_awaited_once()
        service.multimodal_verifier.close.assert_awaited_once()
        service.transcriber.close.assert_awaited_once()

    async def test_test_mode_cleans_up_all_resources_and_stream_managers(self) -> None:
        service = TradeMonitoringService(
            self.config,
            test_duration_seconds=0.02,
        )
        service.notifier.check_connection = AsyncMock(return_value=True)
        service.multimodal_verifier.check_connection = AsyncMock(return_value=True)
        service.transcriber.check_connection = AsyncMock(return_value=True)
        service.notifier.close = AsyncMock()
        service.multimodal_verifier.close = AsyncMock()
        service.transcriber.close = AsyncMock()

        mock_stream_mgr = AsyncMock()
        service._stream_managers.append(mock_stream_mgr)

        async def monitor_until_stop(_channel: ChannelConfig) -> None:
            await service._stop_event.wait()

        service.monitor_channel = AsyncMock(side_effect=monitor_until_stop)

        await asyncio.wait_for(service.run(), timeout=1.0)

        mock_stream_mgr.close.assert_awaited_once()
        service.notifier.close.assert_awaited_once()
        service.multimodal_verifier.close.assert_awaited_once()
        service.transcriber.close.assert_awaited_once()

    async def test_per_channel_routing_mehak_and_naresh(self) -> None:
        """Mehak Trades gets a monitor_channel task; Naresh Cool Trader gets only status listener."""
        routing_config = AppConfig(
            channels=[
                ChannelConfig(
                    name="Mehak Trades",
                    url="https://www.youtube.com/@tradewithmehaktrades/live",
                    enabled=True,
                    mode=ChannelMode.FULL_ANALYSIS,
                    trigger_keywords=["trade active", "enter kiya"],
                    trader_profile="Mehak Trades profile",
                ),
                ChannelConfig(
                    name="Naresh Cool Trader",
                    url="https://www.youtube.com/@NareshCoolTrader/live",
                    enabled=True,
                    mode=ChannelMode.NOTIFICATIONS_ONLY,
                    trigger_keywords=["call liya", "put liya"],
                    trader_profile="Naresh Cool Trader profile",
                ),
                ChannelConfig(
                    name="Sample Trader 1",
                    url="https://www.youtube.com/@SampleTrader1/live",
                    enabled=False,
                    mode=ChannelMode.FULL_ANALYSIS,
                ),
            ],
            capture=CaptureConfig(
                frame_interval_seconds=0.01,
                live_poll_interval_seconds=0.01,
            ),
            cv_filter=CVFilterConfig(),
            ai=AIConfig(),
            telegram=TelegramConfig(),
            groq_api_key=SecretStr("mock_groq_key"),
            gemini_api_key=SecretStr("mock_gemini_key"),
            telegram_bot_token=SecretStr("123:mock_tg_token"),
            telegram_chat_id="123456789",
            log_level="DEBUG",
        )
        service = TradeMonitoringService(
            routing_config,
            test_duration_seconds=0.02,
        )
        service.notifier.check_connection = AsyncMock(return_value=True)
        service.multimodal_verifier.check_connection = AsyncMock(return_value=True)
        service.transcriber.check_connection = AsyncMock(return_value=True)
        service.notifier.close = AsyncMock()
        service.multimodal_verifier.close = AsyncMock()
        service.transcriber.close = AsyncMock()

        monitored_channels: list[ChannelConfig] = []

        async def fake_monitor(channel: ChannelConfig) -> None:
            monitored_channels.append(channel)
            await service._stop_event.wait()

        service.monitor_channel = AsyncMock(side_effect=fake_monitor)

        created_listeners: list[MagicMock] = []

        def make_listener(**kwargs: object) -> MagicMock:
            listener = MagicMock()
            listener.channel_name = kwargs["channel_name"]

            async def listen_until_stopped(stop_event: asyncio.Event) -> None:
                await stop_event.wait()

            listener.listen = AsyncMock(side_effect=listen_until_stopped)
            listener.close = AsyncMock()
            created_listeners.append(listener)
            return listener

        with patch("main.LiveChannelListener", side_effect=make_listener):
            await asyncio.wait_for(service.run(), timeout=1.0)

        # Exactly 2 live listeners created (for the 2 enabled channels)
        self.assertEqual(len(service._live_listeners), 2)
        self.assertEqual(service._live_listeners, created_listeners)
        listener_names = {listener.channel_name for listener in created_listeners}
        self.assertEqual(listener_names, {"Mehak Trades", "Naresh Cool Trader"})
        for listener in created_listeners:
            listener.listen.assert_awaited_once()
            listener.close.assert_awaited_once()

        # monitor_channel must be called EXACTLY ONCE, and ONLY for Mehak Trades
        self.assertEqual(service.monitor_channel.call_count, 1)
        self.assertEqual(len(monitored_channels), 1)
        self.assertEqual(monitored_channels[0].name, "Mehak Trades")
        self.assertEqual(monitored_channels[0].mode, ChannelMode.FULL_ANALYSIS)

        # Naresh Cool Trader must never receive a monitor task
        self.assertNotIn("Naresh Cool Trader", [c.name for c in monitored_channels])

    async def test_test_mode_notification_only_preflights_only_telegram(self) -> None:
        """When all enabled channels are notification-only, Groq and Gemini preflight are skipped."""
        notify_only_config = AppConfig(
            channels=[
                ChannelConfig(
                    name="Naresh Cool Trader",
                    url="https://www.youtube.com/@NareshCoolTrader/live",
                    enabled=True,
                    mode=ChannelMode.NOTIFICATIONS_ONLY,
                ),
                ChannelConfig(
                    name="Sample Disabled",
                    url="https://www.youtube.com/@SampleDisabled/live",
                    enabled=False,
                    mode=ChannelMode.FULL_ANALYSIS,
                ),
            ],
            capture=CaptureConfig(
                frame_interval_seconds=0.01,
                live_poll_interval_seconds=0.01,
            ),
            cv_filter=CVFilterConfig(),
            ai=AIConfig(),
            telegram=TelegramConfig(),
            groq_api_key=SecretStr("mock_groq_key"),
            gemini_api_key=SecretStr("mock_gemini_key"),
            telegram_bot_token=SecretStr("123:mock_tg_token"),
            telegram_chat_id="123456789",
            log_level="DEBUG",
        )
        service = TradeMonitoringService(
            notify_only_config,
            test_duration_seconds=0.02,
        )
        service.notifier.check_connection = AsyncMock(return_value=True)
        service.multimodal_verifier.check_connection = AsyncMock()
        service.transcriber.check_connection = AsyncMock()
        service.notifier.close = AsyncMock()
        service.multimodal_verifier.close = AsyncMock()
        service.transcriber.close = AsyncMock()
        service.monitor_channel = AsyncMock()

        created_listeners: list[MagicMock] = []

        def make_listener(**kwargs: object) -> MagicMock:
            listener = MagicMock()
            listener.channel_name = kwargs["channel_name"]

            async def listen_until_stopped(stop_event: asyncio.Event) -> None:
                await stop_event.wait()

            listener.listen = AsyncMock(side_effect=listen_until_stopped)
            listener.close = AsyncMock()
            created_listeners.append(listener)
            return listener

        with patch("main.LiveChannelListener", side_effect=make_listener):
            await asyncio.wait_for(service.run(), timeout=1.0)

        # Telegram preflight called
        service.notifier.check_connection.assert_awaited_once()
        # Groq and Gemini preflight NOT called
        service.multimodal_verifier.check_connection.assert_not_called()
        service.transcriber.check_connection.assert_not_called()
        # monitor_channel NOT called for notification-only channel
        service.monitor_channel.assert_not_called()
        # Exactly one status listener created
        self.assertEqual(service._live_listeners, created_listeners)
        self.assertEqual(len(created_listeners), 1)
        self.assertEqual(created_listeners[0].channel_name, "Naresh Cool Trader")
        created_listeners[0].listen.assert_awaited_once()
        created_listeners[0].close.assert_awaited_once()

    async def test_test_mode_notification_only_fails_when_telegram_fails(self) -> None:
        """When notification-only channels exist, preflight fails if Telegram check fails."""
        notify_only_config = AppConfig(
            channels=[
                ChannelConfig(
                    name="Naresh Cool Trader",
                    url="https://www.youtube.com/@NareshCoolTrader/live",
                    enabled=True,
                    mode=ChannelMode.NOTIFICATIONS_ONLY,
                )
            ],
            capture=CaptureConfig(
                frame_interval_seconds=0.01,
                live_poll_interval_seconds=0.01,
            ),
            cv_filter=CVFilterConfig(),
            ai=AIConfig(),
            telegram=TelegramConfig(),
            groq_api_key=SecretStr("mock_groq_key"),
            gemini_api_key=SecretStr("mock_gemini_key"),
            telegram_bot_token=SecretStr("123:mock_tg_token"),
            telegram_chat_id="123456789",
            log_level="DEBUG",
        )
        service = TradeMonitoringService(
            notify_only_config,
            test_duration_seconds=1.0,
        )
        service.notifier.check_connection = AsyncMock(return_value=False)
        service.multimodal_verifier.check_connection = AsyncMock()
        service.transcriber.check_connection = AsyncMock()
        service.notifier.close = AsyncMock()
        service.multimodal_verifier.close = AsyncMock()
        service.transcriber.close = AsyncMock()

        with self.assertRaisesRegex(RuntimeError, "Telegram"):
            await service.run()

        service.notifier.check_connection.assert_awaited_once()
        service.multimodal_verifier.check_connection.assert_not_called()
        service.transcriber.check_connection.assert_not_called()
        service.notifier.close.assert_awaited_once()
        service.multimodal_verifier.close.assert_awaited_once()
        service.transcriber.close.assert_awaited_once()

    @patch("main.AudioStreamManager")
    @patch("src.stream_manager.StreamManager.check_live_status", new_callable=AsyncMock)
    async def test_direct_url_propagated_to_audio_manager(
        self,
        mock_check_live: AsyncMock,
        mock_audio_mgr_class: MagicMock,
    ) -> None:
        """monitor_channel passes direct_url from StreamManager to AudioStreamManager."""
        mock_check_live.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/hls_live/stream.m3u8",
        )
        mock_audio_mgr = MagicMock()

        async def empty_audio_chunks(stop_event=None):
            if False:
                yield b""

        mock_audio_mgr.stream_audio_chunks = empty_audio_chunks
        mock_audio_mgr.stop = AsyncMock()
        mock_audio_mgr_class.return_value = mock_audio_mgr

        channel = self.config.channels[0]
        channel.url = "https://www.youtube.com/@LiveTrader/live"

        monitor_task = asyncio.create_task(self.service.monitor_channel(channel))
        for _ in range(25):
            if mock_audio_mgr_class.call_count:
                break
            await asyncio.sleep(0.01)

        self.service.stop()
        await asyncio.wait_for(monitor_task, timeout=0.5)

        self.assertGreaterEqual(mock_audio_mgr_class.call_count, 1)
        call_kwargs = mock_audio_mgr_class.call_args.kwargs
        self.assertEqual(
            call_kwargs.get("direct_url"),
            "https://manifest.googlevideo.com/hls_live/stream.m3u8",
        )
        self.assertIn("stream_url", call_kwargs)
        url_refresher = call_kwargs.get("url_refresher")
        self.assertIsNotNone(url_refresher)

        # Refresher returns URL only for explicit LIVE status
        mock_check_live.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/hls_live/refreshed.m3u8",
        )
        self.assertEqual(
            await url_refresher(),
            "https://manifest.googlevideo.com/hls_live/refreshed.m3u8",
        )

        # OFFLINE returns None
        mock_check_live.return_value = (StreamStatus.OFFLINE, None)
        self.assertIsNone(await url_refresher())

        # UNKNOWN returns None
        mock_check_live.return_value = (StreamStatus.UNKNOWN, None)
        self.assertIsNone(await url_refresher())

        # LIVE with empty/None URL returns None
        mock_check_live.return_value = (StreamStatus.LIVE, None)
        self.assertIsNone(await url_refresher())
        mock_check_live.return_value = (StreamStatus.LIVE, "")
        self.assertIsNone(await url_refresher())

    @patch(
        "src.notifier.TelegramNotifier.send_fast_audio_trade_alert",
        new_callable=AsyncMock,
    )
    @patch("src.stream_manager.StreamManager.check_live_status", new_callable=AsyncMock)
    @patch("src.stream_manager.StreamManager.capture_frame", new_callable=AsyncMock)
    @patch("src.audio_stream.AudioStreamManager.stream_audio_chunks")
    @patch(
        "src.audio_transcriber.AudioTranscriber.transcribe_pcm_chunk",
        new_callable=AsyncMock,
    )
    @patch(
        "src.multimodal_verifier.MultimodalVerifier.verify_multimodal_trade",
        new_callable=AsyncMock,
    )
    @patch(
        "src.stream_manager.StreamManager.broadcast_start_time",
        new_callable=PropertyMock,
    )
    async def test_stable_elapsed_timestamp_across_audio_restarts(
        self,
        mock_broadcast_start: MagicMock,
        mock_multimodal: AsyncMock,
        mock_transcribe: MagicMock,
        mock_stream_chunks: MagicMock,
        mock_capture: AsyncMock,
        mock_check_live: AsyncMock,
        mock_send_alert: AsyncMock,
    ) -> None:
        """Elapsed stream timestamp must reference the broadcast start time across audio restarts."""
        fixed_broadcast_start = time.time() - 3600.0  # broadcast started 1 hour ago
        mock_broadcast_start.return_value = fixed_broadcast_start
        mock_check_live.return_value = (
            StreamStatus.LIVE,
            "https://live.stream/manifest.m3u8",
        )
        fake_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_capture.return_value = fake_frame

        # Session 1 yields chunk and alert, then ends
        # Session 2 restarts, yields chunk and alert
        session_counter = 0

        async def multi_session_chunks(stop_event=None):
            nonlocal session_counter
            session_counter += 1
            yield b"\x00\x00" * 80000

        mock_stream_chunks.side_effect = multi_session_chunks
        mock_transcribe.return_value = "Trade active entered position"

        from src.multimodal_verifier import MultimodalTradeVerification

        mock_multimodal.return_value = MultimodalTradeVerification(
            is_trade_active=True,
            direction="BUY",
            instrument="GOLD",
            confidence=0.95,
            reasoning="Active entry",
        )
        mock_send_alert.return_value = True

        channel = self.config.channels[0]
        channel.trigger_keywords = ["trade active"]

        monitor_task = asyncio.create_task(self.service.monitor_channel(channel))
        for _ in range(50):
            if mock_send_alert.call_count >= 1:
                break
            await asyncio.sleep(0.02)

        self.service.stop()
        await asyncio.wait_for(monitor_task, timeout=1.0)

        self.assertGreaterEqual(mock_send_alert.call_count, 1)
        first_alert_kwargs = mock_send_alert.call_args_list[0].kwargs
        elapsed = first_alert_kwargs.get("elapsed_seconds", 0.0)
        # Should be ~3600s, not 0s
        self.assertGreaterEqual(elapsed, 3590.0)

    @patch(
        "src.notifier.TelegramNotifier.send_fast_audio_trade_alert",
        new_callable=AsyncMock,
    )
    @patch("src.stream_manager.StreamManager.check_live_status", new_callable=AsyncMock)
    @patch("src.stream_manager.StreamManager.capture_frame", new_callable=AsyncMock)
    @patch("src.audio_stream.AudioStreamManager.stream_audio_chunks")
    @patch(
        "src.audio_transcriber.AudioTranscriber.transcribe_pcm_chunk",
        new_callable=AsyncMock,
    )
    @patch(
        "src.multimodal_verifier.MultimodalVerifier.verify_multimodal_trade",
        new_callable=AsyncMock,
    )
    async def test_audited_entry_phrase_dispatches_trade_alert(
        self,
        mock_multimodal: AsyncMock,
        mock_transcribe: AsyncMock,
        mock_stream_chunks: MagicMock,
        mock_capture: AsyncMock,
        mock_check_live: AsyncMock,
        mock_send_alert: AsyncMock,
    ) -> None:
        """Audited phrase 'You can enter with one lot. I am taking a small scalp.' triggers ENTRY alert."""
        mock_check_live.return_value = (
            StreamStatus.LIVE,
            "https://live.stream/manifest.m3u8",
        )
        fake_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_capture.return_value = fake_frame

        async def fake_audio_generator(stop_event=None):
            yield b"\x00\x00" * 80000

        mock_stream_chunks.side_effect = fake_audio_generator
        mock_transcribe.return_value = "You can enter with one lot. I am taking a small scalp."

        from src.multimodal_verifier import MultimodalTradeVerification
        mock_multimodal.return_value = MultimodalTradeVerification(
            signal_type="ENTRY",
            is_trade_active=True,
            direction="BUY",
            instrument="GOLD",
            entry_price="2650.50",
            trigger_quote="You can enter with one lot. I am taking a small scalp.",
            confidence=0.95,
            reasoning="Trader confirmed live scalp entry",
        )
        mock_send_alert.return_value = True

        channel = self.config.channels[0]
        channel.trigger_keywords = ["enter", "scalp"]

        monitor_task = asyncio.create_task(self.service.monitor_channel(channel))
        for _ in range(50):
            if mock_send_alert.call_count >= 1:
                break
            await asyncio.sleep(0.02)

        self.service.stop()
        await asyncio.wait_for(monitor_task, timeout=1.0)

        self.assertEqual(mock_send_alert.call_count, 1)
        call_kwargs = mock_send_alert.call_args.kwargs
        self.assertEqual(call_kwargs["channel_name"], channel.name)
        self.assertEqual(call_kwargs["verification"].signal_type, "ENTRY")

    @patch(
        "src.notifier.TelegramNotifier.send_management_alert",
        new_callable=AsyncMock,
    )
    @patch("src.stream_manager.StreamManager.check_live_status", new_callable=AsyncMock)
    @patch("src.stream_manager.StreamManager.capture_frame", new_callable=AsyncMock)
    @patch("src.audio_stream.AudioStreamManager.stream_audio_chunks")
    @patch(
        "src.audio_transcriber.AudioTranscriber.transcribe_pcm_chunk",
        new_callable=AsyncMock,
    )
    @patch(
        "src.multimodal_verifier.MultimodalVerifier.verify_multimodal_trade",
        new_callable=AsyncMock,
    )
    async def test_audited_management_phrase_dispatches_management_alert(
        self,
        mock_multimodal: AsyncMock,
        mock_transcribe: AsyncMock,
        mock_stream_chunks: MagicMock,
        mock_capture: AsyncMock,
        mock_check_live: AsyncMock,
        mock_send_mgmt_alert: AsyncMock,
    ) -> None:
        """Audited phrase 'Book some profit and move SL to CTC.' triggers MANAGEMENT alert."""
        mock_check_live.return_value = (
            StreamStatus.LIVE,
            "https://live.stream/manifest.m3u8",
        )
        fake_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_capture.return_value = fake_frame

        async def fake_audio_generator(stop_event=None):
            yield b"\x00\x00" * 80000

        mock_stream_chunks.side_effect = fake_audio_generator
        mock_transcribe.return_value = "Book some profit and move SL to CTC."

        from src.multimodal_verifier import MultimodalTradeVerification
        mock_multimodal.return_value = MultimodalTradeVerification(
            signal_type="PARTIAL_EXIT",
            is_trade_active=True,
            direction="BUY",
            instrument="GOLD",
            stop_loss="CTC",
            trigger_quote="Book some profit and move SL to CTC.",
            confidence=0.92,
            reasoning="Partial booking and stop moved to breakeven",
        )
        mock_send_mgmt_alert.return_value = True

        channel = self.config.channels[0]
        channel.trigger_keywords = ["book profit", "sl to ctc", "ctc"]

        monitor_task = asyncio.create_task(self.service.monitor_channel(channel))
        for _ in range(50):
            if mock_send_mgmt_alert.call_count >= 1:
                break
            await asyncio.sleep(0.02)

        self.service.stop()
        await asyncio.wait_for(monitor_task, timeout=1.0)

        self.assertEqual(mock_send_mgmt_alert.call_count, 1)
        call_kwargs = mock_send_mgmt_alert.call_args.kwargs
        self.assertEqual(call_kwargs["channel_name"], channel.name)
        self.assertEqual(call_kwargs["verification"].signal_type, "PARTIAL_EXIT")

    @patch(
        "src.notifier.TelegramNotifier.send_management_alert",
        new_callable=AsyncMock,
    )
    @patch(
        "src.notifier.TelegramNotifier.send_trade_closed_alert",
        new_callable=AsyncMock,
    )
    @patch("src.stream_manager.StreamManager.check_live_status", new_callable=AsyncMock)
    @patch("src.stream_manager.StreamManager.capture_frame", new_callable=AsyncMock)
    @patch("src.audio_stream.AudioStreamManager.stream_audio_chunks")
    @patch(
        "src.audio_transcriber.AudioTranscriber.transcribe_pcm_chunk",
        new_callable=AsyncMock,
    )
    @patch(
        "src.multimodal_verifier.MultimodalVerifier.verify_multimodal_trade",
        new_callable=AsyncMock,
    )
    async def test_audited_exit_phrase_dispatches_exit_alert(
        self,
        mock_multimodal: AsyncMock,
        mock_transcribe: AsyncMock,
        mock_stream_chunks: MagicMock,
        mock_capture: AsyncMock,
        mock_check_live: AsyncMock,
        mock_send_closed_alert: AsyncMock,
        mock_send_mgmt_alert: AsyncMock,
    ) -> None:
        """Audited phrase 'My remaining trade is closed/cut.' triggers immediate exit alert."""
        mock_check_live.return_value = (
            StreamStatus.LIVE,
            "https://live.stream/manifest.m3u8",
        )
        fake_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_capture.return_value = fake_frame

        async def fake_audio_generator(stop_event=None):
            yield b"\x00\x00" * 80000

        mock_stream_chunks.side_effect = fake_audio_generator
        mock_transcribe.return_value = "My remaining trade is closed/cut."

        from src.multimodal_verifier import MultimodalTradeVerification
        mock_multimodal.return_value = MultimodalTradeVerification(
            signal_type="EXIT",
            is_trade_active=False,
            direction="BUY",
            instrument="GOLD",
            trigger_quote="My remaining trade is closed/cut.",
            confidence=0.94,
            reasoning="Explicit position exit confirmed",
        )
        mock_send_mgmt_alert.return_value = True
        mock_send_closed_alert.return_value = True

        channel = self.config.channels[0]
        channel.trigger_keywords = ["closed", "cut", "close"]

        monitor_task = asyncio.create_task(self.service.monitor_channel(channel))
        for _ in range(50):
            if mock_send_mgmt_alert.call_count >= 1 or mock_send_closed_alert.call_count >= 1:
                break
            await asyncio.sleep(0.02)

        self.service.stop()
        await asyncio.wait_for(monitor_task, timeout=1.0)

        total_exit_alerts = mock_send_mgmt_alert.call_count + mock_send_closed_alert.call_count
        self.assertEqual(total_exit_alerts, 1)

    @patch(
        "src.notifier.TelegramNotifier.send_fast_audio_trade_alert",
        new_callable=AsyncMock,
    )
    @patch(
        "src.notifier.TelegramNotifier.send_management_alert",
        new_callable=AsyncMock,
    )
    @patch("src.stream_manager.StreamManager.check_live_status", new_callable=AsyncMock)
    @patch("src.stream_manager.StreamManager.capture_frame", new_callable=AsyncMock)
    @patch("src.audio_stream.AudioStreamManager.stream_audio_chunks")
    @patch(
        "src.audio_transcriber.AudioTranscriber.transcribe_pcm_chunk",
        new_callable=AsyncMock,
    )
    @patch(
        "src.multimodal_verifier.MultimodalVerifier.verify_multimodal_trade",
        new_callable=AsyncMock,
    )
    async def test_negative_conditional_37_level_rejected_fail_closed(
        self,
        mock_multimodal: AsyncMock,
        mock_transcribe: AsyncMock,
        mock_stream_chunks: MagicMock,
        mock_capture: AsyncMock,
        mock_check_live: AsyncMock,
        mock_send_mgmt_alert: AsyncMock,
        mock_send_trade_alert: AsyncMock,
    ) -> None:
        """Conditional/viewer discussion around level 37 is rejected by Gemini and sends no alerts."""
        mock_check_live.return_value = (
            StreamStatus.LIVE,
            "https://live.stream/manifest.m3u8",
        )
        fake_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_capture.return_value = fake_frame

        async def fake_audio_generator(stop_event=None):
            yield b"\x00\x00" * 80000

        mock_stream_chunks.side_effect = fake_audio_generator
        mock_transcribe.return_value = "If 37 level breaks we can enter call."

        from src.multimodal_verifier import MultimodalTradeVerification
        mock_multimodal.return_value = MultimodalTradeVerification(
            signal_type="NONE",
            is_trade_active=False,
            direction="NONE",
            confidence=0.0,
            reasoning="Hypothetical conditional discussion, no live order",
        )

        channel = self.config.channels[0]
        channel.trigger_keywords = ["enter"]

        monitor_task = asyncio.create_task(self.service.monitor_channel(channel))
        for _ in range(25):
            await asyncio.sleep(0.02)

        self.service.stop()
        await asyncio.wait_for(monitor_task, timeout=1.0)

        mock_send_trade_alert.assert_not_called()
        mock_send_mgmt_alert.assert_not_called()



class TestCLITestModeValidation(unittest.TestCase):
    """Tests for CLI arguments and duration validation."""

    def test_parse_args_defaults(self) -> None:
        args = parse_args([])
        self.assertEqual(args.config, "config.yaml")
        self.assertEqual(args.env, ".env")
        self.assertIsNone(args.log_level)
        self.assertIsNone(args.test_minutes)

    def test_parse_args_test_minutes_values(self) -> None:
        args_5 = parse_args(["--test-minutes", "5"])
        self.assertEqual(args_5.test_minutes, 5.0)

        args_10 = parse_args(["--test-minutes", "10"])
        self.assertEqual(args_10.test_minutes, 10.0)

        args_frac = parse_args(["--test-minutes", "0.5"])
        self.assertEqual(args_frac.test_minutes, 0.5)


class TestAsyncMainTestMode(unittest.IsolatedAsyncioTestCase):
    """Tests for async_main orchestration in test mode."""

    def setUp(self) -> None:
        self.dummy_config = AppConfig(
            channels=[
                ChannelConfig(
                    name="Test Trader",
                    url="https://www.youtube.com/@TestTrader/live",
                    enabled=True,
                )
            ],
            capture=CaptureConfig(
                frame_interval_seconds=5.0,
                live_poll_interval_seconds=60.0,
            ),
            cv_filter=CVFilterConfig(),
            ai=AIConfig(),
            telegram=TelegramConfig(),
            groq_api_key=SecretStr("mock_groq_key"),
            gemini_api_key=SecretStr("mock_key"),
            telegram_bot_token=SecretStr("123:mock_token"),
            telegram_chat_id="123456",
            log_level="INFO",
        )

    @patch("main.load_config")
    @patch("main.TradeMonitoringService.run", new_callable=AsyncMock)
    async def test_async_main_test_minutes_5(
        self, mock_run: AsyncMock, mock_load_config: MagicMock
    ) -> None:
        mock_load_config.return_value = self.dummy_config

        exit_code = await async_main(["--test-minutes", "5"])
        self.assertEqual(exit_code, 0)
        mock_run.assert_awaited_once()
        self.assertEqual(self.dummy_config.capture.live_poll_interval_seconds, 15.0)
        self.assertEqual(self.dummy_config.log_level, "DEBUG")

    @patch("main.load_config")
    @patch("main.TradeMonitoringService.run", new_callable=AsyncMock)
    async def test_async_main_test_minutes_10(
        self, mock_run: AsyncMock, mock_load_config: MagicMock
    ) -> None:
        mock_load_config.return_value = self.dummy_config

        exit_code = await async_main(["--test-minutes", "10"])
        self.assertEqual(exit_code, 0)
        mock_run.assert_awaited_once()
        self.assertEqual(self.dummy_config.capture.live_poll_interval_seconds, 15.0)

    @patch("main.load_config")
    @patch("main.TradeMonitoringService.run", new_callable=AsyncMock)
    async def test_async_main_test_minutes_preserves_explicit_log_level(
        self, mock_run: AsyncMock, mock_load_config: MagicMock
    ) -> None:
        mock_load_config.return_value = self.dummy_config

        exit_code = await async_main(["--test-minutes", "5", "--log-level", "WARNING"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.dummy_config.log_level, "WARNING")

    @patch("main.load_config")
    async def test_async_main_test_minutes_invalid_zero(
        self, mock_load_config: MagicMock
    ) -> None:
        mock_load_config.return_value = self.dummy_config

        exit_code = await async_main(["--test-minutes", "0"])
        self.assertEqual(exit_code, 1)

    @patch("main.load_config")
    async def test_async_main_test_minutes_invalid_negative(
        self, mock_load_config: MagicMock
    ) -> None:
        mock_load_config.return_value = self.dummy_config

        exit_code = await async_main(["--test-minutes", "-5"])
        self.assertEqual(exit_code, 1)

    @patch("main.load_config")
    async def test_async_main_test_minutes_invalid_exceeds_60(
        self, mock_load_config: MagicMock
    ) -> None:
        mock_load_config.return_value = self.dummy_config

        exit_code = await async_main(["--test-minutes", "60.1"])
        self.assertEqual(exit_code, 1)

        exit_code_100 = await async_main(["--test-minutes", "100"])
        self.assertEqual(exit_code_100, 1)

    @patch("main.load_config")
    @patch("main.TradeMonitoringService.run", new_callable=AsyncMock)
    async def test_async_main_test_minutes_valid_boundary_60(
        self, mock_run: AsyncMock, mock_load_config: MagicMock
    ) -> None:
        mock_load_config.return_value = self.dummy_config

        exit_code = await async_main(["--test-minutes", "60"])
        self.assertEqual(exit_code, 0)
        mock_run.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
