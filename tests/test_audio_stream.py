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
    CaptureFailureReason,
    RestartBackoffTracker,
    format_diagnostic_tail,
    is_rate_limit_diagnostic,
    is_youtube_url,
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
            "--hls-live-edge": "3",
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
            (
                "https://www.youtube.com/channel/UC12345",
                "https://www.youtube.com/channel/UC12345",
                False,
            ),
            ("https://twitch.tv/liveuser", "https://twitch.tv/liveuser", False),
            ("https://www.twitch.tv/liveuser", "https://www.twitch.tv/liveuser", False),
            (
                "hls://https://custom.stream/live.m3u8",
                "hls://https://custom.stream/live.m3u8",
                False,
            ),
            (
                "https://custom.stream/live.m3u8",
                "hls://https://custom.stream/live.m3u8",
                True,
            ),
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
            stream_url="https://media.example/live.m3u8",
            direct_url="https://media.example/fallback.m3u8",
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
            "https://media.example/fallback.m3u8",
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

    async def test_runtime_eof_uses_initial_direct_url_without_resolver(
        self,
    ) -> None:
        """Streamlink EOF falls back to direct FFmpeg using the initial cached direct URL without resolver."""
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

        manager = AudioStreamManager(
            stream_url="https://media.example/live.m3u8",
            direct_url="https://media.example/initial_cached.m3u8",
            chunk_duration_seconds=5.0,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc1, ff_proc2],
        ) as popen:
            collected_chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(len(collected_chunks), 2)
        self.assertEqual(collected_chunks[0], chunk1)
        self.assertEqual(collected_chunks[1], chunk2)

        # Fallback FFmpeg used the cached direct URL directly without calling any resolver
        fallback_cmd = popen.call_args_list[2].args[0]
        self.assertIn(
            "https://media.example/initial_cached.m3u8",
            fallback_cmd,
        )
        self.assertEqual(
            manager.direct_url, "https://media.example/initial_cached.m3u8"
        )

    async def test_runtime_stall_uses_initial_direct_url_without_resolver(
        self,
    ) -> None:
        """Streamlink stall falls back to direct FFmpeg using the initial cached direct URL."""
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

        manager = AudioStreamManager(
            stream_url="https://media.example/live.m3u8",
            direct_url="https://manifest.googlevideo.com/initial_cached.m3u8",
            chunk_duration_seconds=0.01,
            max_consecutive_timeouts=1,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc1, ff_proc2],
        ) as popen:
            chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        read_event.set()
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], chunk2)

        fallback_cmd = popen.call_args_list[2].args[0]
        self.assertIn(
            "https://manifest.googlevideo.com/initial_cached.m3u8",
            fallback_cmd,
        )
        self.assertEqual(
            manager.direct_url, "https://manifest.googlevideo.com/initial_cached.m3u8"
        )

    async def test_startup_streamlink_failure_uses_initial_direct_url(
        self,
    ) -> None:
        """Streamlink failure during initial startup falls back to direct FFmpeg using initial direct URL."""
        direct_process = MagicMock()
        direct_process.stdout.read.return_value = b""
        direct_process.stderr.readline.return_value = b""
        direct_process.poll.return_value = None
        direct_process.wait.return_value = 0

        manager = AudioStreamManager(
            stream_url="https://media.example/live.m3u8",
            direct_url="https://media.example/initial_fresh.m3u8",
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[OSError("streamlink unavailable"), direct_process],
        ) as popen:
            chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(chunks, [])
        self.assertEqual(popen.call_count, 2)
        fallback_command = popen.call_args_list[1].args[0]
        self.assertIn(
            "https://media.example/initial_fresh.m3u8",
            fallback_command,
        )
        self.assertEqual(
            manager.direct_url,
            "https://media.example/initial_fresh.m3u8",
        )

    async def test_streamlink_stderr_429_classifies_provider_rate_limit(
        self,
    ) -> None:
        """Sanitized Streamlink stderr containing HTTP 429 is classified as PROVIDER_RATE_LIMIT and skips fallback."""
        sl_proc = MagicMock()
        sl_proc.poll.return_value = 1
        sl_proc.wait.return_value = 1
        sl_proc.stderr.readline.return_value = b""

        ff_proc = MagicMock()
        ff_proc.poll.return_value = None
        ff_proc.wait.return_value = 0
        ff_proc.stderr.readline.return_value = b""
        ff_proc.stdout.read.return_value = b""

        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=sample123",
            direct_url="https://manifest.googlevideo.com/direct.m3u8",
        )
        # Inject HTTP 429 stderr line into Streamlink tail
        manager._streamlink_stderr_tail.append(
            "error: Unable to open URL: https://youtube.com/watch?v=abc (429 Client Error: Too Many Requests for url: https://...)"
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc],
        ) as popen:
            chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(chunks, [])
        self.assertEqual(
            manager.failure_reason, CaptureFailureReason.PROVIDER_RATE_LIMIT
        )
        # Direct FFmpeg fallback was NOT attempted because 429 aborted immediately
        self.assertEqual(popen.call_count, 2)  # Streamlink + initial piped FFmpeg only

    async def test_ffmpeg_stderr_429_classifies_provider_rate_limit(
        self,
    ) -> None:
        """Sanitized direct FFmpeg stderr containing HTTP 429 is classified as PROVIDER_RATE_LIMIT."""
        ff_proc = MagicMock()
        ff_proc.poll.return_value = 1
        ff_proc.wait.return_value = 1
        ff_proc.stderr.readline.return_value = b""
        ff_proc.stdout.read.return_value = b""

        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=sample123",
            direct_url="https://manifest.googlevideo.com/direct.m3u8",
            use_streamlink=False,
        )
        # Inject 429 into FFmpeg stderr tail
        manager._ffmpeg_stderr_tail.append(
            "[https @ 0x12345] Server returned 429 Client Error: Too Many Requests"
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            return_value=ff_proc,
        ):
            chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(chunks, [])
        self.assertEqual(
            manager.failure_reason, CaptureFailureReason.PROVIDER_RATE_LIMIT
        )

    def test_rate_limit_classifier_requires_http_context(self) -> None:
        self.assertTrue(is_rate_limit_diagnostic("HTTP error 429"))
        self.assertTrue(is_rate_limit_diagnostic("429 Client Error: Too Many Requests"))
        self.assertTrue(is_rate_limit_diagnostic("Too Many Requests"))
        self.assertFalse(
            is_rate_limit_diagnostic("Failed while downloading media segment 429.ts")
        )

    async def test_exact_delivered_pcm_bytes_and_audio_seconds(self) -> None:
        """Delivered PCM byte length and audio seconds are accurately recorded, including partial chunks."""
        # 16kHz, mono, 16-bit = 32000 bytes/sec
        # Chunk 1: full 5s = 160,000 bytes
        chunk1 = b"\x01\x00" * 80000
        # Chunk 2: partial 0.5s = 16,000 bytes
        chunk2 = b"\x02\x00" * 8000

        ff_proc = MagicMock()
        ff_proc.poll.return_value = None
        ff_proc.wait.return_value = 0
        ff_proc.stderr.readline.return_value = b""
        ff_proc.stdout.read.side_effect = [chunk1, chunk2, b""]

        manager = AudioStreamManager(
            stream_url="https://media.example/live.m3u8",
            use_streamlink=False,
            chunk_duration_seconds=5.0,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            return_value=ff_proc,
        ):
            chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(len(chunks), 2)
        expected_bytes = len(chunk1) + len(chunk2)  # 176,000 bytes
        self.assertEqual(manager.pcm_bytes_delivered, expected_bytes)
        expected_seconds = (
            expected_bytes / manager.bytes_per_second
        )  # 176000 / 32000 = 5.5s
        self.assertEqual(manager.audio_seconds_delivered, expected_seconds)
        self.assertEqual(manager.failure_reason, CaptureFailureReason.EOF)

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
        self.assertEqual(format_diagnostic_tail(empty_tail), "<no stderr output>")

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

    async def test_rate_limit_cooldown_uses_minimum_and_is_interruptible(self) -> None:
        tracker = RestartBackoffTracker(initial_delay_seconds=1.0)
        stop_event = asyncio.Event()

        async def trigger_stop() -> None:
            await asyncio.sleep(0.02)
            stop_event.set()

        asyncio.create_task(trigger_stop())
        start_time = asyncio.get_running_loop().time()
        selected_delay = await tracker.wait_cooldown(
            min_cooldown_seconds=120.0,
            stop_event=stop_event,
        )
        elapsed = asyncio.get_running_loop().time() - start_time

        self.assertEqual(selected_delay, 120.0)
        self.assertLess(elapsed, 0.5)


class TestStreamlinkYouTubeOwnership(unittest.IsolatedAsyncioTestCase):
    """Focused regression tests for Streamlink-owned YouTube playback resolution.

    Verifies:
    1. Robust URL classification for public YouTube watch/live/channel URLs using parsed hostnames.
    2. Canonical public YouTube URL passed to Streamlink without hls:// prefix.
    3. Proven 3-segment live edge configured on Streamlink.
    4. FFmpeg stdin decoding free of -re/-readrate throttling.
    5. Positive ownership streaming path: Streamlink -> FFmpeg stdout pipe yields chunks.
    6. Negative no-direct-fallback guarantee on YouTube startup failure.
    7. Negative no-direct-fallback guarantee on YouTube startup rate-limit.
    8. Negative no-direct-fallback guarantee on YouTube runtime exit.
    9. Negative no-direct-fallback guarantee on YouTube runtime EOF.
    10. Negative no-direct-fallback guarantee on YouTube runtime stall.
    11. Negative no-direct-fallback guarantee via _attempt_direct_fallback.
    12. Negative no-direct-fallback guarantee when use_streamlink=False for YouTube.
    """

    def test_is_youtube_url_classification_matrix(self) -> None:
        """Verify robust URL classification using parsed hostnames rather than substring matching."""
        positive_urls = [
            "https://www.youtube.com/watch?v=Nq2wYlWFucg",
            "https://youtube.com/watch?v=Nq2wYlWFucg",
            "https://m.youtube.com/watch?v=Nq2wYlWFucg",
            "https://music.youtube.com/watch?v=Nq2wYlWFucg",
            "https://gaming.youtube.com/watch?v=Nq2wYlWFucg",
            "https://youtu.be/Nq2wYlWFucg",
            "https://www.youtube.com/@tradewithmehaktrades/live",
            "https://www.youtube.com/live/Nq2wYlWFucg",
            "https://www.youtube.com/channel/UC1234567890",
            "https://www.youtube.com/c/MehakTrades",
            "https://www.youtube.com/embed/Nq2wYlWFucg",
            "https://www.youtube.com:443/watch?v=Nq2wYlWFucg",
            "http://www.youtube.com/watch?v=Nq2wYlWFucg",
            "  https://www.youtube.com/watch?v=Nq2wYlWFucg  ",
            "hls://https://www.youtube.com/watch?v=Nq2wYlWFucg",
        ]
        for url in positive_urls:
            with self.subTest(url=url):
                self.assertTrue(
                    is_youtube_url(url),
                    f"Expected {url} to be classified as YouTube URL",
                )

        negative_urls = [
            "https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
            "https://rr2---sn-ab5sznzl.googlevideo.com/videoplayback?expire=123",
            "https://media.example/live.m3u8",
            "https://notyoutube.com/watch?v=Nq2wYlWFucg",
            "https://youtube.com.attacker.example/watch?v=Nq2wYlWFucg",
            "https://fakeyoutube.com/watch?v=Nq2wYlWFucg",
            "https://attacker.example/youtube.com/live",
            "https://attacker.example/search?q=youtube.com",
            "https://twitch.tv/liveuser",
            "https://www.twitch.tv/liveuser",
            "file:///path/to/youtube.com",
            "",
            "   ",
            None,
            12345,
        ]
        for url in negative_urls:
            with self.subTest(url=url):
                self.assertFalse(
                    is_youtube_url(url),
                    f"Expected {url} NOT to be classified as YouTube URL",
                )

    def test_youtube_ownership_command_options_and_no_ffmpeg_throttling(self) -> None:
        """Streamlink receives canonical public YouTube URL at 3-segment live edge; FFmpeg stdin has no -re."""
        watch_url = "https://www.youtube.com/watch?v=Nq2wYlWFucg"
        manager = AudioStreamManager(watch_url)

        self.assertTrue(manager.is_youtube)

        # Streamlink command verification
        sl_cmd = manager._build_streamlink_command()
        self.assertEqual(sl_cmd[:3], [sys.executable, "-m", "streamlink"])
        self.assertIn("--stdout", sl_cmd)
        self.assertIn(watch_url, sl_cmd)
        self.assertNotIn(f"hls://{watch_url}", sl_cmd)

        legacy_prefixed = AudioStreamManager(f"hls://{watch_url}")
        legacy_sl_cmd = legacy_prefixed._build_streamlink_command()
        self.assertIn(watch_url, legacy_sl_cmd)
        self.assertNotIn(f"hls://{watch_url}", legacy_sl_cmd)

        # Proven 3-segment live edge
        self.assertIn("--hls-live-edge", sl_cmd)
        edge_idx = sl_cmd.index("--hls-live-edge")
        self.assertEqual(sl_cmd[edge_idx + 1], "3")

        # Buffering and retry flags
        self.assertIn("--ringbuffer-size", sl_cmd)
        self.assertIn("--retry-open", sl_cmd)
        self.assertIn("--stream-segment-attempts", sl_cmd)
        self.assertEqual(sl_cmd[-1], "audio_only,worst")

        # FFmpeg stdin command: decode only, NO input rate throttling (-re or -readrate)
        ff_cmd = manager._build_ffmpeg_command(input_from_stdin=True)
        self.assertIn("pipe:0", ff_cmd)
        self.assertNotIn("-re", ff_cmd)
        self.assertNotIn("-readrate", ff_cmd)
        self.assertIn("-acodec", ff_cmd)
        self.assertIn("pcm_s16le", ff_cmd)
        self.assertEqual(ff_cmd[-1], "pipe:1")

    async def test_youtube_positive_ownership_streaming_pipeline(self) -> None:
        """Positive path: Streamlink-owned transport decodes audio to stdout without direct FFmpeg fallback."""
        chunk1 = b"\x01\x02" * 80000
        chunk2 = b"\x03\x04" * 80000

        sl_proc = MagicMock()
        sl_proc.poll.return_value = 0
        sl_proc.wait.return_value = 0
        sl_proc.stderr.readline.return_value = b""

        ff_proc = MagicMock()
        ff_proc.poll.return_value = 0
        ff_proc.wait.return_value = 0
        ff_proc.stderr.readline.return_value = b""
        ff_proc.stdout.read.side_effect = [chunk1, chunk2, b""]

        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=Nq2wYlWFucg",
            direct_url="https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
            chunk_duration_seconds=5.0,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc],
        ) as popen:
            collected = [c async for c in manager.stream_audio_chunks()]

        # Both chunks yielded through Streamlink transport
        self.assertEqual(len(collected), 2)
        self.assertEqual(collected[0], chunk1)
        self.assertEqual(collected[1], chunk2)

        # Exactly 2 processes spawned (Streamlink + piped FFmpeg); NO direct FFmpeg fallback
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(manager.pcm_bytes_delivered, len(chunk1) + len(chunk2))
        self.assertEqual(manager.failure_reason, CaptureFailureReason.EOF)

    async def test_youtube_startup_failure_negative_no_direct_fallback(self) -> None:
        """Negative guarantee: YouTube Streamlink startup failure fails back to supervisor without direct FFmpeg."""
        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=Nq2wYlWFucg",
            direct_url="https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=OSError("streamlink launch failure"),
        ) as popen:
            chunks = [c async for c in manager.stream_audio_chunks()]

        self.assertEqual(chunks, [])
        # Exactly ONE call: Streamlink launch failed, direct FFmpeg was NEVER called
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(manager.failure_reason, CaptureFailureReason.PROCESS_EXIT)

    async def test_youtube_startup_rate_limit_negative_no_direct_fallback(self) -> None:
        """Negative guarantee: YouTube Streamlink startup 429 classifies correctly and skips direct FFmpeg."""
        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=Nq2wYlWFucg",
            direct_url="https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
        )
        manager._streamlink_stderr_tail.append(
            "error: Unable to open URL: https://youtube.com/... (429 Client Error: Too Many Requests)"
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=OSError("streamlink launch failure"),
        ) as popen:
            chunks = [c async for c in manager.stream_audio_chunks()]

        self.assertEqual(chunks, [])
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(
            manager.failure_reason, CaptureFailureReason.PROVIDER_RATE_LIMIT
        )

    async def test_youtube_runtime_exit_negative_no_direct_fallback(self) -> None:
        """Negative guarantee: YouTube Streamlink exit mid-capture fails back to supervisor without direct FFmpeg."""
        chunk1 = b"\x01\x02" * 80000

        sl_proc = MagicMock()
        sl_proc.poll.return_value = 1  # exited with error
        sl_proc.wait.return_value = 1
        sl_proc.returncode = 1
        sl_proc.stderr.readline.return_value = b""

        ff_proc = MagicMock()
        ff_proc.poll.return_value = 0
        ff_proc.wait.return_value = 0
        ff_proc.stderr.readline.return_value = b""
        ff_proc.stdout.read.side_effect = [chunk1, b""]

        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=Nq2wYlWFucg",
            direct_url="https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
            chunk_duration_seconds=5.0,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc],
        ) as popen:
            collected = [c async for c in manager.stream_audio_chunks()]

        # Yielded chunk1 before exit, did NOT attempt direct FFmpeg
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0], chunk1)
        # Exactly 2 calls (Streamlink + piped FFmpeg); NO third Popen for direct FFmpeg
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(manager.failure_reason, CaptureFailureReason.PROCESS_EXIT)

    async def test_youtube_runtime_eof_negative_no_direct_fallback(self) -> None:
        """Negative guarantee: YouTube Streamlink EOF fails back to supervisor without direct FFmpeg."""
        chunk1 = b"\x01\x02" * 80000

        sl_proc = MagicMock()
        sl_proc.poll.return_value = 0  # clean EOF
        sl_proc.wait.return_value = 0
        sl_proc.returncode = 0
        sl_proc.stderr.readline.return_value = b""

        ff_proc = MagicMock()
        ff_proc.poll.return_value = 0
        ff_proc.wait.return_value = 0
        ff_proc.stderr.readline.return_value = b""
        ff_proc.stdout.read.side_effect = [chunk1, b""]

        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=Nq2wYlWFucg",
            direct_url="https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
            chunk_duration_seconds=5.0,
        )

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[sl_proc, ff_proc],
        ) as popen:
            collected = [c async for c in manager.stream_audio_chunks()]

        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0], chunk1)
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(manager.failure_reason, CaptureFailureReason.EOF)

    async def test_youtube_runtime_stall_negative_no_direct_fallback(self) -> None:
        """Negative guarantee: YouTube Streamlink stall fails back to supervisor without direct FFmpeg."""
        sl_proc = MagicMock()
        sl_proc.poll.return_value = None  # alive but stalled
        sl_proc.wait.return_value = 0
        sl_proc.stderr.readline.return_value = b""

        ff_proc = MagicMock()
        ff_proc.poll.return_value = None
        ff_proc.wait.return_value = 0
        ff_proc.stderr.readline.return_value = b""

        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=Nq2wYlWFucg",
            direct_url="https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
            chunk_duration_seconds=0.01,
            max_consecutive_timeouts=1,
        )
        queue = MagicMock()
        queue.get = AsyncMock(side_effect=asyncio.TimeoutError)

        with (
            patch(
                "src.audio_stream.subprocess.Popen",
                side_effect=[sl_proc, ff_proc],
            ) as popen,
            patch("src.audio_stream.asyncio.Queue", return_value=queue),
        ):
            chunks = [c async for c in manager.stream_audio_chunks()]

        self.assertEqual(chunks, [])
        # Exactly 2 calls; NO third Popen for direct FFmpeg
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(manager.failure_reason, CaptureFailureReason.TIMEOUT)

    async def test_youtube_attempt_direct_fallback_always_returns_false(self) -> None:
        """Direct fallback method returns False immediately for YouTube inputs without spawning processes."""
        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=Nq2wYlWFucg",
            direct_url="https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
        )

        with patch("src.audio_stream.subprocess.Popen") as popen:
            res = await manager._attempt_direct_fallback(asyncio.get_running_loop())

        self.assertFalse(res)
        self.assertEqual(popen.call_count, 0)

    async def test_youtube_use_streamlink_false_disables_direct_ffmpeg(self) -> None:
        """Negative guarantee: Initializing with use_streamlink=False on a YouTube URL never spawns direct FFmpeg."""
        manager = AudioStreamManager(
            stream_url="https://www.youtube.com/watch?v=Nq2wYlWFucg",
            direct_url="https://manifest.googlevideo.com/api/manifest/hls_live/stream.m3u8",
            use_streamlink=False,
        )

        with patch("src.audio_stream.subprocess.Popen") as popen:
            chunks = [c async for c in manager.stream_audio_chunks()]

        self.assertEqual(chunks, [])
        self.assertEqual(popen.call_count, 0)
        self.assertEqual(manager.failure_reason, CaptureFailureReason.PROCESS_EXIT)


if __name__ == "__main__":
    unittest.main()
