"""Structured contextual console logging with secret sanitization."""

import logging
import re
import sys
import time
from collections.abc import MutableMapping
from typing import Any


class _EncodingSafeStream:
    """Preserve log records on terminals that cannot encode all Unicode."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def write(self, text: str) -> Any:
        encoding = getattr(self._stream, "encoding", None) or "utf-8"
        safe_text = text.encode(encoding, errors="backslashreplace").decode(encoding)
        return self._stream.write(safe_text)

    def flush(self) -> Any:
        return self._stream.flush()


class _UTCFormatter(logging.Formatter):
    converter = time.gmtime


class SecretRedactorFilter(logging.Filter):
    """Filter that masks sensitive tokens and API keys from log messages."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        super().__init__()
        self._secrets: set[str] = {s for s in (secrets or []) if s and len(s) > 4}
        # Regex patterns for Telegram bot token and generic API keys
        self._token_pattern = re.compile(r"\b\d{8,10}:[a-zA-Z0-9_-]{35}\b")
        self._generic_key_pattern = re.compile(
            r"(AIza[0-9A-Za-z-_]{35}|[a-zA-Z0-9_-]{32,64})"
        )

    def add_secret(self, secret: str) -> None:
        if secret and len(secret) > 4:
            self._secrets.add(secret)

    def mask_text(self, text: str) -> str:
        if not text:
            return text
        # Mask registered secrets
        for secret in self._secrets:
            if secret in text:
                masked = secret[:3] + "..." + secret[-3:] if len(secret) >= 8 else "***"
                text = text.replace(secret, masked)
        # Mask any Telegram bot tokens matched by regex
        text = self._token_pattern.sub(
            lambda m: m.group(0)[:6] + "..." + m.group(0)[-4:], text
        )
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.mask_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self.mask_text(str(v)) for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self.mask_text(str(a)) if isinstance(a, str) else a
                    for a in record.args
                )
        return True


class ChannelAdapter(logging.LoggerAdapter):
    """Contextual LoggerAdapter that prepends channel name and state if present."""

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        channel = self.extra.get("channel", "System") if self.extra else "System"
        state = self.extra.get("state", "") if self.extra else ""
        prefix = f"[{channel}]"
        if state:
            prefix += f"[{state}]"
        return f"{prefix} {msg}", kwargs


_redactor_filter = SecretRedactorFilter()


def register_secret_for_redaction(secret: str) -> None:
    """Register a sensitive secret string to be masked in all log outputs."""
    _redactor_filter.add_secret(secret)


def setup_logger(name: str = "trade_bot", log_level: str = "INFO") -> logging.Logger:
    """Configure and return the root application logger with structured formatting."""
    logger = logging.getLogger(name)
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(_EncodingSafeStream(sys.stdout))
        handler.setLevel(level)
        formatter = _UTCFormatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
        handler.setFormatter(formatter)
        handler.addFilter(_redactor_filter)
        logger.addHandler(handler)

    for handler in logger.handlers:
        handler.setLevel(level)

    return logger


def get_channel_logger(
    channel_name: str, state: str = "", base_logger: logging.Logger | None = None
) -> ChannelAdapter:
    """Return a contextual logger adapter bound to a specific channel name and optional state."""
    logger = base_logger or logging.getLogger("trade_bot")
    return ChannelAdapter(logger, {"channel": channel_name, "state": state})
