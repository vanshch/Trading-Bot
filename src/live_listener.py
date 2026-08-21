"""Decoupled asynchronous YouTube channel live stream listener and Telegram notifier."""

import asyncio
import logging

from src.config import CaptureConfig
from src.live_alert_state import LiveAlertStateStore
from src.logger import get_channel_logger
from src.notifier import TelegramNotifier
from src.stream_manager import StreamManager, StreamStatus

logger = logging.getLogger("trade_bot.live_listener")


class LiveChannelListener:
    """Decoupled listener that polls channel live status and notifies Telegram when live."""

    def __init__(
        self,
        channel_name: str,
        channel_url: str,
        notifier: TelegramNotifier,
        poll_interval_seconds: float = 30.0,
        capture_config: CaptureConfig | None = None,
        stream_manager: StreamManager | None = None,
        alert_state: LiveAlertStateStore | None = None,
    ) -> None:
        self.channel_name = channel_name.strip()
        self.channel_url = channel_url.strip()
        self.notifier = notifier
        self.poll_interval_seconds = max(5.0, float(poll_interval_seconds))
        self.capture_config = capture_config or CaptureConfig()
        self._stream_manager = stream_manager or StreamManager(
            channel_url=self.channel_url,
            config=self.capture_config,
        )
        self._owns_stream_manager = stream_manager is None
        self._alert_state = alert_state or LiveAlertStateStore()
        self._is_live: bool = False
        self._last_alerted_live: bool = False
        self._consecutive_offline_count: int = 0
        self._ch_logger = get_channel_logger(self.channel_name, "LIVE_LISTENER", logger)

    @property
    def is_live(self) -> bool:
        """Return the latest known live state."""
        return self._is_live

    @property
    def consecutive_offline_count(self) -> int:
        """Return current count of consecutive confirmed-offline checks."""
        return self._consecutive_offline_count

    async def check_and_notify(self) -> tuple[bool, bool]:
        """Perform a single live status check and dispatch alert if newly live.

        Returns:
            tuple[bool, bool]: (is_currently_live, alert_was_dispatched)
        """
        try:
            raw_status, _stream_url = await self._stream_manager.check_live_status()
        except Exception as exc:  # noqa: BLE001 - isolated fault boundary
            self._ch_logger.warning(
                f"Live status check failed for {self.channel_name}: {exc}"
            )
            raw_status = StreamStatus.UNKNOWN
            _stream_url = None

        if isinstance(raw_status, StreamStatus):
            status = raw_status
        elif isinstance(raw_status, bool):
            status = StreamStatus.LIVE if raw_status else StreamStatus.OFFLINE
        elif isinstance(raw_status, str):
            try:
                status = StreamStatus(raw_status)
            except ValueError:
                status = StreamStatus.UNKNOWN
        else:
            status = StreamStatus.UNKNOWN

        alert_dispatched = False

        if status == StreamStatus.LIVE:
            self._is_live = True
            self._consecutive_offline_count = 0

            # Extract deterministic YouTube broadcast ID exposed by StreamManager
            raw_broadcast_id = getattr(self._stream_manager, "broadcast_id", None)
            broadcast_id = (
                str(raw_broadcast_id).strip()
                if raw_broadcast_id and isinstance(raw_broadcast_id, str)
                else None
            )

            if not self._last_alerted_live:
                # If a live result lacks a broadcast ID, fail closed / suppress alert
                if not broadcast_id:
                    self._ch_logger.warning(
                        f"Channel {self.channel_name} went LIVE but lacks a broadcast ID. "
                        "Suppressing live alert to prevent duplicate notifications."
                    )
                    return self._is_live, False

                # Claim and atomically persist broadcast ID before calling Telegram
                claimed = self._alert_state.claim_broadcast(
                    self.channel_url, broadcast_id
                )
                self._last_alerted_live = True

                if claimed:
                    self._ch_logger.info(
                        f"CHANNEL WENT LIVE: {self.channel_name} ({self.channel_url}) "
                        f"[broadcast_id={broadcast_id}]. Sending Telegram alert..."
                    )
                    try:
                        alert_dispatched = await self.notifier.send_stream_live_alert(
                            channel_name=self.channel_name,
                            stream_url=self.channel_url,
                        )
                        if alert_dispatched:
                            self._ch_logger.info(
                                f"Live notification successfully delivered for {self.channel_name} "
                                f"(broadcast_id={broadcast_id})."
                            )
                        else:
                            self._ch_logger.error(
                                f"Failed to deliver live notification for {self.channel_name} "
                                f"(broadcast_id={broadcast_id}). Claim retained."
                            )
                    except Exception as exc:  # noqa: BLE001
                        self._ch_logger.error(
                            f"Exception while dispatching live alert for {self.channel_name} "
                            f"(broadcast_id={broadcast_id}): {exc}. Claim retained."
                        )
                else:
                    self._ch_logger.info(
                        f"Live alert for {self.channel_name} [broadcast_id={broadcast_id}] "
                        "suppressed: already claimed or state failure (fail-closed)."
                    )
            else:
                self._ch_logger.debug(
                    f"Channel {self.channel_name} is still live. Alert already dispatched."
                )

        elif status == StreamStatus.OFFLINE:
            self._is_live = False
            self._consecutive_offline_count += 1

            if self._last_alerted_live:
                if self._consecutive_offline_count >= 2:
                    self._ch_logger.info(
                        f"Channel {self.channel_name} confirmed offline (2 consecutive checks). Resetting alert debounce."
                    )
                    self._last_alerted_live = False
                else:
                    self._ch_logger.debug(
                        f"Channel {self.channel_name} reported offline (check 1 of 2). Preserving debounce until confirmed."
                    )

        elif status == StreamStatus.UNKNOWN:
            # Preserve prior known live state and debounce across transient/unknown results.
            # Reset consecutive offline count because OFFLINE sequence was interrupted by an unknown result.
            self._consecutive_offline_count = 0
            self._ch_logger.warning(
                f"Channel {self.channel_name} status is UNKNOWN (transient error). "
                f"Preserving prior state (is_live={self._is_live}, alerted={self._last_alerted_live})."
            )

        return self._is_live, alert_dispatched

    async def listen(self, stop_event: asyncio.Event) -> None:
        """Continuous background polling loop with graceful cancellation and error isolation."""
        self._ch_logger.info(
            f"Starting decoupled live listener for {self.channel_name} (interval: {self.poll_interval_seconds}s)..."
        )
        try:
            while not stop_event.is_set():
                try:
                    await self.check_and_notify()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - keep listener alive on unexpected errors
                    self._ch_logger.warning(
                        f"Unexpected error in live listener iteration: {exc}"
                    )

                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.poll_interval_seconds,
                    )
                    break
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            self._ch_logger.info(
                f"Live listener loop cancelled for {self.channel_name}."
            )
        finally:
            await self.close()
            self._ch_logger.info(f"Live listener stopped for {self.channel_name}.")

    async def close(self) -> None:
        """Release underlying stream manager resources if owned."""
        if self._owns_stream_manager and self._stream_manager is not None:
            try:
                await self._stream_manager.close()
            except Exception as exc:  # noqa: BLE001
                self._ch_logger.debug(f"Error closing stream manager: {exc}")
