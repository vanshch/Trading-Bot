import asyncio
import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import yt_dlp

from src.config import CaptureConfig
from src.stream_manager import (
    StreamManager,
    StreamStatus,
    select_best_audio_stream_url,
)


class TestStreamManager(unittest.IsolatedAsyncioTestCase):
    """Tests for StreamManager with mock yt-dlp and OpenCV VideoCapture."""

    def setUp(self) -> None:
        self.channel_url = "https://www.youtube.com/@SampleTrader/live"
        self.config = CaptureConfig(
            frame_interval_seconds=1.0,
            live_poll_interval_seconds=1.0,
            stream_timeout_seconds=5.0,
            max_consecutive_stream_errors=3,
        )
        self.manager = StreamManager(self.channel_url, self.config)

    async def asyncTearDown(self) -> None:
        await self.manager.close()

    @patch("yt_dlp.YoutubeDL")
    async def test_live_stream_resolution_success(
        self, mock_ydl_class: MagicMock
    ) -> None:
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "is_live": True,
            "url": "https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
        }
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        status, url = await self.manager.check_live_status()
        self.assertEqual(status, StreamStatus.LIVE)
        self.assertTrue(status)  # bool(StreamStatus.LIVE) is True
        self.assertEqual(
            url, "https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8"
        )
        self.assertTrue(self.manager.is_live)
        self.assertEqual(self.manager.status, StreamStatus.LIVE)
        self.assertEqual(self.manager.current_stream_url, url)

    @patch("yt_dlp.YoutubeDL")
    async def test_offline_stream_resolution(self, mock_ydl_class: MagicMock) -> None:
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "is_live": False,
            "live_status": "not_live",
            "url": None,
        }
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        status, url = await self.manager.check_live_status()
        self.assertEqual(status, StreamStatus.OFFLINE)
        self.assertFalse(status)  # bool(StreamStatus.OFFLINE) is False
        self.assertIsNone(url)
        self.assertFalse(self.manager.is_live)
        self.assertEqual(self.manager.status, StreamStatus.OFFLINE)

    @patch("yt_dlp.YoutubeDL")
    async def test_recorded_video_formats_do_not_count_as_live(
        self, mock_ydl_class: MagicMock
    ) -> None:
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "is_live": False,
            "live_status": "was_live",
            "url": "https://media.example/recording.mp4",
            "formats": [{"url": "https://media.example/recording.mp4"}],
        }
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        status, url = await self.manager.check_live_status()

        self.assertEqual(status, StreamStatus.OFFLINE)
        self.assertFalse(status)
        self.assertIsNone(url)

    @patch("yt_dlp.YoutubeDL")
    async def test_offline_download_error_handled(
        self, mock_ydl_class: MagicMock
    ) -> None:
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(
            "This live event has ended."
        )
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        status, url = await self.manager.check_live_status()
        self.assertEqual(status, StreamStatus.OFFLINE)
        self.assertFalse(status)
        self.assertIsNone(url)

    @patch("yt_dlp.YoutubeDL")
    async def test_transient_download_error_returns_unknown(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """Transient HTTP/extraction errors must return UNKNOWN instead of guessing OFFLINE."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(
            "HTTP Error 503: Service Unavailable"
        )
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        status, url = await self.manager.check_live_status()
        self.assertEqual(status, StreamStatus.UNKNOWN)
        self.assertFalse(status)
        self.assertIsNone(url)
        self.assertEqual(self.manager.status, StreamStatus.UNKNOWN)

    @patch("yt_dlp.YoutubeDL")
    async def test_transient_network_timeout_returns_unknown(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """Transient socket/network errors must resolve to UNKNOWN."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(
            "Connection reset by peer"
        )
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        status, url = await self.manager.check_live_status()
        self.assertEqual(status, StreamStatus.UNKNOWN)
        self.assertIsNone(url)

    @patch("yt_dlp.YoutubeDL")
    async def test_ambiguous_video_unavailable_returns_unknown(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """A generic unavailable response is not proof that a live ended."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(
            "Video unavailable"
        )
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        status, url = await self.manager.check_live_status()

        self.assertEqual(status, StreamStatus.UNKNOWN)
        self.assertIsNone(url)

    @patch("yt_dlp.YoutubeDL")
    async def test_generic_exception_returns_unknown(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """Unexpected non-DownloadError exceptions fail closed to UNKNOWN."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = TimeoutError("Remote connection timed out")
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        status, url = await self.manager.check_live_status()
        self.assertEqual(status, StreamStatus.UNKNOWN)
        self.assertIsNone(url)

    @patch("yt_dlp.YoutubeDL")
    async def test_unknown_status_preserves_prior_live_state(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """A transient UNKNOWN result must preserve the active live stream URL and is_live."""
        mock_ydl = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        # 1. Successful LIVE resolution
        mock_ydl.extract_info.return_value = {
            "is_live": True,
            "url": "https://manifest.googlevideo.com/live.m3u8",
        }
        status1, url1 = await self.manager.check_live_status()
        self.assertEqual(status1, StreamStatus.LIVE)
        self.assertEqual(url1, "https://manifest.googlevideo.com/live.m3u8")
        self.assertTrue(self.manager.is_live)
        self.assertEqual(
            self.manager.current_stream_url,
            "https://manifest.googlevideo.com/live.m3u8",
        )

        # 2. Transient error occurring while live
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(
            "HTTP Error 500: Internal Server Error"
        )
        status2, url2 = await self.manager.check_live_status()
        self.assertEqual(status2, StreamStatus.UNKNOWN)
        self.assertIsNone(url2)
        # Verify prior live state was preserved
        self.assertTrue(self.manager.is_live)
        self.assertEqual(
            self.manager.current_stream_url,
            "https://manifest.googlevideo.com/live.m3u8",
        )
        self.assertEqual(self.manager.status, StreamStatus.UNKNOWN)

    @patch("yt_dlp.YoutubeDL")
    async def test_broadcast_id_extraction_and_lifecycle(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """StreamManager extracts broadcast_id, preserves it on UNKNOWN, and clears it on OFFLINE and close."""
        mock_ydl = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        # Initial state before checking
        self.assertIsNone(self.manager.broadcast_id)

        # 1. LIVE with broadcast ID
        mock_ydl.extract_info.return_value = {
            "id": "yt_video_12345",
            "is_live": True,
            "url": "https://manifest.googlevideo.com/live.m3u8",
        }
        status1, _ = await self.manager.check_live_status()
        self.assertEqual(status1, StreamStatus.LIVE)
        self.assertEqual(self.manager.broadcast_id, "yt_video_12345")

        # 2. Transient UNKNOWN preserves broadcast_id
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(
            "503 Server Error"
        )
        status2, _ = await self.manager.check_live_status()
        self.assertEqual(status2, StreamStatus.UNKNOWN)
        self.assertEqual(self.manager.broadcast_id, "yt_video_12345")

        # 3. Confirmed OFFLINE clears broadcast_id
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(
            "Channel is not live"
        )
        status3, _ = await self.manager.check_live_status()
        self.assertEqual(status3, StreamStatus.OFFLINE)
        self.assertIsNone(self.manager.broadcast_id)

        # 4. Goes LIVE again, then close() clears broadcast_id
        mock_ydl.extract_info.side_effect = None
        mock_ydl.extract_info.return_value = {
            "id": "yt_video_67890",
            "is_live": True,
            "url": "https://manifest.googlevideo.com/live2.m3u8",
        }
        await self.manager.check_live_status()
        self.assertEqual(self.manager.broadcast_id, "yt_video_67890")

        await self.manager.close()
        self.assertIsNone(self.manager.broadcast_id)

    @patch("yt_dlp.YoutubeDL")
    async def test_broadcast_start_time_extracted_from_release_timestamp(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """StreamManager extracts validated release_timestamp, preserves it on UNKNOWN, and clears on OFFLINE/close."""
        mock_ydl = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        test_now = time.time()
        start_ts = test_now - 1200.0  # broadcast started 20 minutes ago

        # 1. LIVE with release_timestamp
        mock_ydl.extract_info.return_value = {
            "id": "video_broadcast_1",
            "is_live": True,
            "release_timestamp": start_ts,
            "url": "https://manifest.googlevideo.com/live.m3u8",
        }
        status, _ = await self.manager.check_live_status()
        self.assertEqual(status, StreamStatus.LIVE)
        self.assertEqual(self.manager.broadcast_start_time, start_ts)

        # 2. Transient UNKNOWN preserves broadcast_start_time
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError("503")
        status2, _ = await self.manager.check_live_status()
        self.assertEqual(status2, StreamStatus.UNKNOWN)
        self.assertEqual(self.manager.broadcast_start_time, start_ts)

        # 3. OFFLINE clears broadcast_start_time
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError("Channel is not live")
        status3, _ = await self.manager.check_live_status()
        self.assertEqual(status3, StreamStatus.OFFLINE)
        self.assertIsNone(self.manager.broadcast_start_time)

    @patch("yt_dlp.YoutubeDL")
    async def test_broadcast_start_time_fallback_to_first_live_detection(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """When release_timestamp is absent, broadcast_start_time anchors to first live detection."""
        mock_ydl = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        mock_ydl.extract_info.return_value = {
            "id": "video_broadcast_2",
            "is_live": True,
            "url": "https://manifest.googlevideo.com/live.m3u8",
        }
        before = time.time()
        status, _ = await self.manager.check_live_status()
        after = time.time()

        self.assertEqual(status, StreamStatus.LIVE)
        first_time = self.manager.broadcast_start_time
        self.assertIsNotNone(first_time)
        self.assertTrue(before <= first_time <= after)

        # Subsequent check while still live preserves the exact initial timestamp
        await asyncio.sleep(0.01)
        mock_ydl.extract_info.return_value = {
            "id": "video_broadcast_2",
            "is_live": True,
            "url": "https://manifest.googlevideo.com/live.m3u8",
        }
        await self.manager.check_live_status()
        self.assertEqual(self.manager.broadcast_start_time, first_time)

        await self.manager.close()
        self.assertIsNone(self.manager.broadcast_start_time)

    @patch("yt_dlp.YoutubeDL")
    async def test_new_broadcast_id_resets_fallback_start_clock(
        self, mock_ydl_class: MagicMock
    ) -> None:
        mock_ydl = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {
            "id": "broadcast-one",
            "is_live": True,
            "url": "https://manifest.googlevideo.com/live-one.m3u8",
        }
        await self.manager.check_live_status()
        first_start = self.manager.broadcast_start_time

        await asyncio.sleep(0.01)
        mock_ydl.extract_info.return_value = {
            "id": "broadcast-two",
            "is_live": True,
            "url": "https://manifest.googlevideo.com/live-two.m3u8",
        }
        await self.manager.check_live_status()

        self.assertGreater(self.manager.broadcast_start_time, first_start)

    @patch("yt_dlp.YoutubeDL")
    async def test_changed_resolved_url_releases_stale_video_capture(
        self, mock_ydl_class: MagicMock
    ) -> None:
        mock_ydl = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {
            "id": "same-broadcast",
            "is_live": True,
            "url": "https://manifest.googlevideo.com/live-one.m3u8",
        }
        await self.manager.check_live_status()
        old_capture = MagicMock()
        self.manager._cap = old_capture

        mock_ydl.extract_info.return_value = {
            "id": "same-broadcast",
            "is_live": True,
            "url": "https://manifest.googlevideo.com/live-two.m3u8",
        }
        await self.manager.check_live_status()

        old_capture.release.assert_called_once()
        self.assertIsNone(self.manager._cap)

    @patch("yt_dlp.YoutubeDL")
    async def test_live_stream_without_broadcast_id_sets_none(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """If yt-dlp metadata lacks an id, broadcast_id resolves to None."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "is_live": True,
            "url": "https://manifest.googlevideo.com/live.m3u8",
        }
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        status, _ = await self.manager.check_live_status()
        self.assertEqual(status, StreamStatus.LIVE)
        self.assertIsNone(self.manager.broadcast_id)

    @patch("yt_dlp.YoutubeDL")
    async def test_offline_status_clears_prior_live_state(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """Confirmed OFFLINE result cleanly clears live state and stream URL."""
        mock_ydl = MagicMock()
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        # 1. Stream goes LIVE
        mock_ydl.extract_info.return_value = {
            "is_live": True,
            "url": "https://manifest.googlevideo.com/live.m3u8",
        }
        await self.manager.check_live_status()
        self.assertTrue(self.manager.is_live)

        # 2. Stream explicitly confirmed OFFLINE
        mock_ydl.extract_info.return_value = None
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(
            "Channel is not live"
        )
        status, url = await self.manager.check_live_status()
        self.assertEqual(status, StreamStatus.OFFLINE)
        self.assertIsNone(url)
        self.assertFalse(self.manager.is_live)
        self.assertIsNone(self.manager.current_stream_url)
        self.assertEqual(self.manager.status, StreamStatus.OFFLINE)

    async def test_capture_frame_when_offline_returns_none(self) -> None:
        # StreamManager is not live initially
        frame = await self.manager.capture_frame()
        self.assertIsNone(frame)

    @patch("cv2.VideoCapture")
    async def test_capture_frame_when_live_success(
        self, mock_cap_class: MagicMock
    ) -> None:
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        fake_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        mock_cap.read.return_value = (True, fake_frame)
        mock_cap_class.return_value = mock_cap

        # Manually set live state and stream URL
        self.manager._is_live = True
        self.manager._current_stream_url = "https://test.stream/live.m3u8"

        frame = await self.manager.capture_frame()
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape, (100, 100, 3))

    @patch("cv2.VideoCapture")
    async def test_repeated_frame_errors_return_to_live_polling(
        self, mock_cap_class: MagicMock
    ) -> None:
        """A stale media URL is discarded after the configured failure threshold."""
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (False, None)
        mock_cap_class.return_value = mock_cap
        self.manager._is_live = True
        self.manager._current_stream_url = "https://test.stream/expired.m3u8"

        for _ in range(self.config.max_consecutive_stream_errors):
            self.assertIsNone(await self.manager.capture_frame())

        self.assertFalse(self.manager.is_live)
        self.assertIsNone(self.manager.current_stream_url)
        mock_cap.release.assert_called_once()

    @patch("yt_dlp.YoutubeDL")
    async def test_live_stream_with_only_video_formats_returns_unknown_not_offline(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """A live stream where only video-only formats are available must return UNKNOWN, not OFFLINE."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "is_live": True,
            "formats": [
                {
                    "format_id": "137",
                    "url": "https://media.test/video1080p.mp4",
                    "vcodec": "avc1.640028",
                    "acodec": "none",
                },
                {
                    "format_id": "248",
                    "url": "https://media.test/video1080p.webm",
                    "vcodec": "vp9",
                    "acodec": "none",
                },
            ],
        }
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        status, url = await self.manager.check_live_status()
        self.assertEqual(status, StreamStatus.UNKNOWN)
        self.assertIsNone(url)
        self.assertEqual(self.manager.status, StreamStatus.UNKNOWN)

    @patch("yt_dlp.YoutubeDL")
    async def test_live_stream_resolution_selects_ranked_hls_audio_format(
        self, mock_ydl_class: MagicMock
    ) -> None:
        """Select continuous muxed HLS so audio and chart frames remain available."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "is_live": True,
            "formats": [
                {
                    "format_id": "137",
                    "url": "https://media.test/video1080p.mp4",
                    "vcodec": "avc1.640028",
                    "acodec": "none",
                },
                {
                    "format_id": "93",
                    "url": "https://manifest.googlevideo.com/hls_720p.m3u8",
                    "protocol": "m3u8_native",
                    "vcodec": "avc1.4d401f",
                    "acodec": "mp4a.40.2",
                    "abr": 128,
                },
                {
                    "format_id": "91",
                    "url": "https://manifest.googlevideo.com/hls_audio_128k.m3u8",
                    "protocol": "m3u8_native",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                    "abr": 128,
                },
            ],
        }
        mock_ydl_class.return_value.__enter__.return_value = mock_ydl

        status, url = await self.manager.check_live_status()
        self.assertEqual(status, StreamStatus.LIVE)
        self.assertEqual(url, "https://manifest.googlevideo.com/hls_720p.m3u8")
        self.assertTrue(self.manager.is_live)
        self.assertEqual(self.manager.current_stream_url, url)


class TestStreamFormatSelection(unittest.TestCase):
    """Deterministic offline tests for select_best_audio_stream_url format ranking and fallback."""

    def test_select_prefers_muxed_hls_over_all_others(self) -> None:
        """Muxed HLS preserves both reliable audio and screenshot capability."""
        info = {
            "is_live": True,
            "formats": [
                {
                    "format_id": "137",
                    "url": "https://media.test/video1080.mp4",
                    "vcodec": "avc1.640028",
                    "acodec": "none",
                },
                {
                    "format_id": "18",
                    "url": "https://media.test/muxed360.mp4",
                    "vcodec": "avc1.42001E",
                    "acodec": "mp4a.40.2",
                },
                {
                    "format_id": "140",
                    "url": "https://media.test/prog_audio128.m4a",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                    "abr": 128,
                },
                {
                    "format_id": "93",
                    "url": "https://media.test/hls_muxed720.m3u8",
                    "protocol": "m3u8_native",
                    "vcodec": "avc1.4d401f",
                    "acodec": "mp4a.40.2",
                    "abr": 128,
                },
                {
                    "format_id": "91",
                    "url": "https://media.test/hls_audio128.m3u8",
                    "protocol": "m3u8_native",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                    "abr": 128,
                },
            ],
        }

        selected_url = select_best_audio_stream_url(info)
        self.assertEqual(selected_url, "https://media.test/hls_muxed720.m3u8")

    def test_select_prefers_higher_audio_bitrate(self) -> None:
        """Within the same tier, higher audio bitrate format must be selected."""
        info = {
            "formats": [
                {
                    "format_id": "91",
                    "url": "https://media.test/hls_audio48.m3u8",
                    "protocol": "m3u8",
                    "vcodec": "none",
                    "acodec": "aac",
                    "abr": 48,
                },
                {
                    "format_id": "92",
                    "url": "https://media.test/hls_audio128.m3u8",
                    "protocol": "m3u8",
                    "vcodec": "none",
                    "acodec": "aac",
                    "abr": 128,
                },
            ]
        }

        selected_url = select_best_audio_stream_url(info)
        self.assertEqual(selected_url, "https://media.test/hls_audio128.m3u8")

    def test_select_falls_back_to_hls_muxed_when_audio_only_missing(self) -> None:
        """When HLS audio-only is not available, fall back to continuous HLS muxed stream."""
        info = {
            "formats": [
                {
                    "format_id": "137",
                    "url": "https://media.test/video1080.mp4",
                    "vcodec": "avc1",
                    "acodec": "none",
                },
                {
                    "format_id": "93",
                    "url": "https://media.test/hls_muxed720.m3u8",
                    "protocol": "m3u8_native",
                    "vcodec": "avc1.4d401f",
                    "acodec": "mp4a.40.2",
                    "abr": 128,
                },
            ]
        }

        selected_url = select_best_audio_stream_url(info)
        self.assertEqual(selected_url, "https://media.test/hls_muxed720.m3u8")

    def test_select_falls_back_to_progressive_audio_when_no_hls(self) -> None:
        """When HLS is not available, fall back to progressive audio-only format."""
        info = {
            "formats": [
                {
                    "format_id": "137",
                    "url": "https://media.test/video1080.mp4",
                    "vcodec": "avc1",
                    "acodec": "none",
                },
                {
                    "format_id": "18",
                    "url": "https://media.test/muxed360.mp4",
                    "vcodec": "avc1",
                    "acodec": "aac",
                },
                {
                    "format_id": "140",
                    "url": "https://media.test/audio128.m4a",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                    "abr": 128,
                },
            ]
        }

        selected_url = select_best_audio_stream_url(info)
        self.assertEqual(selected_url, "https://media.test/audio128.m4a")

    def test_select_falls_back_to_progressive_muxed_when_no_audio_only(self) -> None:
        """When neither HLS nor audio-only are available, fall back to progressive muxed stream."""
        info = {
            "formats": [
                {
                    "format_id": "137",
                    "url": "https://media.test/video1080.mp4",
                    "vcodec": "avc1",
                    "acodec": "none",
                },
                {
                    "format_id": "18",
                    "url": "https://media.test/muxed360.mp4",
                    "vcodec": "avc1",
                    "acodec": "aac",
                },
            ]
        }

        selected_url = select_best_audio_stream_url(info)
        self.assertEqual(selected_url, "https://media.test/muxed360.mp4")

    def test_select_rejects_video_only_formats(self) -> None:
        """Formats with acodec='none' or format_note='video only' must be rejected."""
        info = {
            "formats": [
                {
                    "format_id": "137",
                    "url": "https://media.test/video1080.mp4",
                    "vcodec": "avc1",
                    "acodec": "none",
                },
                {
                    "format_id": "248",
                    "url": "https://media.test/video_vp9.webm",
                    "vcodec": "vp9",
                    "format_note": "video only",
                },
            ]
        }

        selected_url = select_best_audio_stream_url(info)
        self.assertIsNone(selected_url)

    def test_select_handles_malformed_and_empty_metadata(self) -> None:
        """Malformed, None, or empty metadata structures must safely evaluate to None."""
        self.assertIsNone(select_best_audio_stream_url(None))
        self.assertIsNone(select_best_audio_stream_url({}))
        self.assertIsNone(select_best_audio_stream_url({"formats": None}))
        self.assertIsNone(select_best_audio_stream_url({"formats": []}))
        self.assertIsNone(
            select_best_audio_stream_url(
                {"formats": [None, "invalid", {}, {"url": ""}, {"url": "   "}]}
            )
        )

    def test_select_top_level_url_fallback(self) -> None:
        """Top-level URL without formats list is selected as candidate."""
        info = {
            "url": "https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
        }
        selected_url = select_best_audio_stream_url(info)
        self.assertEqual(
            selected_url,
            "https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
        )


if __name__ == "__main__":
    unittest.main()
