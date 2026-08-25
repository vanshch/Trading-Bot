"""Offline tests for FFmpeg audio-stream self-recovering transport and backoff."""

import asyncio
import collections
import io
import sys
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.audio_stream import (
    AudioStreamManager,
    RestartBackoffTracker,
    format_diagnostic_tail,
    sanitize_diagnostic,
)
from src.logger import register_secret_for_redaction


class TestAudioStreamManager(unittest.IsolatedAsyncioTestCase):
    def test_ffmpeg_command_enables_bounded_reconnects(self) -> None:
        manager = AudioStreamManager("https://media.example/live.m3u8")
        command = manager._build_ffmpeg_command(input_from_stdin=False)

        self.assertIn("-reconnect", command)
        self.assertIn("-reconnect_streamed", command)
        self.assertIn("-reconnect_delay_max", command)
        self.assertIn("-rw_timeout", command)
        self.assertEqual(command[-1], "pipe:1")

    def test_streamlink_command_enables_hls_recovery_and_buffering(self) -> None:
        manager = AudioStreamManager("https://media.example/live.m3u8")
        command = manager._build_streamlink_command()

        self.assertEqual(command[:3], [sys.executable, "-m", "streamlink"])
        self.assertIn("--stdout", command)

        expected_flags = {
            "--retry-streams": "15",
            "--retry-max": "3",
            "--retry-open": "3",
            "--ringbuffer-size": "64M",
            "--stream-segment-attempts": "5",
            "--stream-segment-threads": "1",
            "--stream-segment-timeout": "20",
            "--hls-live-edge": "6",
            "--hls-playlist-reload-attempts": "10",
            "--hls-playlist-reload-time": "segment",
            "--stream-segmented-queue-deadline": "0",
            "--stream-timeout": "120",
            "--http-timeout": "30.0",
            "--loglevel": "warning",
        }
        for flag, expected_val in expected_flags.items():
            self.assertIn(flag, command)
            idx = command.index(flag)
            self.assertEqual(
                command[idx + 1],
                expected_val,
                f"Flag {flag} expected {expected_val}, got {command[idx + 1]}",
            )

        self.assertIn("hls://https://media.example/live.m3u8", command)
        self.assertEqual(command[-1], "audio_only,worst")
        self.assertNotIn("best", command)

    def test_streamlink_command_passes_youtube_url_directly(self) -> None:
        manager = AudioStreamManager("https://www.youtube.com/watch?v=sample123")
        command = manager._build_streamlink_command()

        self.assertEqual(command[:3], [sys.executable, "-m", "streamlink"])
        self.assertIn("https://www.youtube.com/watch?v=sample123", command)
        self.assertNotIn("hls://https://www.youtube.com/watch?v=sample123", command)
        self.assertEqual(command[-1], "audio_only,worst")
        self.assertNotIn("best", command)

    def test_streamlink_command_url_handling_matrix(self) -> None:
        """Verify URL prefixing behavior across YouTube, youtu.be, Twitch, and raw HLS URLs."""
        test_cases = [
            ("https://youtu.be/sample456", "https://youtu.be/sample456", False),
            ("https://www.youtube.com/channel/UC12345", "https://www.youtube.com/channel/UC12345", False),
            ("https://twitch.tv/liveuser", "https://twitch.tv/liveuser", False),
            ("https://www.twitch.tv/liveuser", "https://www.twitch.tv/liveuser", False),
            ("hls://https://custom.stream/live.m3u8", "hls://https://custom.stream/live.m3u8", False),
            ("https://custom.stream/live.m3u8", "hls://https://custom.stream/live.m3u8", True),
        ]
        for input_url, expected_target, was_prefixed in test_cases:
            mgr = AudioStreamManager(input_url)
            cmd = mgr._build_streamlink_command()
            self.assertIn(expected_target, cmd)
            self.assertEqual(cmd[-1], "audio_only,worst")
            self.assertNotIn("best", cmd)
            if was_prefixed:
                self.assertNotIn(f"hls://{expected_target}", cmd)

    def test_ffmpeg_reads_streamlink_output_from_stdin(self) -> None:
        manager = AudioStreamManager("https://media.example/live.m3u8")
        command = manager._build_ffmpeg_command(input_from_stdin=True)

        self.assertIn("pipe:0", command)
        self.assertNotIn("-reconnect", command)
        self.assertEqual(command[-1], "pipe:1")

    def test_direct_url_propagation_in_ffmpeg_command(self) -> None:
        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=sample123",
            direct_url="https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
        )
        # Streamlink command uses public watch URL
        sl_command = manager._build_streamlink_command()
        self.assertIn("https://www.youtube.com/watch?v=sample123", sl_command)

        # Direct FFmpeg command uses direct media URL
        ffmpeg_command = manager._build_ffmpeg_command(input_from_stdin=False)
        self.assertIn(
            "https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
            ffmpeg_command,
        )
        self.assertNotIn("https://www.youtube.com/watch?v=sample123", ffmpeg_command)

    async def test_two_consecutive_timeouts_restart_audio_capture(self) -> None:
        process = MagicMock()
        process.stdout.read.return_value = b""
        process.stderr.readline.return_value = b""
        process.poll.return_value = None
        process.wait.return_value = 0

        manager = AudioStreamManager(
            "https://media.example/live.m3u8",
            chunk_duration_seconds=0.01,
            max_consecutive_timeouts=2,
            use_streamlink=False,
        )
        queue = MagicMock()
        queue.get = AsyncMock(side_effect=asyncio.TimeoutError)

        with (
            patch("src.audio_stream.subprocess.Popen", return_value=process),
            patch("src.audio_stream.asyncio.Queue", return_value=queue),
        ):
            chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(chunks, [])
        self.assertEqual(queue.get.await_count, 2)
        process.terminate.assert_called_once()

    async def test_streamlink_stdout_is_piped_into_ffmpeg(self) -> None:
        streamlink_process = MagicMock()
        streamlink_process.poll.return_value = None
        streamlink_process.wait.return_value = 0
        streamlink_process.stderr.readline.return_value = b""

        ffmpeg_process = MagicMock()
        ffmpeg_process.stdout.read.return_value = b""
        ffmpeg_process.stderr.readline.return_value = b""
        ffmpeg_process.poll.return_value = None
        ffmpeg_process.wait.return_value = 0

        fallback_process = MagicMock()
        fallback_process.stdout.read.return_value = b""
        fallback_process.stderr.readline.return_value = b""
        fallback_process.poll.return_value = None
        fallback_process.wait.return_value = 0

        manager = AudioStreamManager("https://media.example/live.m3u8")

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[streamlink_process, ffmpeg_process, fallback_process],
        ) as popen:
            chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(chunks, [])
        self.assertEqual(popen.call_count, 3)
        self.assertIs(
            popen.call_args_list[1].kwargs["stdin"], streamlink_process.stdout
        )
        streamlink_process.stdout.close.assert_called_once()
        ffmpeg_process.terminate.assert_called_once()
        streamlink_process.terminate.assert_called_once()
        fallback_process.terminate.assert_called_once()

    async def test_streamlink_start_error_falls_back_to_direct_ffmpeg(self) -> None:
        direct_process = MagicMock()
        direct_process.stdout.read.return_value = b""
        direct_process.stderr.readline.return_value = b""
        direct_process.poll.return_value = None
        direct_process.wait.return_value = 0
        manager = AudioStreamManager("https://media.example/live.m3u8")

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[OSError("streamlink unavailable"), direct_process],
        ) as popen:
            chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(chunks, [])
        self.assertEqual(popen.call_count, 2)
        fallback_command = popen.call_args_list[1].args[0]
        self.assertIn("-reconnect", fallback_command)
        direct_process.terminate.assert_called_once()

    async def test_runtime_streamlink_exit_triggers_direct_fallback_and_yields_chunks(
        self,
    ) -> None:
        """Streamlink exits during capture; direct FFmpeg fallback recovers and yields next chunk."""
        chunk1 = b"\x01\x02" * 80000  # 160000 bytes = 5s chunk
        chunk2 = b"\x03\x04" * 80000

        sl_proc = MagicMock()
        sl_proc.poll.return_value = None
        sl_proc.wait.return_value = 0
        sl_proc.stderr.readline.return_value = b""

        # Initial FFmpeg yields chunk 1, then reaches EOF as streamlink exits
        ff_proc1 = MagicMock()
        ff_proc1.poll.return_value = None
        ff_proc1.wait.return_value = 0
        ff_proc1.stderr.readline.return_value = b""
        ff_proc1.stdout.read.side_effect = [chunk1, b""]

        # Fallback direct FFmpeg yields chunk 2, then EOF
        ff_proc2 = MagicMock()
        ff_proc2.poll.return_value = None
        ff_proc2.wait.return_value = 0
        ff_proc2.stderr.readline.return_value = b""
        ff_proc2.stdout.read.side_effect = [chunk2, b""]

        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=sample123",
            direct_url="https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
            chunk_duration_seconds=5.0,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc1, ff_proc2],
        ) as popen:
            collected_chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        # Both chunks recovered across the single-session fallback
        self.assertEqual(len(collected_chunks), 2)
        self.assertEqual(collected_chunks[0], chunk1)
        self.assertEqual(collected_chunks[1], chunk2)

        # Fallback occurred exactly once (sl_proc + ff_proc1 + ff_proc2 = 3 Popen calls)
        self.assertEqual(popen.call_count, 3)

        # Direct FFmpeg used the resolved direct URL
        fallback_cmd = popen.call_args_list[2].args[0]
        self.assertIn(
            "https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
            fallback_cmd,
        )
        self.assertIn("-reconnect", fallback_cmd)

    async def test_direct_fallback_failure_terminates_session(self) -> None:
        """When direct fallback also fails to start, capture halts and returns control."""
        chunk1 = b"\x01\x02" * 80000

        sl_proc = MagicMock()
        sl_proc.poll.return_value = None
        sl_proc.wait.return_value = 0
        sl_proc.stderr.readline.return_value = b""

        ff_proc1 = MagicMock()
        ff_proc1.poll.return_value = None
        ff_proc1.wait.return_value = 0
        ff_proc1.stderr.readline.return_value = b""
        ff_proc1.stdout.read.side_effect = [chunk1, b""]

        manager = AudioStreamManager(
            stream_url="https://media.example/live.m3u8",
            chunk_duration_seconds=5.0,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc1, OSError("direct ffmpeg binary missing")],
        ) as popen:
            collected_chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(len(collected_chunks), 1)
        self.assertEqual(collected_chunks[0], chunk1)
        # Attempted fallback exactly once
        self.assertEqual(popen.call_count, 3)

    async def test_streamlink_stall_triggers_direct_fallback(self) -> None:
        """Consecutive timeouts under Streamlink trigger direct FFmpeg fallback."""
        chunk2 = b"\x05\x06" * 80000

        sl_proc = MagicMock()
        sl_proc.poll.return_value = None
        sl_proc.wait.return_value = 0
        sl_proc.stderr.readline.return_value = b""

        # Initial FFmpeg hangs (does not return data before timeout)
        ff_proc1 = MagicMock()
        ff_proc1.poll.return_value = None
        ff_proc1.wait.return_value = 0
        ff_proc1.stderr.readline.return_value = b""
        # Will block indefinitely in read
        read_event = threading.Event()

        def block_read(_size: int) -> bytes:
            read_event.wait(timeout=1.0)
            return b""

        ff_proc1.stdout.read.side_effect = block_read

        # Direct FFmpeg yields chunk 2 then EOF
        ff_proc2 = MagicMock()
        ff_proc2.poll.return_value = None
        ff_proc2.wait.return_value = 0
        ff_proc2.stderr.readline.return_value = b""
        ff_proc2.stdout.read.side_effect = [chunk2, b""]

        manager = AudioStreamManager(
            stream_url="https://media.example/live.m3u8",
            chunk_duration_seconds=0.01,
            max_consecutive_timeouts=1,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc1, ff_proc2],
        ):
            chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        read_event.set()
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], chunk2)

    async def test_runtime_eof_refreshes_direct_url_before_fallback_ffmpeg_starts(
        self,
    ) -> None:
        """Streamlink EOF awaits URL refresher and fallback FFmpeg uses the refreshed URL."""
        chunk1 = b"\x01\x02" * 80000
        chunk2 = b"\x03\x04" * 80000

        sl_proc = MagicMock()
        sl_proc.poll.return_value = None
        sl_proc.wait.return_value = 0
        sl_proc.stderr.readline.return_value = b""

        ff_proc1 = MagicMock()
        ff_proc1.poll.return_value = None
        ff_proc1.wait.return_value = 0
        ff_proc1.stderr.readline.return_value = b""
        ff_proc1.stdout.read.side_effect = [chunk1, b""]

        ff_proc2 = MagicMock()
        ff_proc2.poll.return_value = None
        ff_proc2.wait.return_value = 0
        ff_proc2.stderr.readline.return_value = b""
        ff_proc2.stdout.read.side_effect = [chunk2, b""]

        url_refresher = AsyncMock(
            return_value="https://manifest.googlevideo.com/refreshed_live_eof.m3u8"
        )

        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=sample123",
            direct_url="https://manifest.googlevideo.com/stale_initial.m3u8",
            chunk_duration_seconds=5.0,
            url_refresher=url_refresher,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc1, ff_proc2],
        ) as popen:
            collected_chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(len(collected_chunks), 2)
        self.assertEqual(collected_chunks[0], chunk1)
        self.assertEqual(collected_chunks[1], chunk2)

        # Refresher was called exactly once
        url_refresher.assert_awaited_once()

        # Fallback FFmpeg used the refreshed URL, not the stale initial URL
        fallback_cmd = popen.call_args_list[2].args[0]
        self.assertIn(
            "https://manifest.googlevideo.com/refreshed_live_eof.m3u8",
            fallback_cmd,
        )
        self.assertNotIn("https://manifest.googlevideo.com/stale_initial.m3u8", fallback_cmd)
        self.assertEqual(manager.direct_url, "https://manifest.googlevideo.com/refreshed_live_eof.m3u8")

    async def test_runtime_stall_refreshes_direct_url_before_fallback_ffmpeg_starts(
        self,
    ) -> None:
        """Streamlink stall awaits URL refresher and fallback FFmpeg uses the refreshed URL."""
        chunk2 = b"\x05\x06" * 80000

        sl_proc = MagicMock()
        sl_proc.poll.return_value = None
        sl_proc.wait.return_value = 0
        sl_proc.stderr.readline.return_value = b""

        ff_proc1 = MagicMock()
        ff_proc1.poll.return_value = None
        ff_proc1.wait.return_value = 0
        ff_proc1.stderr.readline.return_value = b""
        read_event = threading.Event()

        def block_read(_size: int) -> bytes:
            read_event.wait(timeout=1.0)
            return b""

        ff_proc1.stdout.read.side_effect = block_read

        ff_proc2 = MagicMock()
        ff_proc2.poll.return_value = None
        ff_proc2.wait.return_value = 0
        ff_proc2.stderr.readline.return_value = b""
        ff_proc2.stdout.read.side_effect = [chunk2, b""]

        url_refresher = AsyncMock(
            return_value="https://manifest.googlevideo.com/refreshed_live_stall.m3u8"
        )

        manager = AudioStreamManager(
            stream_url="https://media.example/live.m3u8",
            direct_url="https://manifest.googlevideo.com/stale_initial.m3u8",
            chunk_duration_seconds=0.01,
            max_consecutive_timeouts=1,
            url_refresher=url_refresher,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc1, ff_proc2],
        ) as popen:
            chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        read_event.set()
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], chunk2)
        url_refresher.assert_awaited_once()

        fallback_cmd = popen.call_args_list[2].args[0]
        self.assertIn(
            "https://manifest.googlevideo.com/refreshed_live_stall.m3u8",
            fallback_cmd,
        )
        self.assertNotIn("https://manifest.googlevideo.com/stale_initial.m3u8", fallback_cmd)
        self.assertEqual(manager.direct_url, "https://manifest.googlevideo.com/refreshed_live_stall.m3u8")

    async def test_refresh_exception_retains_cached_url_and_continues_fallback(
        self,
    ) -> None:
        """When URL refresher raises an exception, fallback proceeds using the existing cached direct URL."""
        chunk1 = b"\x01\x02" * 80000
        chunk2 = b"\x03\x04" * 80000

        sl_proc = MagicMock()
        sl_proc.poll.return_value = None
        sl_proc.wait.return_value = 0
        sl_proc.stderr.readline.return_value = b""

        ff_proc1 = MagicMock()
        ff_proc1.poll.return_value = None
        ff_proc1.wait.return_value = 0
        ff_proc1.stderr.readline.return_value = b""
        ff_proc1.stdout.read.side_effect = [chunk1, b""]

        ff_proc2 = MagicMock()
        ff_proc2.poll.return_value = None
        ff_proc2.wait.return_value = 0
        ff_proc2.stderr.readline.return_value = b""
        ff_proc2.stdout.read.side_effect = [chunk2, b""]

        url_refresher = AsyncMock(
            side_effect=RuntimeError("yt-dlp transient network failure")
        )

        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=sample123",
            direct_url="https://manifest.googlevideo.com/cached_stale.m3u8",
            chunk_duration_seconds=5.0,
            url_refresher=url_refresher,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc1, ff_proc2],
        ) as popen:
            collected_chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(len(collected_chunks), 2)
        self.assertEqual(collected_chunks[0], chunk1)
        self.assertEqual(collected_chunks[1], chunk2)
        url_refresher.assert_awaited_once()

        # Fallback command still used the cached URL
        fallback_cmd = popen.call_args_list[2].args[0]
        self.assertIn(
            "https://manifest.googlevideo.com/cached_stale.m3u8",
            fallback_cmd,
        )
        self.assertEqual(
            manager.direct_url,
            "https://manifest.googlevideo.com/cached_stale.m3u8",
        )

    async def test_refresh_empty_result_retains_cached_url_and_continues_fallback(
        self,
    ) -> None:
        """When URL refresher returns None or whitespace, fallback proceeds using the existing cached direct URL."""
        chunk1 = b"\x01\x02" * 80000
        chunk2 = b"\x03\x04" * 80000

        sl_proc = MagicMock()
        sl_proc.poll.return_value = None
        sl_proc.wait.return_value = 0
        sl_proc.stderr.readline.return_value = b""

        ff_proc1 = MagicMock()
        ff_proc1.poll.return_value = None
        ff_proc1.wait.return_value = 0
        ff_proc1.stderr.readline.return_value = b""
        ff_proc1.stdout.read.side_effect = [chunk1, b""]

        ff_proc2 = MagicMock()
        ff_proc2.poll.return_value = None
        ff_proc2.wait.return_value = 0
        ff_proc2.stderr.readline.return_value = b""
        ff_proc2.stdout.read.side_effect = [chunk2, b""]

        url_refresher = AsyncMock(return_value="   ")

        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=sample123",
            direct_url="https://manifest.googlevideo.com/cached_stale.m3u8",
            chunk_duration_seconds=5.0,
            url_refresher=url_refresher,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc1, ff_proc2],
        ) as popen:
            collected_chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(len(collected_chunks), 2)
        url_refresher.assert_awaited_once()

        fallback_cmd = popen.call_args_list[2].args[0]
        self.assertIn(
            "https://manifest.googlevideo.com/cached_stale.m3u8",
            fallback_cmd,
        )
        self.assertEqual(
            manager.direct_url,
            "https://manifest.googlevideo.com/cached_stale.m3u8",
        )

    async def test_startup_streamlink_failure_uses_initial_direct_url_without_refresh(
        self,
    ) -> None:
        """Streamlink failure during initial startup uses initial direct_url without calling url_refresher."""
        direct_process = MagicMock()
        direct_process.stdout.read.return_value = b""
        direct_process.stderr.readline.return_value = b""
        direct_process.poll.return_value = None
        direct_process.wait.return_value = 0

        url_refresher = AsyncMock(
            return_value="https://manifest.googlevideo.com/unexpected_refresh.m3u8"
        )

        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=sample123",
            direct_url="https://manifest.googlevideo.com/initial_fresh.m3u8",
            url_refresher=url_refresher,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[OSError("streamlink unavailable"), direct_process],
        ) as popen:
            chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(chunks, [])
        url_refresher.assert_not_called()
        self.assertEqual(popen.call_count, 2)
        fallback_command = popen.call_args_list[1].args[0]
        self.assertIn(
            "https://manifest.googlevideo.com/initial_fresh.m3u8",
            fallback_command,
        )
        self.assertNotIn("unexpected_refresh", fallback_command)
        self.assertEqual(
            manager.direct_url,
            "https://manifest.googlevideo.com/initial_fresh.m3u8",
        )

    def test_diagnostic_sanitization_removes_query_params_and_secrets(self) -> None:
        """Sanitization must strip query strings and mask secrets and tokens."""
        register_secret_for_redaction("my_top_secret_groq_api_key_12345")

        raw_stderr = (
            "Opening 'https://manifest.googlevideo.com/api/manifest/hls_live/live.m3u8?expire=1724500000&signature=abc123xyz456&key=secret' for reading\n"
            "Auth token: 123456789:abcdefghijklmnopqrstuvwxyz012345678\n"
            "Config key: my_top_secret_groq_api_key_12345\n"
            "Generic query: ?key=value&foo=bar"
        )
        sanitized = sanitize_diagnostic(raw_stderr)

        self.assertNotIn("expire=1724500000", sanitized)
        self.assertNotIn("signature=abc123xyz456", sanitized)
        self.assertIn(
            "https://manifest.googlevideo.com/api/manifest/hls_live/live.m3u8?[redacted]",
            sanitized,
        )
        self.assertNotIn("123456789:abcdefghijklmnopqrstuvwxyz012345678", sanitized)
        self.assertNotIn("my_top_secret_groq_api_key_12345", sanitized)
        self.assertIn("?[redacted]", sanitized)

    def test_bounded_diagnostic_tail_retention(self) -> None:
        """Tail formatting returns only bounded last N lines and handles empty/mock inputs."""
        tail = collections.deque(maxlen=100)
        for i in range(20):
            tail.append(f"stderr line {i} from https://example.com/hls.m3u8?token={i}")

        result = format_diagnostic_tail(tail, max_lines=3)
        lines = result.split("\n")
        self.assertEqual(len(lines), 3)
        self.assertIn("stderr line 19", lines[-1])
        self.assertIn("https://example.com/hls.m3u8?[redacted]", lines[-1])
        self.assertNotIn("token=19", lines[-1])

        empty_tail = collections.deque(maxlen=100)
        self.assertEqual(
            format_diagnostic_tail(empty_tail), "<no stderr output>"
        )

    def test_stderr_drain_bounds_individual_line_size(self) -> None:
        """A provider-controlled line cannot bypass the bounded tail by being huge."""
        manager = AudioStreamManager("https://media.example/live.m3u8")
        tail: collections.deque[str] = collections.deque(maxlen=100)
        pipe = io.BytesIO(("x" * 10000 + "\n").encode())

        manager._drain_stderr(pipe, tail)

        self.assertEqual(len(tail), 1)
        self.assertEqual(len(tail[0]), 4096)

    async def test_deterministic_process_and_thread_cleanup_on_stop(self) -> None:
        """Calling stop() joins threads, terminates processes, and closes pipes without leaking."""
        proc1 = MagicMock()
        proc1.poll.return_value = None
        proc1.wait.return_value = 0
        proc1.stdout = io.BytesIO(b"")
        proc1.stderr = io.BytesIO(b"")
        proc1.stdin = io.BytesIO(b"")

        proc2 = MagicMock()
        proc2.poll.return_value = None
        proc2.wait.return_value = 0
        proc2.stdout = io.BytesIO(b"")
        proc2.stderr = io.BytesIO(b"")

        manager = AudioStreamManager("https://media.example/live.m3u8")
        manager._streamlink_process = proc1
        manager._process = proc2

        # Create dummy stderr thread
        def dummy_worker():
            pass

        t = threading.Thread(target=dummy_worker)
        t.start()
        manager._stderr_threads.append(t)

        await manager.stop()

        self.assertIsNone(manager._process)
        self.assertIsNone(manager._streamlink_process)
        self.assertEqual(len(manager._stderr_threads), 0)
        self.assertFalse(t.is_alive())
        proc1.terminate.assert_called_once()
        proc2.terminate.assert_called_once()

    async def test_deterministic_cleanup_on_cancellation(self) -> None:
        """Cancelling the stream_audio_chunks task terminates processes cleanly."""
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stderr.readline.return_value = b""
        # Keep stdout.read blocking until stop event
        stop_reading = threading.Event()

        def slow_read(_size: int) -> bytes:
            stop_reading.wait(timeout=2.0)
            return b""

        proc.stdout.read.side_effect = slow_read

        manager = AudioStreamManager(
            "https://media.example/live.m3u8",
            chunk_duration_seconds=5.0,
            use_streamlink=False,
        )

        async def run_stream():
            with patch("src.audio_stream.subprocess.Popen", return_value=proc):
                async for _ in manager.stream_audio_chunks():
                    pass

        task = asyncio.create_task(run_stream())
        await asyncio.sleep(0.05)
        task.cancel()
        stop_reading.set()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertIsNone(manager._process)
        proc.terminate.assert_called_once()

    async def test_reader_start_failure_cleans_up_started_process(self) -> None:
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stdout = io.BytesIO(b"")
        proc.stderr = io.BytesIO(b"")

        manager = AudioStreamManager(
            "https://media.example/live.m3u8",
            use_streamlink=False,
        )

        with (
            patch("src.audio_stream.subprocess.Popen", return_value=proc),
            patch.object(
                manager,
                "_start_reader_thread",
                side_effect=RuntimeError("thread start failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            async for _ in manager.stream_audio_chunks():
                pass

        self.assertIsNone(manager._process)
        proc.terminate.assert_called_once()

    async def test_enqueue_with_backpressure_blocks_and_resumes_when_capacity_freed(
        self,
    ) -> None:
        """_enqueue_with_backpressure blocks cross-thread until queue space is available."""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2)
        await queue.put(b"chunk1")
        await queue.put(b"chunk2")
        self.assertTrue(queue.full())

        loop = asyncio.get_running_loop()
        stop_event = threading.Event()
        enqueue_result: list[bool] = []

        def producer_worker() -> None:
            res = AudioStreamManager._enqueue_with_backpressure(
                queue, b"chunk3", loop, stop_event, poll_timeout_seconds=0.01
            )
            enqueue_result.append(res)

        t = threading.Thread(target=producer_worker)
        t.start()

        # Verify thread is blocked waiting for queue capacity
        await asyncio.sleep(0.05)
        self.assertTrue(t.is_alive())
        self.assertEqual(len(enqueue_result), 0)

        # Free space in queue
        first = await queue.get()
        self.assertEqual(first, b"chunk1")

        # Now thread unblocks and successfully enqueues
        await asyncio.to_thread(t.join, 1.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(enqueue_result, [True])

        second = await queue.get()
        third = await queue.get()
        self.assertEqual(second, b"chunk2")
        self.assertEqual(third, b"chunk3")

    async def test_enqueue_with_backpressure_stop_event_cancels_and_unblocks_cleanly(
        self,
    ) -> None:
        """Setting stop_event cancels the pending put future and unblocks the thread without leaking."""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1)
        await queue.put(b"chunk1")
        self.assertTrue(queue.full())

        loop = asyncio.get_running_loop()
        stop_event = threading.Event()
        enqueue_result: list[bool] = []

        def producer_worker() -> None:
            res = AudioStreamManager._enqueue_with_backpressure(
                queue, b"chunk2", loop, stop_event, poll_timeout_seconds=0.01
            )
            enqueue_result.append(res)

        t = threading.Thread(target=producer_worker)
        t.start()

        await asyncio.sleep(0.05)
        self.assertTrue(t.is_alive())

        # Signal stop
        stop_event.set()

        await asyncio.to_thread(t.join, 1.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(enqueue_result, [False])
        # Queue still contains only original item
        self.assertEqual(queue.qsize(), 1)
        self.assertEqual(await queue.get(), b"chunk1")

    async def test_enqueue_with_backpressure_closed_loop_or_none_queue_fails_closed(
        self,
    ) -> None:
        """_enqueue_with_backpressure safely returns False for None queue or stopped event."""
        loop = asyncio.get_running_loop()
        stop_event = threading.Event()
        stop_event.set()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1)

        # Pre-set stop_event
        self.assertFalse(
            AudioStreamManager._enqueue_with_backpressure(
                queue, b"chunk", loop, stop_event
            )
        )

        # None queue
        stop_event.clear()
        self.assertFalse(
            AudioStreamManager._enqueue_with_backpressure(
                None, b"chunk", loop, stop_event
            )
        )

    async def test_enqueue_with_backpressure_handles_coroutine_put_exception(
        self,
    ) -> None:
        """_enqueue_with_backpressure safely handles an exception raised during queue.put."""
        loop = asyncio.get_running_loop()
        stop_event = threading.Event()
        mock_queue = MagicMock()
        mock_queue.put = AsyncMock(side_effect=RuntimeError("queue corrupted"))

        res = await asyncio.to_thread(
            AudioStreamManager._enqueue_with_backpressure,
            mock_queue,
            b"chunk",
            loop,
            stop_event,
            0.01,
        )
        self.assertFalse(res)

    def test_enqueue_with_backpressure_when_loop_is_closed(self) -> None:
        """_enqueue_with_backpressure returns False immediately when the event loop is closed."""
        loop = asyncio.new_event_loop()
        loop.close()
        stop_event = threading.Event()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1)

        res = AudioStreamManager._enqueue_with_backpressure(
            queue, b"chunk", loop, stop_event
        )
        self.assertFalse(res)

    async def test_natural_eof_delivers_final_partial_chunk_before_sentinel(
        self,
    ) -> None:
        """Natural EOF delivers any trailing partial PCM chunk before ending the stream."""
        chunk1 = b"\x01" * 160
        chunk_partial = b"\x02" * 50

        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stderr.readline.return_value = b""
        proc.stdout.read.side_effect = [chunk1, chunk_partial, b""]

        manager = AudioStreamManager(
            "https://media.example/live.m3u8",
            chunk_duration_seconds=0.005,  # 160 bytes per full chunk
            use_streamlink=False,
        )

        with patch("src.audio_stream.subprocess.Popen", return_value=proc):
            collected = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(len(collected), 2)
        self.assertEqual(collected[0], chunk1)
        self.assertEqual(collected[1], chunk_partial)
        self.assertEqual(len(collected[1]), 50)

    async def test_natural_eof_under_queue_backpressure_delivers_partial_chunk_then_sentinel(
        self,
    ) -> None:
        """Under queue backpressure, natural EOF still delivers full and partial chunks before ending."""
        chunk1 = b"\x01" * 160
        chunk2 = b"\x02" * 160
        chunk3 = b"\x03" * 160
        chunk_partial = b"\x04" * 75

        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stderr.readline.return_value = b""
        proc.stdout.read.side_effect = [chunk1, chunk2, chunk3, chunk_partial, b""]

        manager = AudioStreamManager(
            "https://media.example/live.m3u8",
            chunk_duration_seconds=0.005,
            use_streamlink=False,
        )

        collected = []
        with patch("src.audio_stream.subprocess.Popen", return_value=proc):
            async for chunk in manager.stream_audio_chunks():
                await asyncio.sleep(0.005)
                collected.append(chunk)

        self.assertEqual(len(collected), 4)
        self.assertEqual(collected[0], chunk1)
        self.assertEqual(collected[1], chunk2)
        self.assertEqual(collected[2], chunk3)
        self.assertEqual(collected[3], chunk_partial)

    async def test_stream_audio_chunks_lossless_delivery_under_slow_consumer(
        self,
    ) -> None:
        """End-to-end stream delivery with 25 chunks against maxsize=20 queue delivers all 25 chunks in order."""
        chunks_data = [bytes([i % 256]) * 160 for i in range(1, 26)]

        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stderr.readline.return_value = b""
        proc.stdout.read.side_effect = [*chunks_data, b""]

        manager = AudioStreamManager(
            "https://media.example/live.m3u8",
            chunk_duration_seconds=0.005,
            use_streamlink=False,
        )

        collected = []
        with patch("src.audio_stream.subprocess.Popen", return_value=proc):
            async for chunk in manager.stream_audio_chunks():
                await asyncio.sleep(0.002)
                collected.append(chunk)

        self.assertEqual(len(collected), 25)
        self.assertEqual(collected, chunks_data)

    async def test_shutdown_during_backpressure_unblocks_reader_and_cleans_up(
        self,
    ) -> None:
        """Calling manager.stop() while reader is blocked on backpressure terminates reader without leak."""
        def infinite_chunks(_size: int) -> bytes:
            return b"\xaa" * 160

        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stderr.readline.return_value = b""
        proc.stdout.read.side_effect = infinite_chunks

        manager = AudioStreamManager(
            "https://media.example/live.m3u8",
            chunk_duration_seconds=0.005,
            use_streamlink=False,
        )

        stop_event = asyncio.Event()
        collected = []
        with patch("src.audio_stream.subprocess.Popen", return_value=proc):
            async for chunk in manager.stream_audio_chunks(stop_event=stop_event):
                collected.append(chunk)
                if len(collected) == 3:
                    stop_event.set()

        self.assertEqual(len(collected), 3)
        self.assertIsNone(manager._process)
        self.assertIsNone(manager._thread)
        proc.terminate.assert_called_once()

    async def test_cancellation_during_backpressure_unblocks_reader_and_cleans_up(
        self,
    ) -> None:
        """Cancelling the consumer task while reader is blocked on backpressure releases thread cleanly."""
        def infinite_chunks(_size: int) -> bytes:
            return b"\xbb" * 160

        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stderr.readline.return_value = b""
        proc.stdout.read.side_effect = infinite_chunks

        manager = AudioStreamManager(
            "https://media.example/live.m3u8",
            chunk_duration_seconds=0.005,
            use_streamlink=False,
        )

        async def consumer() -> None:
            async for _ in manager.stream_audio_chunks():
                pass

        with patch("src.audio_stream.subprocess.Popen", return_value=proc):
            task = asyncio.create_task(consumer())
            await asyncio.sleep(0.05)
            task.cancel()

            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertIsNone(manager._process)
        self.assertIsNone(manager._thread)
        proc.terminate.assert_called_once()

    async def test_direct_fallback_unblocks_blocked_streamlink_reader(
        self,
    ) -> None:
        """Streamlink reader blocked on full queue is cleanly released when fallback triggers."""
        chunk1 = b"\x01\x02" * 80000
        chunk2 = b"\x03\x04" * 80000

        sl_proc = MagicMock()
        sl_proc.poll.return_value = 1  # Streamlink exits
        sl_proc.wait.return_value = 1
        sl_proc.stderr.readline.return_value = b""

        ff_proc1 = MagicMock()
        ff_proc1.poll.return_value = None
        ff_proc1.wait.return_value = 0
        ff_proc1.stderr.readline.return_value = b""
        ff_proc1.stdout.read.side_effect = [chunk1, b""]

        ff_proc2 = MagicMock()
        ff_proc2.poll.return_value = None
        ff_proc2.wait.return_value = 0
        ff_proc2.stderr.readline.return_value = b""
        ff_proc2.stdout.read.side_effect = [chunk2, b""]

        manager = AudioStreamManager(
            stream_url="https://media.example/live.m3u8",
            chunk_duration_seconds=5.0,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc1, ff_proc2],
        ) as popen:
            collected = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(len(collected), 2)
        self.assertEqual(collected[0], chunk1)
        self.assertEqual(collected[1], chunk2)
        self.assertEqual(popen.call_count, 3)


class TestRestartBackoffTracker(unittest.IsolatedAsyncioTestCase):
    """Deterministic tests for configurable exponential restart backoff."""

    def test_exponential_growth_and_max_cap(self) -> None:
        tracker = RestartBackoffTracker(
            initial_delay_seconds=1.0,
            max_delay_seconds=8.0,
            backoff_factor=2.0,
            reset_window_seconds=60.0,
            jitter_ratio=0.0,
        )

        # 1st attempt: delay 1.0, next becomes 2.0
        self.assertEqual(tracker.compute_next_delay(), 1.0)
        # 2nd attempt: delay 2.0, next becomes 4.0
        self.assertEqual(tracker.compute_next_delay(), 2.0)
        # 3rd attempt: delay 4.0, next becomes 8.0
        self.assertEqual(tracker.compute_next_delay(), 4.0)
        # 4th attempt: delay 8.0, next becomes min(8.0, 16.0) = 8.0
        self.assertEqual(tracker.compute_next_delay(), 8.0)
        # 5th attempt: stays capped at max 8.0
        self.assertEqual(tracker.compute_next_delay(), 8.0)

    def test_sustained_success_resets_backoff(self) -> None:
        tracker = RestartBackoffTracker(
            initial_delay_seconds=1.0,
            max_delay_seconds=30.0,
            backoff_factor=2.0,
            reset_window_seconds=300.0,
        )

        # Advance backoff across failures
        tracker.compute_next_delay()  # 1.0
        tracker.compute_next_delay()  # 2.0
        tracker.compute_next_delay()  # 4.0
        tracker.record_session(duration_seconds=10.0, status="INTERRUPTED")
        self.assertEqual(tracker.current_delay_seconds, 8.0)

        # A short two-minute session still observed in the failed live must not reset.
        tracker.record_session(duration_seconds=120.0, status="INTERRUPTED")
        self.assertEqual(tracker.current_delay_seconds, 8.0)

        # A sustained healthy session (duration >= 300.0s) resets backoff.
        tracker.record_session(duration_seconds=305.0, status="INTERRUPTED")
        self.assertEqual(tracker.current_delay_seconds, 1.0)

    def test_completed_session_resets_backoff(self) -> None:
        tracker = RestartBackoffTracker(
            initial_delay_seconds=1.0,
            max_delay_seconds=30.0,
            backoff_factor=2.0,
        )
        tracker.compute_next_delay()  # 1.0
        tracker.compute_next_delay()  # 2.0
        self.assertEqual(tracker.current_delay_seconds, 4.0)

        # Normal completion (stream went offline) resets backoff
        tracker.record_session(duration_seconds=5.0, status="COMPLETED")
        self.assertEqual(tracker.current_delay_seconds, 1.0)

    def test_injectable_jitter(self) -> None:
        tracker = RestartBackoffTracker(
            initial_delay_seconds=2.0,
            jitter_ratio=0.0,
        )
        # Custom jitter injector adding 0.5s
        custom_jitter = lambda d: d + 0.5
        delay = tracker.compute_next_delay(jitter_fn=custom_jitter)
        self.assertEqual(delay, 2.5)

    async def test_wait_interruptible_by_stop_event(self) -> None:
        tracker = RestartBackoffTracker(initial_delay_seconds=10.0)
        stop_event = asyncio.Event()

        async def trigger_stop():
            await asyncio.sleep(0.02)
            stop_event.set()

        asyncio.create_task(trigger_stop())
        start_time = asyncio.get_running_loop().time()
        await tracker.wait(stop_event=stop_event)
        elapsed = asyncio.get_running_loop().time() - start_time

        # Must exit almost immediately upon stop_event (< 0.5s), not 10s
        self.assertLess(elapsed, 0.5)


if __name__ == "__main__":
    unittest.main()
