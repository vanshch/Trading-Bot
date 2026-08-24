"""Rolling transcript buffer and keyword-based audio trigger detection."""

import logging
import re
import time
from dataclasses import dataclass

logger = logging.getLogger("trade_bot.audio_trigger")

DEFAULT_FALLBACK_KEYWORDS: list[str] = [
    # English execution triggers (Whisper /translations always outputs English)
    "active",
    "activated",
    "entry",
    "entered",
    "bought",
    "buy",
    "sold",
    "sell",
    "short",
    "long",
    "position",
    "executed",
    "order placed",
]


@dataclass
class TranscriptEntry:
    """Individual timestamped transcript chunk."""

    timestamp: float
    text: str


class RollingTranscriptBuffer:
    """Maintains a rolling in-memory window of recent spoken transcript chunks."""

    def __init__(self, max_duration_seconds: int = 180) -> None:
        self.max_duration_seconds = max_duration_seconds
        self._buffer: list[TranscriptEntry] = []

    def append(self, text: str) -> None:
        """Append a newly transcribed text chunk and prune outdated entries."""
        cleaned = text.strip()
        if not cleaned:
            return

        now = time.time()
        self._buffer.append(TranscriptEntry(timestamp=now, text=cleaned))
        self._prune(now)

    def _prune(self, current_time: float) -> None:
        """Remove entries older than max_duration_seconds."""
        cutoff = current_time - self.max_duration_seconds
        self._buffer = [entry for entry in self._buffer if entry.timestamp >= cutoff]

    def get_context_text(self) -> str:
        """Return the concatenated context of all retained chunks in chronological order."""
        self._prune(time.time())
        return " ".join(entry.text for entry in self._buffer).strip()

    def get_formatted_context(self) -> str:
        """Return formatted transcript with relative elapsed seconds."""
        self._prune(time.time())
        if not self._buffer:
            return ""

        now = time.time()
        lines = []
        for entry in self._buffer:
            elapsed_ago = int(now - entry.timestamp)
            lines.append(f"[-{elapsed_ago}s]: {entry.text}")
        return "\n".join(lines)

    def clear(self) -> None:
        """Clear all entries in the buffer."""
        self._buffer.clear()

    @property
    def entry_count(self) -> int:
        """Number of active transcript segments in buffer."""
        return len(self._buffer)


class AudioTriggerDetector:
    """Scans incoming transcript chunks for trade entry trigger keywords/phrases."""

    def __init__(
        self,
        trigger_keywords: list[str] | None = None,
        cooldown_seconds: float = 15.0,
    ) -> None:
        keywords = trigger_keywords if trigger_keywords else DEFAULT_FALLBACK_KEYWORDS
        self.keywords = [k.strip() for k in keywords if k.strip()]
        self.cooldown_seconds = cooldown_seconds
        self._last_trigger_time: float = 0.0
        self._compiled_patterns: list[tuple[str, re.Pattern]] = []
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Compile keyword patterns for fast matching."""
        self._compiled_patterns.clear()
        for kw in self.keywords:
            # Escape keyword and allow flexible whitespace between words
            escaped = re.escape(kw).replace(r"\ ", r"\s+")
            pattern = re.compile(rf"\b{escaped}\b", re.IGNORECASE)
            self._compiled_patterns.append((kw, pattern))

    def update_keywords(self, new_keywords: list[str]) -> None:
        """Update active trigger keyword list."""
        self.keywords = [k.strip() for k in new_keywords if k.strip()]
        self._compile_patterns()

    def check_trigger(self, chunk_text: str) -> tuple[bool, str | None]:
        """Check if any trigger keyword is spoken in the given chunk text.

        Returns:
            tuple[bool, str | None]: (is_triggered, matched_keyword)
        """
        if not chunk_text.strip():
            return False, None

        now = time.time()
        if now - self._last_trigger_time < self.cooldown_seconds:
            # Still in cooldown window from previous trigger
            return False, None

        for kw, pattern in self._compiled_patterns:
            if pattern.search(chunk_text):
                self._last_trigger_time = now
                logger.info(f"Audio trigger detected: '{kw}' in chunk: '{chunk_text}'")
                return True, kw

        return False, None

    def reset_cooldown(self) -> None:
        """Reset the trigger cooldown timer."""
        self._last_trigger_time = 0.0
