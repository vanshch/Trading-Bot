"""State machine and trade lifecycle tracking per YouTube channel."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class TradeState(str, Enum):
    """Current state of monitoring for a channel."""

    IDLE = "IDLE"
    ACTIVE_TRADE = "ACTIVE_TRADE"
    CLOSED_TRADE = "CLOSED_TRADE"


class StateTransition(str, Enum):
    """Lifecycle transition event triggered by analysis."""

    NONE = "NONE"
    NEW_TRADE = "NEW_TRADE"
    STILL_ACTIVE = "STILL_ACTIVE"
    TRADE_CLOSED = "TRADE_CLOSED"


@dataclass
class TradeInfo:
    """Detailed record of an active or concluded trade."""

    channel_name: str
    direction: str
    instrument: str
    entry_or_pnl: str
    confidence: float
    reasoning: str
    opened_at: datetime  # Timezone-aware UTC
    last_seen_at: datetime  # Timezone-aware UTC
    closed_at: datetime | None = None  # Timezone-aware UTC
    last_frame_bytes: bytes | None = None

    @property
    def duration_seconds(self) -> float:
        """Calculate elapsed trade duration in seconds."""
        end_time = self.closed_at or self.last_seen_at
        return max(0.0, (end_time - self.opened_at).total_seconds())

    @property
    def formatted_duration(self) -> str:
        """Return human-readable duration string (e.g. '12m 30s')."""
        total_sec = int(self.duration_seconds)
        hours, remainder = divmod(total_sec, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"


class ChannelStateRecord:
    """Internal state tracking data for an individual channel."""

    def __init__(self) -> None:
        self.state: TradeState = TradeState.IDLE
        self.current_trade: TradeInfo | None = None
        self.consecutive_misses: int = 0


class TradeStateTracker:
    """Manages trade lifecycle state transitions across monitored channels."""

    def __init__(self, miss_threshold: int = 2) -> None:
        """Initialize the state tracker.

        Args:
            miss_threshold: Number of consecutive frames without trade detection
                            before transitioning an active trade to CLOSED.
        """
        self.miss_threshold = max(1, miss_threshold)
        self._channels: dict[str, ChannelStateRecord] = {}

    def _get_or_create_channel(self, channel_name: str) -> ChannelStateRecord:
        if channel_name not in self._channels:
            self._channels[channel_name] = ChannelStateRecord()
        return self._channels[channel_name]

    def get_state(self, channel_name: str) -> TradeState:
        """Get the current TradeState for a given channel."""
        record = self._get_or_create_channel(channel_name)
        return record.state

    def get_active_trade(self, channel_name: str) -> TradeInfo | None:
        """Get the current active TradeInfo for a given channel, if any."""
        record = self._get_or_create_channel(channel_name)
        return record.current_trade if record.state == TradeState.ACTIVE_TRADE else None

    def reset_channel(self, channel_name: str) -> None:
        """Reset channel state back to IDLE."""
        record = self._get_or_create_channel(channel_name)
        record.state = TradeState.IDLE
        record.current_trade = None
        record.consecutive_misses = 0

    def process_verification(
        self,
        channel_name: str,
        verification: Any,  # TradeVerification or MultimodalTradeVerification
        frame_bytes: bytes | None = None,
    ) -> tuple[StateTransition, TradeInfo | None]:
        """Evaluate a verification result against the channel's state machine.

        Args:
            channel_name: Friendly channel name.
            verification: TradeVerification or MultimodalTradeVerification output.
            frame_bytes: Optional raw JPEG screenshot bytes.

        Returns:
            Tuple of (StateTransition, TradeInfo or None)
        """
        record = self._get_or_create_channel(channel_name)
        now_utc = datetime.now(timezone.utc)

        is_active = getattr(verification, "is_trade_active", False)
        direction = getattr(verification, "direction", "NONE")
        instrument = getattr(verification, "instrument", "UNKNOWN")
        entry_details = getattr(
            verification,
            "entry_price",
            getattr(verification, "entry_or_pnl", ""),
        ) or ""
        confidence = float(getattr(verification, "confidence", 0.0))
        reasoning = getattr(verification, "reasoning", "")

        if is_active:
            # Trade is detected
            record.consecutive_misses = 0

            if record.state == TradeState.IDLE:
                # Transition: IDLE -> ACTIVE_TRADE (NEW_TRADE)
                record.state = TradeState.ACTIVE_TRADE
                trade = TradeInfo(
                    channel_name=channel_name,
                    direction=direction,
                    instrument=instrument or "UNKNOWN",
                    entry_or_pnl=entry_details,
                    confidence=confidence,
                    reasoning=reasoning,
                    opened_at=now_utc,
                    last_seen_at=now_utc,
                    closed_at=None,
                    last_frame_bytes=frame_bytes,
                )
                record.current_trade = trade
                return StateTransition.NEW_TRADE, trade

            else:
                # State: Already ACTIVE_TRADE (STILL_ACTIVE)
                if record.current_trade is not None:
                    record.current_trade.last_seen_at = now_utc
                    if entry_details:
                        record.current_trade.entry_or_pnl = entry_details
                    if direction != "NONE":
                        record.current_trade.direction = direction
                    if instrument and instrument != "UNKNOWN":
                        record.current_trade.instrument = instrument
                    if frame_bytes is not None:
                        record.current_trade.last_frame_bytes = frame_bytes

                return StateTransition.STILL_ACTIVE, record.current_trade

        else:
            # Trade is NOT detected in the current frame
            if record.state == TradeState.ACTIVE_TRADE:
                record.consecutive_misses += 1

                if record.consecutive_misses >= self.miss_threshold:
                    # Transition: ACTIVE_TRADE -> IDLE (TRADE_CLOSED)
                    closed_trade = record.current_trade
                    if closed_trade is not None:
                        closed_trade.closed_at = now_utc

                    record.state = TradeState.IDLE
                    record.current_trade = None
                    record.consecutive_misses = 0

                    return StateTransition.TRADE_CLOSED, closed_trade
                else:
                    # Temporary miss within tolerance window
                    return StateTransition.STILL_ACTIVE, record.current_trade

            else:
                # Channel is already IDLE
                return StateTransition.NONE, None
