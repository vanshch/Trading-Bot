"""YouTube live stream status detection and frame extraction via yt-dlp and OpenCV."""

import asyncio
import logging
from enum import Enum

import cv2
import numpy as np
import yt_dlp

from src.config import CaptureConfig

logger = logging.getLogger("trade_bot.stream_manager")


class StreamStatus(str, Enum):
    """Explicit stream state resolved from YouTube / yt-dlp."""

    LIVE = "LIVE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"

    def __bool__(self) -> bool:
        return self == StreamStatus.LIVE


class _YtDlpLogger:
    """Route yt-dlp output through application logging without direct stderr spam."""

    def debug(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None


def _extract_bitrate(val: object) -> float:
    """Safely extract a float bitrate value from numeric or string metadata."""
    if isinstance(val, (int, float)) and val > 0:
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.strip().rstrip("kKmM"))
        except ValueError:
            return 0.0
    return 0.0


def _candidate_rank(fmt: dict, idx: int) -> tuple[int, float, float, float, int]:
    """Calculate the priority rank tuple for a stream format candidate.

    Higher tier and bitrates rank higher. Returns tier -1 for rejected / video-only formats.
    Rank tuple: (tier, audio_bitrate, audio_sample_rate, total_bitrate, format_index)
    """
    url = fmt.get("url")
    if not url or not isinstance(url, str) or not url.strip():
        return (-1, 0.0, 0.0, 0.0, -1)

    acodec = (
        str(fmt.get("acodec") or "").lower() if fmt.get("acodec") is not None else None
    )
    if acodec == "none":
        return (-1, 0.0, 0.0, 0.0, -1)

    format_note = str(fmt.get("format_note") or "").lower()
    if "video only" in format_note:
        return (-1, 0.0, 0.0, 0.0, -1)

    vcodec = (
        str(fmt.get("vcodec") or "").lower() if fmt.get("vcodec") is not None else None
    )
    ext = str(fmt.get("ext") or "").lower()
    protocol = str(fmt.get("protocol") or "").lower()
    format_id = str(fmt.get("format_id") or "").lower()
    resolution = str(fmt.get("resolution") or "").lower()
    url_lower = url.lower()

    is_hls = (
        protocol.startswith(("m3u8", "http_m3u8"))
        or ext == "m3u8"
        or ".m3u8" in url_lower
        or "m3u8" in protocol
        or "hls" in format_note
        or "hls" in format_id
        or "/hls_live/" in url_lower
        or "/manifest/hls_" in url_lower
    )

    is_audio_only = (
        vcodec == "none"
        or resolution == "audio only"
        or "audio only" in format_note
        or (
            ext in ("m4a", "aac", "opus", "mp3", "flac", "ogg", "wav")
            and (vcodec is None or vcodec == "none")
        )
    )

    # One resolved URL currently feeds both FFmpeg audio capture and OpenCV frames.
    # Prefer muxed HLS so reliable STT does not silently remove chart verification.
    if is_hls and not is_audio_only:
        tier = 4
    elif is_hls:
        tier = 3
    elif is_audio_only:
        tier = 2
    else:
        tier = 1

    abr = _extract_bitrate(fmt.get("abr"))
    tbr = _extract_bitrate(fmt.get("tbr"))
    asr = _extract_bitrate(fmt.get("asr"))
    audio_bitrate = abr if abr > 0.0 else (tbr if is_audio_only and tbr > 0.0 else 0.0)

    return (tier, audio_bitrate, asr, tbr, idx)


def select_best_audio_stream_url(info: dict | None) -> str | None:
    """Select the most reliable audio-capable stream URL from yt-dlp metadata.

    Prefers continuous muxed HLS/m3u8 so one URL supports both transcription and
    chart frames, falls back to HLS audio-only, rejects video-only candidates,
    and provides a safe ranked fallback.
    """
    if not isinstance(info, dict):
        return None

    raw_candidates: list[dict] = []
    formats = info.get("formats")
    if isinstance(formats, list):
        for fmt in formats:
            if isinstance(fmt, dict):
                raw_candidates.append(fmt)

    # Top-level info can serve as a candidate if it provides a direct stream URL
    top_url = info.get("url")
    if (
        isinstance(top_url, str)
        and top_url.strip()
        and not any(f.get("url") == top_url for f in raw_candidates)
    ):
        raw_candidates.append(info)

    if not raw_candidates:
        return None

    scored_candidates: list[tuple[tuple[int, float, float, float, int], str]] = []
    for idx, fmt in enumerate(raw_candidates):
        rank = _candidate_rank(fmt, idx)
        if rank[0] > 0:
            url = fmt.get("url")
            if isinstance(url, str) and url.strip():
                scored_candidates.append((rank, url.strip()))

    if not scored_candidates:
        return None

    best_candidate = max(scored_candidates, key=lambda item: item[0])
    return best_candidate[1]


class StreamManager:
    """Manages stream resolution and real-time frame extraction for a YouTube channel."""

    def __init__(self, channel_url: str, config: CaptureConfig | None = None) -> None:
        self.channel_url = channel_url.strip()
        self.config = config or CaptureConfig()
        self._current_stream_url: str | None = None
        self._broadcast_id: str | None = None
        self._is_live: bool = False
        self._status: StreamStatus = StreamStatus.OFFLINE
        self._consecutive_errors: int = 0
        self._cap: cv2.VideoCapture | None = None

    @property
    def status(self) -> StreamStatus:
        """Return latest resolved stream status."""
        return self._status

    @property
    def is_live(self) -> bool:
        """Return cached live status."""
        return self._is_live

    @property
    def current_stream_url(self) -> str | None:
        """Return the resolved direct HLS/video stream URL."""
        return self._current_stream_url

    @property
    def broadcast_id(self) -> str | None:
        """Return the latest confirmed broadcast/video ID from yt-dlp metadata."""
        return self._broadcast_id

    def _resolve_stream_url_sync(self) -> tuple[StreamStatus, str | None, str | None]:
        """Synchronously check live status and extract direct stream URL using yt-dlp."""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "js_runtimes": {"node": {}},
            "format": (
                "bestaudio[protocol^=m3u8]/bestaudio/best[protocol^=m3u8]/"
                "best[acodec!=none]/best"
            ),
            "noplaylist": True,
            "socket_timeout": self.config.stream_timeout_seconds,
            "logger": _YtDlpLogger(),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.channel_url, download=False)
                if info is None:
                    return StreamStatus.UNKNOWN, None, None

                # Check if live
                is_live = info.get("is_live", False)
                live_status = info.get("live_status", "")

                if not is_live and live_status != "is_live":
                    return StreamStatus.OFFLINE, None, None

                stream_url = select_best_audio_stream_url(info)
                raw_id = info.get("id") or info.get("video_id")
                broadcast_id = (
                    str(raw_id).strip()
                    if raw_id and isinstance(raw_id, (str, int))
                    else None
                )

                if stream_url:
                    return StreamStatus.LIVE, stream_url, broadcast_id
                return StreamStatus.UNKNOWN, None, None

        except yt_dlp.utils.DownloadError as exc:
            err_str = str(exc).lower()
            if (
                "offline" in err_str
                or "not live" in err_str
                or "not currently live" in err_str
                or "private video" in err_str
                or "this live event has ended" in err_str
                or "has ended" in err_str
                or "premieres in" in err_str
                or "is upcoming" in err_str
            ):
                logger.debug(f"Channel {self.channel_url} is offline: {exc}")
                return StreamStatus.OFFLINE, None, None
            else:
                logger.warning(f"DownloadError resolving {self.channel_url}: {exc}")
                return StreamStatus.UNKNOWN, None, None
        except Exception as exc:  # noqa: BLE001 - provider boundary must fail closed
            logger.warning(
                f"Unexpected error resolving stream for {self.channel_url}: {exc}"
            )
            return StreamStatus.UNKNOWN, None, None

    async def check_live_status(self) -> tuple[StreamStatus, str | None]:
        """Asynchronously check if the stream is currently active and resolve stream URL."""
        status, stream_url, broadcast_id = await asyncio.to_thread(
            self._resolve_stream_url_sync
        )
        self._status = status

        if status == StreamStatus.LIVE:
            self._is_live = True
            self._current_stream_url = stream_url
            self._broadcast_id = broadcast_id
            self._consecutive_errors = 0
        elif status == StreamStatus.OFFLINE:
            self._is_live = False
            self._current_stream_url = None
            self._broadcast_id = None
            self._close_capture()
        elif status == StreamStatus.UNKNOWN:
            # Preserve the prior known live state and broadcast_id across transient/unknown results
            pass

        return status, stream_url

    def _open_capture_sync(self, stream_url: str) -> cv2.VideoCapture | None:
        """Open cv2.VideoCapture with reasonable buffer size."""
        try:
            cap = cv2.VideoCapture(stream_url)
            # Minimize latency buffer
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                cap.release()
                return None
            return cap
        except cv2.error as exc:
            logger.warning(
                f"Failed to open video capture for {self.channel_url}: {exc}"
            )
            return None

    def _grab_frame_sync(self) -> np.ndarray | None:
        """Synchronously grab a single frame from the live stream."""
        if not self._current_stream_url:
            return None

        if self._cap is None or not self._cap.isOpened():
            self._cap = self._open_capture_sync(self._current_stream_url)
            if self._cap is None:
                self._consecutive_errors += 1
                return None

        # Flush buffer to ensure latest live frame
        for _ in range(2):
            self._cap.grab()

        ret, frame = self._cap.read()
        if not ret or frame is None:
            self._consecutive_errors += 1
            if self._consecutive_errors >= self.config.max_consecutive_stream_errors:
                logger.warning(
                    f"Exceeded max consecutive frame read errors ({self._consecutive_errors}) for {self.channel_url}. Returning to live-status polling."
                )
                self._close_capture()
                self._is_live = False
                self._current_stream_url = None
                self._consecutive_errors = 0
            return None

        self._consecutive_errors = 0
        return frame

    async def capture_frame(self) -> np.ndarray | None:
        """Asynchronously capture a fresh frame from the active stream."""
        if not self._is_live or not self._current_stream_url:
            return None

        return await asyncio.to_thread(self._grab_frame_sync)

    def _close_capture(self) -> None:
        """Release underlying OpenCV VideoCapture instance."""
        if self._cap is not None:
            try:
                self._cap.release()
            except cv2.error as exc:
                logger.debug("Ignoring capture release failure: %s", type(exc).__name__)
            self._cap = None

    async def close(self) -> None:
        """Clean up stream resources and release capture device."""
        await asyncio.to_thread(self._close_capture)
        self._is_live = False
        self._status = StreamStatus.OFFLINE
        self._current_stream_url = None
        self._broadcast_id = None
