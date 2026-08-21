"""Durable state persistence for YouTube live alert deduplication.

Tracks alerted YouTube broadcast IDs per normalized channel URL to guarantee
at-most-once live notification semantics across process restarts and listener flaps.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("trade_bot.live_alert_state")

DEFAULT_ALERT_STATE_PATH = Path("data/live_alert_state.json")
DEFAULT_MAX_HISTORY_PER_CHANNEL = 50


def normalize_channel_url(url: str) -> str:
    """Normalize YouTube channel/stream URL for deterministic lookup."""
    if not url or not isinstance(url, str):
        return ""
    clean = url.strip()
    if clean.endswith("/"):
        clean = clean.rstrip("/")
    return clean.lower()


class LiveAlertStateStore:
    """Durable, thread-safe, atomic state store for tracking alerted YouTube broadcast IDs."""

    def __init__(
        self,
        storage_path: str | Path = DEFAULT_ALERT_STATE_PATH,
        max_history_per_channel: int = DEFAULT_MAX_HISTORY_PER_CHANNEL,
    ) -> None:
        self.storage_path = Path(storage_path)
        self.max_history_per_channel = max(1, int(max_history_per_channel))
        self._lock = threading.Lock()

    def _read_state_locked(self) -> dict[str, Any]:
        """Read state from disk under lock.

        Returns a dict with 'version' and 'channels' keys.
        Raises OSError, json.JSONDecodeError, or TypeError on corrupted state.
        """
        if not self.storage_path.exists():
            return {"version": 1, "channels": {}}

        with open(self.storage_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise TypeError(f"State file root is not a dict: {type(data)}")

        channels = data.get("channels")
        if channels is None:
            data["channels"] = {}
        elif not isinstance(channels, dict):
            raise TypeError(
                f"State file channels field is not a dict: {type(channels)}"
            )

        return data

    def _write_state_locked(self, data: dict[str, Any]) -> None:
        """Atomically persist state to disk under lock.

        Writes to a temporary file in the destination directory, flushes and fsyncs,
        then replaces the destination file via os.replace.
        """
        parent_dir = self.storage_path.parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=parent_dir,
                delete=False,
                prefix=f"{self.storage_path.stem}_",
                suffix=".tmp",
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)
                json.dump(data, tmp_file, indent=2)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())

            os.replace(tmp_path, self.storage_path)
        except Exception as exc:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            logger.error(
                f"Failed to write live alert state to {self.storage_path}: {exc}"
            )
            raise

    def claim_broadcast(self, channel_url: str, broadcast_id: str | None) -> bool:
        """Atomically claim a broadcast ID for a channel before Telegram alert dispatch.

        Guarantees at-most-once semantics:
        - If already claimed, returns False.
        - If broadcast_id is missing/empty, returns False (fail closed).
        - If state cannot be read or durably written, returns False (fail closed).
        - On successful claim and durable write, returns True.
        """
        if (
            not broadcast_id
            or not isinstance(broadcast_id, str)
            or not broadcast_id.strip()
        ):
            logger.warning(
                f"Cannot claim alert: missing or invalid broadcast ID for {channel_url}"
            )
            return False

        norm_url = normalize_channel_url(channel_url)
        if not norm_url:
            logger.warning("Cannot claim alert: empty or invalid channel URL")
            return False

        b_id = broadcast_id.strip()

        with self._lock:
            try:
                state = self._read_state_locked()
                channels = state.get("channels", {})
                history = channels.get(norm_url, [])
                if not isinstance(history, list):
                    logger.error(f"Invalid history structure for channel {norm_url}")
                    return False

                if b_id in history:
                    logger.debug(
                        f"Broadcast {b_id} has already been claimed for channel {norm_url}"
                    )
                    return False

                new_history = list(history)
                new_history.append(b_id)
                if len(new_history) > self.max_history_per_channel:
                    new_history = new_history[-self.max_history_per_channel :]

                channels[norm_url] = new_history
                state["channels"] = channels
                state["version"] = state.get("version", 1)

                self._write_state_locked(state)
                logger.info(
                    f"Successfully claimed broadcast ID [{b_id}] for {norm_url}"
                )
                return True
            except Exception as exc:  # noqa: BLE001 - fail-closed state boundary
                logger.error(
                    f"Alert state claim failed closed for {norm_url} ({b_id}): {exc}"
                )
                return False

    def has_claimed(self, channel_url: str, broadcast_id: str | None) -> bool:
        """Check if a broadcast ID has already been claimed for a channel."""
        if (
            not broadcast_id
            or not isinstance(broadcast_id, str)
            or not broadcast_id.strip()
        ):
            return False
        norm_url = normalize_channel_url(channel_url)
        if not norm_url:
            return False
        b_id = broadcast_id.strip()
        with self._lock:
            try:
                state = self._read_state_locked()
                channels = state.get("channels", {})
                history = channels.get(norm_url, [])
                return isinstance(history, list) and b_id in history
            except Exception:  # noqa: BLE001 - read-only query safe fallback
                return False

    def get_history(self, channel_url: str) -> list[str]:
        """Return the bounded history of claimed broadcast IDs for a channel."""
        norm_url = normalize_channel_url(channel_url)
        if not norm_url:
            return []
        with self._lock:
            try:
                state = self._read_state_locked()
                channels = state.get("channels", {})
                history = channels.get(norm_url, [])
                return list(history) if isinstance(history, list) else []
            except Exception:  # noqa: BLE001 - read-only query safe fallback
                return []
