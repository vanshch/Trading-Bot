"""Unit and integration tests for the decoupled LiveChannelListener and durable deduplication."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from src.config import CaptureConfig
from src.live_alert_state import LiveAlertStateStore
from src.live_listener import LiveChannelListener
from src.stream_manager import StreamStatus


class TestLiveChannelListener(unittest.IsolatedAsyncioTestCase):
    """Test suite for decoupled YouTube live stream state tracking and alerts."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temp_dir.name) / "live_alert_state.json"
        self.alert_state = LiveAlertStateStore(storage_path=self.state_file)

        self.mock_notifier = MagicMock()
        self.mock_notifier.send_stream_live_alert = AsyncMock(return_value=True)
        self.mock_stream_manager = MagicMock()
        self.mock_stream_manager.check_live_status = AsyncMock()
        self.mock_stream_manager.close = AsyncMock()
        self.mock_stream_manager.broadcast_id = "test_broadcast_123"

        self.listener = LiveChannelListener(
            channel_name="Mehak Trades",
            channel_url="https://www.youtube.com/@tradewithmehaktrades/live",
            notifier=self.mock_notifier,
            poll_interval_seconds=0.05,
            capture_config=CaptureConfig(),
            stream_manager=self.mock_stream_manager,
            alert_state=self.alert_state,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_offline_to_live_triggers_immediate_alert(self) -> None:
        """Verify transitioning from OFFLINE to LIVE dispatches a Telegram alert immediately without delay."""
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_123"

        is_live, alerted = await self.listener.check_and_notify()

        self.assertTrue(is_live)
        self.assertTrue(alerted)
        self.mock_notifier.send_stream_live_alert.assert_awaited_once_with(
            channel_name="Mehak Trades",
            stream_url="https://www.youtube.com/@tradewithmehaktrades/live",
        )
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)
        self.assertTrue(
            self.alert_state.has_claimed(
                "https://www.youtube.com/@tradewithmehaktrades/live",
                "test_broadcast_123",
            )
        )

    async def test_debounce_multiple_consecutive_live_checks(self) -> None:
        """Verify that once alerted, subsequent live checks do not send duplicate alerts."""
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_123"

        # First check triggers alert
        is_live_1, alerted_1 = await self.listener.check_and_notify()
        self.assertTrue(is_live_1)
        self.assertTrue(alerted_1)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # Second check while still live does NOT trigger alert
        is_live_2, alerted_2 = await self.listener.check_and_notify()
        self.assertTrue(is_live_2)
        self.assertFalse(alerted_2)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # Third check while still live also does NOT trigger alert
        is_live_3, alerted_3 = await self.listener.check_and_notify()
        self.assertTrue(is_live_3)
        self.assertFalse(alerted_3)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

    async def test_unknown_status_preserves_live_state_and_debounce(self) -> None:
        """Verify transient UNKNOWN status preserves the prior live state and alert debounce."""
        # 1. Goes live and alerts
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_123"
        is_live_1, alerted_1 = await self.listener.check_and_notify()
        self.assertTrue(is_live_1)
        self.assertTrue(alerted_1)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # 2. Transient error occurs -> UNKNOWN status
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.UNKNOWN,
            None,
        )
        is_live_2, alerted_2 = await self.listener.check_and_notify()
        # Preserves live state and does not dispatch new alert
        self.assertTrue(is_live_2)
        self.assertFalse(alerted_2)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # 3. Next check is LIVE again -> debounce is still active, no duplicate alert
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        is_live_3, alerted_3 = await self.listener.check_and_notify()
        self.assertTrue(is_live_3)
        self.assertFalse(alerted_3)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

    async def test_single_offline_hysteresis_does_not_reset_debounce(self) -> None:
        """A single offline result does NOT reset debounce (single-offline hysteresis)."""
        # 1. Goes live and alerts
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_123"
        await self.listener.check_and_notify()
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # 2. Single OFFLINE observation
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.OFFLINE,
            None,
        )
        is_live_2, alerted_2 = await self.listener.check_and_notify()
        self.assertFalse(is_live_2)
        self.assertFalse(alerted_2)
        self.assertEqual(self.listener.consecutive_offline_count, 1)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # 3. Flaps back to LIVE -> debounce was NOT reset, no new alert
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        is_live_3, alerted_3 = await self.listener.check_and_notify()
        self.assertTrue(is_live_3)
        self.assertFalse(alerted_3)
        self.assertEqual(self.listener.consecutive_offline_count, 0)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

    async def test_two_consecutive_offline_resets_debounce_for_new_alert(self) -> None:
        """Two consecutive confirmed OFFLINE observations reset in-memory debounce, enabling a new live alert for a new broadcast ID."""
        # 1. Goes live and alerts
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_123"
        await self.listener.check_and_notify()
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # 2. First OFFLINE observation
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.OFFLINE,
            None,
        )
        await self.listener.check_and_notify()
        self.assertEqual(self.listener.consecutive_offline_count, 1)

        # 3. Second consecutive OFFLINE observation (confirms offline)
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.OFFLINE,
            None,
        )
        is_live_3, alerted_3 = await self.listener.check_and_notify()
        self.assertFalse(is_live_3)
        self.assertFalse(alerted_3)
        self.assertEqual(self.listener.consecutive_offline_count, 2)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # 4. Stream goes live again with NEW broadcast ID -> new live alert IS dispatched
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live2.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_456"
        is_live_4, alerted_4 = await self.listener.check_and_notify()
        self.assertTrue(is_live_4)
        self.assertTrue(alerted_4)
        self.assertEqual(self.listener.consecutive_offline_count, 0)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 2)

    async def test_two_consecutive_offline_followed_by_same_broadcast_id_suppresses_alert(
        self,
    ) -> None:
        """Two confirmed OFFLINEs followed by the SAME broadcast ID must NEVER send a second alert."""
        # 1. Goes live with ID "test_broadcast_123" and alerts
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_123"
        await self.listener.check_and_notify()
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # 2. Two consecutive confirmed OFFLINEs (resets in-memory debounce)
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.OFFLINE,
            None,
        )
        await self.listener.check_and_notify()
        await self.listener.check_and_notify()
        self.assertEqual(self.listener.consecutive_offline_count, 2)

        # 3. Stream reports LIVE again with the SAME broadcast ID "test_broadcast_123"
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_123"
        is_live, alerted = await self.listener.check_and_notify()
        self.assertTrue(is_live)
        self.assertFalse(alerted)
        # Call count must strictly remain 1
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

    async def test_unknown_interrupts_offline_consecutive_count(self) -> None:
        """An UNKNOWN result between OFFLINE checks resets the consecutive offline sequence."""
        # 1. Goes live
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_123"
        await self.listener.check_and_notify()
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # 2. First OFFLINE (count = 1)
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.OFFLINE,
            None,
        )
        await self.listener.check_and_notify()
        self.assertEqual(self.listener.consecutive_offline_count, 1)

        # 3. UNKNOWN error (resets consecutive count to 0)
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.UNKNOWN,
            None,
        )
        await self.listener.check_and_notify()
        self.assertEqual(self.listener.consecutive_offline_count, 0)

        # 4. Another OFFLINE (count = 1, not 2)
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.OFFLINE,
            None,
        )
        await self.listener.check_and_notify()
        self.assertEqual(self.listener.consecutive_offline_count, 1)

        # 5. Goes live again -> debounce was NOT reset because offlines were not consecutive
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_123"
        _, alerted = await self.listener.check_and_notify()
        self.assertFalse(alerted)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

    async def test_stream_check_failure_isolation(self) -> None:
        """Verify network or yt-dlp exception in status check does not crash listener."""
        self.mock_stream_manager.check_live_status.side_effect = RuntimeError(
            "Network timeout"
        )

        is_live, alerted = await self.listener.check_and_notify()

        self.assertFalse(is_live)
        self.assertFalse(alerted)
        self.mock_notifier.send_stream_live_alert.assert_not_awaited()

    async def test_stream_check_failure_when_live_preserves_live_state_and_debounce(
        self,
    ) -> None:
        """Exception during status check while live preserves live state and debounce."""
        # 1. Goes live and alerts
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_123"
        await self.listener.check_and_notify()
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # 2. Status check raises an exception
        self.mock_stream_manager.check_live_status.side_effect = RuntimeError(
            "503 timeout"
        )
        is_live_2, alerted_2 = await self.listener.check_and_notify()
        self.assertTrue(is_live_2)  # Prior live state preserved
        self.assertFalse(alerted_2)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # 3. Status check recovers to LIVE
        self.mock_stream_manager.check_live_status.side_effect = None
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_123"
        is_live_3, alerted_3 = await self.listener.check_and_notify()
        self.assertTrue(is_live_3)
        self.assertFalse(alerted_3)  # Debounce preserved, no duplicate alert
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

    async def test_telegram_dispatch_failure_never_releases_claim(self) -> None:
        """Telegram alert failure or exception never releases the durable claim."""
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_err_1"
        self.mock_notifier.send_stream_live_alert.return_value = False

        _, alerted = await self.listener.check_and_notify()
        self.assertFalse(alerted)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # Verify broadcast ID was claimed and retained despite Telegram delivery returning False
        self.assertTrue(
            self.alert_state.has_claimed(
                "https://www.youtube.com/@tradewithmehaktrades/live",
                "test_broadcast_err_1",
            )
        )

        # Next check for the same broadcast ID must NOT retry or re-send
        self.mock_notifier.send_stream_live_alert.return_value = True
        _, alerted_2 = await self.listener.check_and_notify()
        self.assertFalse(alerted_2)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

    async def test_missing_broadcast_id_suppresses_alert(self) -> None:
        """A missing ID suppresses the alert but a later valid ID remains eligible."""
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = None

        is_live, alerted = await self.listener.check_and_notify()
        self.assertTrue(is_live)
        self.assertFalse(alerted)
        self.mock_notifier.send_stream_live_alert.assert_not_awaited()

        self.mock_stream_manager.broadcast_id = "test_broadcast_after_missing_id"
        is_live_2, alerted_2 = await self.listener.check_and_notify()

        self.assertTrue(is_live_2)
        self.assertTrue(alerted_2)
        self.mock_notifier.send_stream_live_alert.assert_awaited_once()

    async def test_corrupt_state_fails_closed_and_suppresses_alert(self) -> None:
        """Corrupt state file causes claim failure and suppresses Telegram alert (fail closed)."""
        # Corrupt state file
        await asyncio.to_thread(
            self.state_file.write_text, "{corrupt_json_bad_file", encoding="utf-8"
        )

        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "test_broadcast_corrupt"

        is_live, alerted = await self.listener.check_and_notify()
        self.assertTrue(is_live)
        self.assertFalse(alerted)
        self.mock_notifier.send_stream_live_alert.assert_not_awaited()

    async def test_process_restart_with_persisted_state_suppresses_duplicate(
        self,
    ) -> None:
        """Simulating a full service restart with fresh listener suppresses alert for already-claimed broadcast."""
        # 1. First listener instance runs and alerts for broadcast "vid_restart_1"
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.LIVE,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.mock_stream_manager.broadcast_id = "vid_restart_1"
        _, alerted_1 = await self.listener.check_and_notify()
        self.assertTrue(alerted_1)
        self.assertEqual(self.mock_notifier.send_stream_live_alert.await_count, 1)

        # 2. Simulate process restart: create a new LiveAlertStateStore pointing to same file, and new LiveChannelListener
        restarted_state = LiveAlertStateStore(storage_path=self.state_file)
        new_mock_notifier = MagicMock()
        new_mock_notifier.send_stream_live_alert = AsyncMock(return_value=True)

        new_listener = LiveChannelListener(
            channel_name="Mehak Trades",
            channel_url="https://www.youtube.com/@tradewithmehaktrades/live",
            notifier=new_mock_notifier,
            poll_interval_seconds=0.05,
            capture_config=CaptureConfig(),
            stream_manager=self.mock_stream_manager,
            alert_state=restarted_state,
        )

        # 3. New listener starts up with in-memory _last_alerted_live=False, checks same live broadcast
        is_live, alerted_2 = await new_listener.check_and_notify()
        self.assertTrue(is_live)
        self.assertFalse(alerted_2)
        new_mock_notifier.send_stream_live_alert.assert_not_awaited()

    async def test_listen_loop_stops_cleanly(self) -> None:
        """Verify the continuous listen loop runs and cleanly terminates on stop_event."""
        stop_event = asyncio.Event()
        self.mock_stream_manager.check_live_status.return_value = (
            StreamStatus.OFFLINE,
            None,
        )

        task = asyncio.create_task(self.listener.listen(stop_event))
        await asyncio.sleep(0.08)

        self.assertFalse(task.done())
        stop_event.set()
        await asyncio.wait_for(task, timeout=0.5)

        self.assertTrue(task.done())


if __name__ == "__main__":
    unittest.main()
