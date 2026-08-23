"""Asynchronous real-time audio chunk capture from live streams via FFmpeg."""

import asyncio
import logging
import subprocess
import sys
import threading
from collections.abc import AsyncGenerator

logger = logging.getLogger("trade_bot.audio_stream")


class AudioStreamManager:
    """Extracts raw 16kHz mono PCM audio chunks in real-time from a live stream URL."""

    def __init__(
        self,
        stream_url: str,
        chunk_duration_seconds: float = 5.0,
        sample_rate: int = 16000,
        channels: int = 1,
        max_consecutive_timeouts: int = 2,
        use_streamlink: bool = True,
    ) -> None:
        self.stream_url = stream_url.strip()
        self.chunk_duration_seconds = chunk_duration_seconds
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_consecutive_timeouts = max(1, int(max_consecutive_timeouts))
        self.use_streamlink = use_streamlink
        # 16-bit PCM = 2 bytes per sample
        self.bytes_per_second = self.sample_rate * self.channels * 2
        self.chunk_size_bytes = int(self.bytes_per_second * self.chunk_duration_seconds)
        self._process: subprocess.Popen | None = None
        self._streamlink_process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._is_running: bool = False
        self._queue: asyncio.Queue[bytes | None] | None = None

    def _build_streamlink_command(self) -> list[str]:
        """Build a resilient transport command that writes media to stdout."""
        target = self.stream_url
        if not (
            "youtube.com" in target
            or "youtu.be" in target
            or "twitch.tv" in target
        ) and not target.startswith("hls://"):
            target = f"hls://{target}"

        return [
            sys.executable,
            "-m",
            "streamlink",
            "--stdout",
            "--loglevel",
            "warning",
            "--ringbuffer-size",
            "64M",
            "--stream-segment-attempts",
            "5",
            "--stream-segment-threads",
            "3",
            "--stream-segment-timeout",
            "15",
            "--hls-playlist-reload-attempts",
            "5",
            "--stream-timeout",
            "60",
            target,
            "best",
        ]

    def _build_ffmpeg_command(self, *, input_from_stdin: bool = False) -> list[str]:
        """Build the PCM decoder command for Streamlink or direct URL input."""
        command = [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "warning",
        ]
        if input_from_stdin:
            command.extend(["-i", "pipe:0"])
        else:
            command.extend(
                [
                    "-user_agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
                    "-reconnect",
                    "1",
                    "-reconnect_streamed",
                    "1",
                    "-reconnect_delay_max",
                    "5",
                    "-rw_timeout",
                    "15000000",
                    "-flush_packets",
                    "1",
                    "-i",
                    self.stream_url,
                ]
            )
        command.extend(
            [
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                str(self.sample_rate),
                "-ac",
                str(self.channels),
                "-f",
                "s16le",
                "pipe:1",
            ]
        )
        return command

    def _start_capture_processes(self) -> None:
        """Start Streamlink -> FFmpeg, falling back to direct FFmpeg on startup errors."""
        if self.use_streamlink:
            try:
                self._streamlink_process = subprocess.Popen(
                    self._build_streamlink_command(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                )
                if self._streamlink_process.stdout is None:
                    raise OSError("Streamlink stdout pipe was not created")

                self._process = subprocess.Popen(
                    self._build_ffmpeg_command(input_from_stdin=True),
                    stdin=self._streamlink_process.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=self.chunk_size_bytes,
                )
                # FFmpeg owns the duplicated read handle after Popen returns.
                self._streamlink_process.stdout.close()
                logger.info("Started Streamlink-buffered HLS audio capture.")
                return
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning(
                    "Streamlink capture could not start (%s); using direct FFmpeg.", exc
                )
                if self._streamlink_process is not None:
                    try:
                        if self._streamlink_process.poll() is None:
                            self._streamlink_process.terminate()
                    except OSError:
                        pass
                    self._streamlink_process = None

        self._process = subprocess.Popen(
            self._build_ffmpeg_command(input_from_stdin=False),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=self.chunk_size_bytes,
        )
        logger.info("Started direct FFmpeg audio capture.")

    async def stream_audio_chunks(
        self, stop_event: asyncio.Event | None = None
    ) -> AsyncGenerator[bytes, None]:
        """Continuously read and yield raw PCM audio chunks of chunk_duration_seconds."""
        self._is_running = True
        loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=20)

        logger.info(
            "Starting audio capture (chunk size: %ss / %s bytes)...",
            self.chunk_duration_seconds,
            self.chunk_size_bytes,
        )
        self._start_capture_processes()

        def _reader_thread():
            try:
                while self._is_running and self._process and self._process.stdout:
                    raw = b""
                    while len(raw) < self.chunk_size_bytes and self._is_running:
                        part = self._process.stdout.read(
                            self.chunk_size_bytes - len(raw)
                        )
                        if not part:
                            break
                        raw += part

                    if raw and self._is_running and self._queue is not None:
                        loop.call_soon_threadsafe(self._queue.put_nowait, raw)
                    elif not raw:
                        break
            except Exception as e:  # noqa: BLE001 - reader thread error boundary
                logger.debug(f"Reader thread exited: {e}")
            finally:
                if self._queue is not None:
                    loop.call_soon_threadsafe(self._queue.put_nowait, None)

        self._thread = threading.Thread(target=_reader_thread, daemon=True)
        self._thread.start()

        try:
            consecutive_timeouts = 0
            while self._is_running:
                if stop_event and stop_event.is_set():
                    break

                try:
                    chunk = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=self.chunk_duration_seconds + 8.0,
                    )
                    if chunk is None:
                        logger.warning("Audio capture stream reached EOF.")
                        if (
                            self._streamlink_process
                            and self._streamlink_process.poll() is not None
                        ):
                            logger.warning(
                                "Streamlink exited with code %s.",
                                self._streamlink_process.returncode,
                            )
                        break
                    consecutive_timeouts = 0
                    yield chunk
                except asyncio.TimeoutError:
                    consecutive_timeouts += 1
                    logger.warning(
                        "Timeout waiting for audio chunk from reader thread (%s/%s).",
                        consecutive_timeouts,
                        self.max_consecutive_timeouts,
                    )
                    if self._process and self._process.poll() is not None:
                        logger.error(
                            f"FFmpeg process terminated with code {self._process.returncode}"
                        )
                        break
                    if (
                        self._streamlink_process
                        and self._streamlink_process.poll() is not None
                    ):
                        logger.error(
                            "Streamlink process terminated with code %s",
                            self._streamlink_process.returncode,
                        )
                        break
                    if consecutive_timeouts >= self.max_consecutive_timeouts:
                        logger.error(
                            "Audio capture stalled while FFmpeg remained alive; restarting with a fresh stream URL."
                        )
                        break
                    continue
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Gracefully terminate FFmpeg and its optional Streamlink transport."""
        self._is_running = False
        for process, name in (
            (self._process, "FFmpeg"),
            (self._streamlink_process, "Streamlink"),
        ):
            if process is None:
                continue
            try:
                if process.poll() is None:
                    process.terminate()
                    await asyncio.to_thread(process.wait, timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
            except Exception as exc:  # noqa: BLE001 - subprocess termination boundary
                logger.debug("Error closing %s process: %s", name, exc)
        self._process = None
        self._streamlink_process = None
