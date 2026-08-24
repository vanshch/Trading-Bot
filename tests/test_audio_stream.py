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
        self.assertIn("--ringbuffer-size", command)
        self.assertIn("--stream-segment-attempts", command)
        self.assertIn("--stream-segment-threads", command)
        self.assertIn("--hls-playlist-reload-attempts", command)
        self.assertIn("hls://https://media.example/live.m3u8", command)
        self.assertEqual(command[-1], "best")

    def test_streamlink_command_passes_youtube_url_directly(self) -> None:
        manager = AudioStreamManager("https://www.youtube.com/watch?v=sample123")
        command = manager._build_streamlink_command()

        self.assertEqual(command[:3], [sys.executable, "-m", "streamlink"])
        self.assertIn("https://www.youtube.com/watch?v=sample123", command)
        self.assertNotIn("hls://https://www.youtube.com/watch?v=sample123", command)
        self.assertEqual(command[-1], "best")

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
