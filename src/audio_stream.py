"""Asynchronous real-time audio chunk capture from live streams via FFmpeg."""

import asyncio
import collections
import concurrent.futures
import logging
import re
import subprocess
import sys
import threading
from collections.abc import AsyncGenerator, Callable
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from src.logger import _redactor_filter

logger = logging.getLogger("trade_bot.audio_stream")

_MAX_DIAGNOSTIC_LINE_CHARS = 4096

_RATE_LIMIT_PATTERN = re.compile(
    r"(?:too\s+many\s+requests|"
    r"\b429\b\s+(?:client\s+error|http\s+error|too\s+many\s+requests)|"
    r"(?:http|server\s+returned)[^\r\n]{0,32}\b429\b)",
    re.IGNORECASE,
)


_YOUTUBE_HOSTNAMES = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "gaming.youtube.com",
        "youtu.be",
    }
)

_STREAMLINK_NATIVE_HOSTNAMES = frozenset(
    {
        "twitch.tv",
        "www.twitch.tv",
        "m.twitch.tv",
    }
)


def is_youtube_url(url: str) -> bool:
    """Classify whether a URL is a public YouTube watch, live, or channel URL using parsed hostnames."""
    if not url or not isinstance(url, str):
        return False
    cleaned = url.strip().removeprefix("hls://")
    try:
        parsed = urlparse(cleaned)
        if (parsed.scheme or "").lower() not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        return (
            hostname in _YOUTUBE_HOSTNAMES
            or hostname.endswith((".youtube.com", ".youtu.be"))
        )
    except (ValueError, TypeError):
        return False


def _is_streamlink_native_url(url: str) -> bool:
    """Classify whether a URL has a native Streamlink plugin using parsed hostnames."""
    if is_youtube_url(url):
        return True
    cleaned = url.strip().removeprefix("hls://")
    try:
        parsed = urlparse(cleaned)
        if (parsed.scheme or "").lower() not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        return (
            hostname in _STREAMLINK_NATIVE_HOSTNAMES
            or hostname.endswith(".twitch.tv")
        )
    except (ValueError, TypeError):
        return False


class CaptureFailureReason(str, Enum):
    """Structured failure classification for audio stream capture."""

    NONE = "NONE"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    PROCESS_EXIT = "PROCESS_EXIT"
    TIMEOUT = "TIMEOUT"
    EOF = "EOF"
    UNKNOWN = "UNKNOWN"

    def __bool__(self) -> bool:
        return self != CaptureFailureReason.NONE


def is_rate_limit_diagnostic(text: str) -> bool:
    """Classify whether sanitized diagnostic text contains an HTTP 429 / Too Many Requests response."""
    if not text or not isinstance(text, str):
        return False
    return bool(_RATE_LIMIT_PATTERN.search(text))


def sanitize_diagnostic(text: str) -> str:
    """Sanitize URLs, query strings, and secrets from diagnostic lines."""
    if not text or not isinstance(text, str):
        return str(text) if text is not None else ""

    # Strip query parameters from URLs: e.g. https://...?... -> https://...?[redacted]
    def _sanitize_url(match: re.Match[str]) -> str:
        full_url = match.group(0)
        try:
            parsed = urlparse(full_url)
            if parsed.query:
                return full_url.split("?", 1)[0] + "?[redacted]"
        except (ValueError, TypeError):
            pass
        return full_url

    url_pattern = re.compile(r"https?://[^\s\"'>]+", re.IGNORECASE)
    sanitized = url_pattern.sub(_sanitize_url, text)

    # Mask standalone query strings like ?expire=...&sig=...
    query_pattern = re.compile(r"(\?[a-zA-Z0-9_=&%.-]{8,})")
    sanitized = query_pattern.sub("?[redacted]", sanitized)

    # Mask registered secrets and token patterns
    sanitized = _redactor_filter.mask_text(sanitized)
    return sanitized


def format_diagnostic_tail(tail: collections.deque[str], max_lines: int = 5) -> str:
    """Return a concise, sanitized diagnostic string from the stderr tail."""
    lines = list(tail)[-max_lines:]
    sanitized_lines = [sanitize_diagnostic(l) for l in lines if l.strip()]
    return "\n".join(sanitized_lines) if sanitized_lines else "<no stderr output>"


class RestartBackoffTracker:
    """Exponential backoff tracker with sustained-success reset window and injectable jitter."""

    def __init__(
        self,
        initial_delay_seconds: float = 1.0,
        max_delay_seconds: float = 30.0,
        backoff_factor: float = 2.0,
        reset_window_seconds: float = 300.0,
        jitter_ratio: float = 0.0,
    ) -> None:
        self.initial_delay_seconds = max(0.01, float(initial_delay_seconds))
        self.max_delay_seconds = max(
            self.initial_delay_seconds, float(max_delay_seconds)
        )
        self.backoff_factor = max(1.0, float(backoff_factor))
        self.reset_window_seconds = max(0.0, float(reset_window_seconds))
        self.jitter_ratio = max(0.0, min(1.0, float(jitter_ratio)))
        self.current_delay_seconds = self.initial_delay_seconds
        self._consecutive_failures = 0

    def record_session(self, duration_seconds: float, status: str) -> None:
        """Update backoff state based on session outcome and duration."""
        if status == "COMPLETED" or duration_seconds >= self.reset_window_seconds:
            self.reset()
        elif status == "INTERRUPTED":
            self._consecutive_failures += 1

    def compute_next_delay(
        self, jitter_fn: Callable[[float], float] | None = None
    ) -> float:
        """Compute the next delay, advance exponential state, and apply optional jitter."""
        delay = self.current_delay_seconds
        self.current_delay_seconds = min(
            self.max_delay_seconds, self.current_delay_seconds * self.backoff_factor
        )
        if jitter_fn is not None:
            return jitter_fn(delay)
        if self.jitter_ratio > 0.0:
            import random

            delta = delay * self.jitter_ratio
            return max(0.01, delay + random.uniform(-delta, delta))
        return delay

    def reset(self) -> None:
        """Reset backoff delay to initial value."""
        self.current_delay_seconds = self.initial_delay_seconds
        self._consecutive_failures = 0

    async def wait_cooldown(
        self,
        min_cooldown_seconds: float = 0.0,
        stop_event: asyncio.Event | None = None,
        jitter_fn: Callable[[float], float] | None = None,
    ) -> float:
        """Wait for the larger of computed backoff delay and min_cooldown_seconds, or until stop_event is set.

        Returns the actual delay waited.
        """
        delay = max(
            self.compute_next_delay(jitter_fn=jitter_fn), float(min_cooldown_seconds)
        )
        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
        else:
            await asyncio.sleep(delay)
        return delay

    async def wait(
        self,
        stop_event: asyncio.Event | None = None,
        jitter_fn: Callable[[float], float] | None = None,
    ) -> float:
        """Wait for the computed backoff delay or until stop_event is set.

        Returns the actual delay waited.
        """
        return await self.wait_cooldown(
            min_cooldown_seconds=0.0,
            stop_event=stop_event,
            jitter_fn=jitter_fn,
        )


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
        direct_url: str | None = None,
    ) -> None:
        self.stream_url = stream_url.strip()
        self.direct_url = direct_url.strip() if direct_url else None
        self.chunk_duration_seconds = chunk_duration_seconds
        self.sample_rate = sample_rate
        self.channels = channels
        self.max_consecutive_timeouts = max(1, int(max_consecutive_timeouts))
        self.use_streamlink = use_streamlink
        # 16-bit PCM = 2 bytes per sample
        self.sample_width = 2
        self.bytes_per_second = self.sample_rate * self.channels * self.sample_width
        self.chunk_size_bytes = int(self.bytes_per_second * self.chunk_duration_seconds)
        self._process: subprocess.Popen | None = None
        self._streamlink_process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._reader_stop_event: threading.Event = threading.Event()
        self._is_running: bool = False
        self._queue: asyncio.Queue[bytes | None] | None = None
        self._fallback_attempted: bool = False
        self._active_transport: str = "none"
        self._pcm_bytes_delivered: int = 0
        self._failure_reason: CaptureFailureReason = CaptureFailureReason.NONE
        self._ffmpeg_stderr_tail: collections.deque[str] = collections.deque(maxlen=100)
        self._streamlink_stderr_tail: collections.deque[str] = collections.deque(
            maxlen=100
        )
        self._stderr_threads: list[threading.Thread] = []

    @property
    def failure_reason(self) -> CaptureFailureReason:
        """Typed/structured failure classification for the audio stream session."""
        return self._failure_reason

    @property
    def pcm_bytes_delivered(self) -> int:
        """Exact PCM byte length delivered across all yielded chunks in current session."""
        return self._pcm_bytes_delivered

    @property
    def audio_seconds_delivered(self) -> float:
        """Exact decoded audio duration in seconds delivered to consumer in current session."""
        if self.bytes_per_second <= 0:
            return 0.0
        return self._pcm_bytes_delivered / self.bytes_per_second

    @property
    def is_youtube(self) -> bool:
        """Return True if the configured stream URL is a public YouTube URL."""
        return is_youtube_url(self.stream_url)

    def _check_rate_limit(self) -> bool:
        """Inspect sanitized stderr tails for HTTP 429 / Too Many Requests and classify failure."""
        for tail in (self._streamlink_stderr_tail, self._ffmpeg_stderr_tail):
            for line in tail:
                sanitized = sanitize_diagnostic(line)
                if is_rate_limit_diagnostic(sanitized):
                    self._failure_reason = CaptureFailureReason.PROVIDER_RATE_LIMIT
                    return True
        return False

    def _build_streamlink_command(self) -> list[str]:
        """Build a resilient transport command that writes media to stdout."""
        target = self.stream_url.strip()
        if is_youtube_url(target):
            # Normalize legacy callers while preserving YouTube plugin ownership.
            target = target.removeprefix("hls://")
        if not _is_streamlink_native_url(target) and not target.startswith("hls://"):
            target = f"hls://{target}"

        return [
            sys.executable,
            "-m",
            "streamlink",
            "--stdout",
            "--loglevel",
            "warning",
            "--retry-streams",
            "15",
            "--retry-max",
            "3",
            "--retry-open",
            "3",
            "--ringbuffer-size",
            "64M",
            "--stream-segment-attempts",
            "5",
            "--stream-segment-threads",
            "1",
            "--stream-segment-timeout",
            "20",
            "--hls-live-edge",
            "3",
            "--hls-playlist-reload-attempts",
            "10",
            "--hls-playlist-reload-time",
            "segment",
            "--stream-segmented-queue-deadline",
            "0",
            "--stream-timeout",
            "120",
            "--http-timeout",
            "30.0",
            target,
            "audio_only,worst",
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
            target_url = self.direct_url or self.stream_url
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
                    target_url,
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

    def _drain_stderr(self, pipe: Any, tail: collections.deque[str]) -> None:
        try:
            while True:
                if not pipe or getattr(pipe, "closed", True):
                    break
                try:
                    raw_line = pipe.readline()
                except (OSError, ValueError):
                    break
                if not raw_line or not isinstance(raw_line, (bytes, bytearray)):
                    break
                decoded = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if decoded:
                    tail.append(decoded[-_MAX_DIAGNOSTIC_LINE_CHARS:])
        except (OSError, ValueError):
            pass
        finally:
            if pipe and not getattr(pipe, "closed", True):
                try:
                    pipe.close()
                except OSError:
                    pass

    def _start_direct_ffmpeg(self) -> None:
        """Start direct FFmpeg capture process with stderr draining."""
        self._active_transport = "direct"
        self._process = subprocess.Popen(
            self._build_ffmpeg_command(input_from_stdin=False),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=self.chunk_size_bytes,
        )
        if self._process.stderr:
            t_err = threading.Thread(
                target=self._drain_stderr,
                args=(self._process.stderr, self._ffmpeg_stderr_tail),
                daemon=True,
            )
            t_err.start()
            self._stderr_threads.append(t_err)
        logger.info("Started direct FFmpeg audio capture.")

    def _start_capture_processes(self) -> None:
        """Start Streamlink -> FFmpeg, falling back to direct FFmpeg on startup errors."""
        if self.use_streamlink:
            try:
                self._streamlink_process = subprocess.Popen(
                    self._build_streamlink_command(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                )
                if self._streamlink_process.stdout is None:
                    raise OSError("Streamlink stdout pipe was not created")

                if self._streamlink_process.stderr:
                    t_sl_err = threading.Thread(
                        target=self._drain_stderr,
                        args=(
                            self._streamlink_process.stderr,
                            self._streamlink_stderr_tail,
                        ),
                        daemon=True,
                    )
                    t_sl_err.start()
                    self._stderr_threads.append(t_sl_err)

                self._process = subprocess.Popen(
                    self._build_ffmpeg_command(input_from_stdin=True),
                    stdin=self._streamlink_process.stdout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=self.chunk_size_bytes,
                )
                if self._process.stderr:
                    t_ff_err = threading.Thread(
                        target=self._drain_stderr,
                        args=(self._process.stderr, self._ffmpeg_stderr_tail),
                        daemon=True,
                    )
                    t_ff_err.start()
                    self._stderr_threads.append(t_ff_err)

                # FFmpeg owns the duplicated read handle after Popen returns.
                self._streamlink_process.stdout.close()
                self._active_transport = "streamlink"
                logger.info("Started Streamlink-buffered HLS audio capture.")
                return
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning(
                    "Streamlink capture could not start (%s); %s.",
                    exc,
                    "failing back to supervised restart loop"
                    if self.is_youtube
                    else "using direct FFmpeg",
                )
                diag = format_diagnostic_tail(self._streamlink_stderr_tail)
                if diag != "<no stderr output>":
                    logger.warning("Streamlink startup diagnostic:\n%s", diag)
                self._stop_active_processes_sync()
                self._fallback_attempted = True
                if self._check_rate_limit():
                    logger.warning(
                        "Streamlink startup encountered provider rate limit (HTTP 429)."
                    )
                    return
                if self.is_youtube:
                    if self._failure_reason == CaptureFailureReason.NONE:
                        self._failure_reason = CaptureFailureReason.PROCESS_EXIT
                    return

        if self.is_youtube:
            logger.warning(
                "Direct FFmpeg capture is disabled for YouTube inputs; failing back to supervised restart loop."
            )
            if self._failure_reason == CaptureFailureReason.NONE:
                self._failure_reason = CaptureFailureReason.PROCESS_EXIT
            return

        self._start_direct_ffmpeg()

    @staticmethod
    def _enqueue_with_backpressure(
        queue: Any,
        item: bytes | None,
        loop: asyncio.AbstractEventLoop,
        stop_event: threading.Event,
        poll_timeout_seconds: float = 0.05,
    ) -> bool:
        """Bounded, shutdown-aware cross-thread enqueue that waits for queue capacity."""
        if stop_event.is_set() or queue is None or loop.is_closed():
            return False

        async def _put_coro() -> None:
            res = queue.put(item)
            if asyncio.iscoroutine(res):
                await res

        try:
            future = asyncio.run_coroutine_threadsafe(_put_coro(), loop)
        except RuntimeError:
            return False

        while not stop_event.is_set():
            try:
                future.result(timeout=poll_timeout_seconds)
                return True
            except concurrent.futures.TimeoutError:
                continue
            except concurrent.futures.CancelledError:
                return False
            except Exception as exc:  # noqa: BLE001 - queue/event-loop boundary
                logger.debug("Audio queue enqueue failed: %s", exc)
                return False

        future.cancel()
        try:
            future.result(timeout=poll_timeout_seconds)
        except concurrent.futures.CancelledError:
            pass
        except concurrent.futures.TimeoutError:
            logger.debug("Timed out waiting for cancelled audio queue enqueue.")
        except Exception as exc:  # noqa: BLE001 - queue/event-loop boundary
            logger.debug("Cancelled audio queue enqueue ended with error: %s", exc)
        return False

    def _start_reader_thread(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start background stdout reader thread."""
        self._reader_stop_event.clear()
        process = self._process
        queue = self._queue
        stop_event = self._reader_stop_event
        chunk_size = self.chunk_size_bytes

        def _reader() -> None:
            try:
                while not stop_event.is_set() and process and process.stdout:
                    raw = b""
                    while len(raw) < chunk_size and not stop_event.is_set():
                        try:
                            part = process.stdout.read(chunk_size - len(raw))
                        except (OSError, ValueError):
                            part = None
                        if not part:
                            break
                        raw += part

                    if raw and not stop_event.is_set() and queue is not None:
                        if not self._enqueue_with_backpressure(
                            queue, raw, loop, stop_event
                        ):
                            break
                    elif not raw:
                        break
            except Exception as e:  # noqa: BLE001 - reader thread error boundary
                logger.debug("Reader thread exited: %s", e)
            finally:
                if not stop_event.is_set() and queue is not None:
                    self._enqueue_with_backpressure(queue, None, loop, stop_event)

        self._thread = threading.Thread(target=_reader, daemon=True)
        self._thread.start()

    def _stop_active_processes_sync(self) -> None:
        """Synchronously terminate active processes, close pipes, and join stderr threads."""
        self._reader_stop_event.set()

        for process, name in (
            (self._process, "FFmpeg"),
            (self._streamlink_process, "Streamlink"),
        ):
            if process is None:
                continue
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2.0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error stopping %s process: %s", name, exc)
            finally:
                for pipe in (
                    getattr(process, "stdout", None),
                    getattr(process, "stderr", None),
                    getattr(process, "stdin", None),
                ):
                    if pipe and not getattr(pipe, "closed", True):
                        try:
                            pipe.close()
                        except OSError:
                            pass

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                logger.warning(
                    "Audio reader thread did not stop within cleanup timeout."
                )
        self._thread = None

        for t in list(self._stderr_threads):
            if t.is_alive():
                t.join(timeout=1.0)
        self._stderr_threads.clear()
        self._process = None
        self._streamlink_process = None

    async def _attempt_direct_fallback(self, loop: asyncio.AbstractEventLoop) -> bool:
        """Stop Streamlink pipeline and attempt direct FFmpeg capture exactly once for non-YouTube inputs."""
        if self._fallback_attempted or self.is_youtube:
            return False
        self._fallback_attempted = True

        if self._check_rate_limit():
            logger.warning(
                "Streamlink encountered provider rate limit (HTTP 429); skipping direct fallback."
            )
            return False

        logger.warning(
            "Stopping Streamlink pipeline and attempting direct FFmpeg fallback..."
        )

        # Stop active processes and reader thread
        await asyncio.to_thread(self._stop_active_processes_sync)

        # Reset queue for direct FFmpeg
        self._queue = asyncio.Queue(maxsize=20)

        try:
            self._start_direct_ffmpeg()
            self._start_reader_thread(loop)
            logger.info("Direct FFmpeg fallback audio capture active.")
            return True
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            logger.error("Direct FFmpeg fallback failed to start (%s).", exc)
            diag = format_diagnostic_tail(self._ffmpeg_stderr_tail)
            if diag != "<no stderr output>":
                logger.error("FFmpeg fallback diagnostic:\n%s", diag)
            await asyncio.to_thread(self._stop_active_processes_sync)
            self._check_rate_limit()
            return False

    async def stream_audio_chunks(
        self, stop_event: asyncio.Event | None = None
    ) -> AsyncGenerator[bytes, None]:
        """Continuously read and yield raw PCM audio chunks of chunk_duration_seconds."""
        self._is_running = True
        self._fallback_attempted = False
        self._failure_reason = CaptureFailureReason.NONE
        self._pcm_bytes_delivered = 0
        loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=20)

        logger.info(
            "Starting audio capture (chunk size: %ss / %s bytes)...",
            self.chunk_duration_seconds,
            self.chunk_size_bytes,
        )
        try:
            self._start_capture_processes()
            if self._failure_reason == CaptureFailureReason.PROVIDER_RATE_LIMIT:
                return
            if self._process is None:
                return

            self._start_reader_thread(loop)
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
                        # EOF received
                        if self._check_rate_limit():
                            break

                        if (
                            self._active_transport == "streamlink"
                            and not self._fallback_attempted
                        ):
                            sl_code = (
                                self._streamlink_process.poll()
                                if self._streamlink_process
                                else None
                            )
                            diag = format_diagnostic_tail(self._streamlink_stderr_tail)
                            if diag != "<no stderr output>":
                                logger.warning("Streamlink diagnostic:\n%s", diag)

                            if self.is_youtube:
                                logger.warning(
                                    "Streamlink audio capture ended for YouTube (EOF/exit code %s); "
                                    "failing back to supervised restart loop without direct FFmpeg.",
                                    sl_code,
                                )
                                if not self._check_rate_limit():
                                    self._failure_reason = (
                                        CaptureFailureReason.EOF
                                        if sl_code in (0, None)
                                        else CaptureFailureReason.PROCESS_EXIT
                                    )
                                break

                            logger.warning(
                                "Streamlink audio capture ended (EOF/exit code %s); attempting direct FFmpeg fallback.",
                                sl_code,
                            )
                            fallback_ok = await self._attempt_direct_fallback(loop)
                            if fallback_ok:
                                consecutive_timeouts = 0
                                continue
                            else:
                                if self._check_rate_limit():
                                    pass
                                elif self._failure_reason == CaptureFailureReason.NONE:
                                    self._failure_reason = (
                                        CaptureFailureReason.PROCESS_EXIT
                                    )
                                break
                        else:
                            logger.warning("Audio capture stream reached EOF.")
                            if self._process and self._process.poll() is not None:
                                diag = format_diagnostic_tail(self._ffmpeg_stderr_tail)
                                if diag != "<no stderr output>":
                                    logger.warning("FFmpeg exit diagnostic:\n%s", diag)
                            if not self._check_rate_limit():
                                self._failure_reason = CaptureFailureReason.EOF
                            break

                    consecutive_timeouts = 0
                    self._pcm_bytes_delivered += len(chunk)
                    yield chunk

                except asyncio.TimeoutError:
                    consecutive_timeouts += 1
                    logger.warning(
                        "Timeout waiting for audio chunk from reader thread (%s/%s).",
                        consecutive_timeouts,
                        self.max_consecutive_timeouts,
                    )

                    sl_exited = (
                        self._streamlink_process is not None
                        and self._streamlink_process.poll() is not None
                    )
                    ff_exited = (
                        self._process is not None and self._process.poll() is not None
                    )
                    stalled = consecutive_timeouts >= self.max_consecutive_timeouts

                    if sl_exited or ff_exited or stalled:
                        if self._check_rate_limit():
                            break

                        if (
                            self._active_transport == "streamlink"
                            and not self._fallback_attempted
                        ):
                            reason = (
                                f"process exited (Streamlink: {self._streamlink_process.returncode if self._streamlink_process else None})"
                                if sl_exited
                                else ("FFmpeg exited" if ff_exited else "stalled")
                            )
                            diag = format_diagnostic_tail(self._streamlink_stderr_tail)
                            if diag != "<no stderr output>":
                                logger.warning("Streamlink diagnostic:\n%s", diag)

                            if self.is_youtube:
                                logger.warning(
                                    "Streamlink capture %s for YouTube; "
                                    "failing back to supervised restart loop without direct FFmpeg.",
                                    reason,
                                )
                                if not self._check_rate_limit():
                                    if sl_exited or ff_exited:
                                        self._failure_reason = (
                                            CaptureFailureReason.PROCESS_EXIT
                                        )
                                    else:
                                        self._failure_reason = (
                                            CaptureFailureReason.TIMEOUT
                                        )
                                break

                            logger.warning(
                                "Streamlink capture %s; attempting direct FFmpeg fallback.",
                                reason,
                            )
                            fallback_ok = await self._attempt_direct_fallback(loop)
                            if fallback_ok:
                                consecutive_timeouts = 0
                                continue
                            else:
                                if self._check_rate_limit():
                                    pass
                                elif self._failure_reason == CaptureFailureReason.NONE:
                                    self._failure_reason = (
                                        CaptureFailureReason.PROCESS_EXIT
                                    )
                                break
                        else:
                            if ff_exited:
                                logger.error(
                                    "FFmpeg process terminated with code %s",
                                    self._process.returncode if self._process else None,
                                )
                                diag = format_diagnostic_tail(self._ffmpeg_stderr_tail)
                                if diag != "<no stderr output>":
                                    logger.error("FFmpeg diagnostic:\n%s", diag)
                                if not self._check_rate_limit():
                                    self._failure_reason = (
                                        CaptureFailureReason.PROCESS_EXIT
                                    )
                            elif stalled:
                                logger.error(
                                    "Audio capture stalled while FFmpeg remained alive; restarting with a fresh stream URL."
                                )
                                if not self._check_rate_limit():
                                    self._failure_reason = CaptureFailureReason.TIMEOUT
                            break
                    continue
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Gracefully terminate FFmpeg and its optional Streamlink transport."""
        self._is_running = False
        await asyncio.to_thread(self._stop_active_processes_sync)
        # Stderr drain threads are joined during process cleanup, so inspect their
        # final lines only after cleanup has completed.
        self._check_rate_limit()
