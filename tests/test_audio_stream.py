"""Offline tests for FFmpeg audio-stream restart behavior."""

import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.audio_stream import AudioStreamManager


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

    def test_ffmpeg_reads_streamlink_output_from_stdin(self) -> None:
        manager = AudioStreamManager("https://media.example/live.m3u8")
        command = manager._build_ffmpeg_command(input_from_stdin=True)

        self.assertIn("pipe:0", command)
        self.assertNotIn("-reconnect", command)
        self.assertEqual(command[-1], "pipe:1")

    async def test_two_consecutive_timeouts_restart_audio_capture(self) -> None:
        process = MagicMock()
        process.stdout.read.return_value = b""
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
        ffmpeg_process = MagicMock()
        ffmpeg_process.stdout.read.return_value = b""
        ffmpeg_process.poll.return_value = None
        ffmpeg_process.wait.return_value = 0
        manager = AudioStreamManager("https://media.example/live.m3u8")

        with patch(
            "src.audio_stream.subprocess.Popen",
            side_effect=[streamlink_process, ffmpeg_process],
        ) as popen:
            chunks = [chunk async for chunk in manager.stream_audio_chunks()]

        self.assertEqual(chunks, [])
        self.assertEqual(popen.call_count, 2)
        self.assertIs(
            popen.call_args_list[1].kwargs["stdin"], streamlink_process.stdout
        )
        streamlink_process.stdout.close.assert_called_once()
        ffmpeg_process.terminate.assert_called_once()
        streamlink_process.terminate.assert_called_once()

    async def test_streamlink_start_error_falls_back_to_direct_ffmpeg(self) -> None:
        direct_process = MagicMock()
        direct_process.stdout.read.return_value = b""
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


if __name__ == "__main__":
    unittest.main()
