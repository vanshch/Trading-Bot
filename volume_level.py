"""Closed-hour OANDA tick-volume level monitor based on user IST target hour."""

import math
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import config
from tradingview_feed import SOURCE, fetch_tradingview

THRESHOLD_RATIO = 0.95
IST_OFFSET = timedelta(hours=5, minutes=30)
IST_TZ = timezone(IST_OFFSET, name="IST")


def parse_ist_time(input_str: str) -> Tuple[int, int]:
    """Parse a 24-hour time string in IST, e.g. '14', '14:00', '14:30'.

    Returns (hour, minute).
    Raises ValueError on invalid format or out-of-range times.
    """
    s = input_str.strip()
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?$", s)
    if not match:
        raise ValueError(f"Invalid time format '{input_str}'. Use HH:MM or HH (24-hour IST).")
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) is not None else 0
    if not (0 <= hour <= 23):
        raise ValueError(f"Hour {hour} out of range (0-23).")
    if not (0 <= minute <= 59):
        raise ValueError(f"Minute {minute} out of range (0-59).")
    return hour, minute


def find_target_ist_candle(
    closed_candles: List[Dict[str, Any]],
    target_hour: int,
    target_minute: int = 0,
    *,
    now: Optional[float] = None
) -> Dict[str, Any]:
    """Find the most recent closed OANDA hourly candle matching the user-specified IST time.

    OANDA 1H candles start at top-of-hour UTC (e.g. 09:00 UTC = 14:30 IST to 15:30 IST).
    - If user specifies an exact start (e.g. 14:30), it matches candles starting at 14:30 IST.
    - If user specifies an hour (e.g. 14:00 or 14), it matches the candle active during or
      closest to that hour.
    Iterates backwards through closed candles to pick the most recent one.
    """
    if not closed_candles:
        raise ValueError("No closed candle history available.")

    target_total_minutes = target_hour * 60 + target_minute

    # First pass: exact start minute match if user gave :30 (or exact start)
    for candle in reversed(closed_candles):
        if candle.get("source") != SOURCE:
            continue
        start_ts = float(candle.get("start_timestamp", 0))
        volume = float(candle.get("volume", -1))
        if volume <= 0 or not math.isfinite(volume):
            continue
        dt_ist = datetime.fromtimestamp(start_ts, IST_TZ)
        start_minutes = dt_ist.hour * 60 + dt_ist.minute
        if start_minutes == target_total_minutes:
            return candle

    # Second pass: candle spanning the target time (start_ist <= target < end_ist)
    for candle in reversed(closed_candles):
        if candle.get("source") != SOURCE:
            continue
        start_ts = float(candle.get("start_timestamp", 0))
        volume = float(candle.get("volume", -1))
        if volume <= 0 or not math.isfinite(volume):
            continue
        dt_ist = datetime.fromtimestamp(start_ts, IST_TZ)
        start_minutes = dt_ist.hour * 60 + dt_ist.minute
        # Handle 60m interval spanning across midnight (e.g. 23:30 to 00:30 IST)
        rel_target = (target_total_minutes - start_minutes) % 1440
        if 0 <= rel_target < 60:
            return candle

    # Third pass: candle starting in that hour (dt_ist.hour == target_hour)
    for candle in reversed(closed_candles):
        if candle.get("source") != SOURCE:
            continue
        start_ts = float(candle.get("start_timestamp", 0))
        volume = float(candle.get("volume", -1))
        if volume <= 0 or not math.isfinite(volume):
            continue
        dt_ist = datetime.fromtimestamp(start_ts, IST_TZ)
        if dt_ist.hour == target_hour:
            return candle

    raise ValueError(f"No completed OANDA 1H candle found matching {target_hour:02d}:{target_minute:02d} IST in recent history.")


def format_candle_ist_range(candle: Dict[str, Any]) -> str:
    """Format candle start-to-end range in IST (with date) and UTC for user messaging."""
    start_ts = float(candle["start_timestamp"])
    dt_ist = datetime.fromtimestamp(start_ts, IST_TZ)
    end_ist = dt_ist + timedelta(hours=1)
    dt_utc = datetime.fromtimestamp(start_ts, timezone.utc)
    return (
        f"{dt_ist.strftime('%d %b %H:%M')} – {end_ist.strftime('%H:%M')} IST "
        f"({dt_utc.strftime('%H:%M UTC')})"
    )


async def check_volume_level(bot, manager, *, started_at: float):
    """Poll live forming 1H candle and trigger alert if >= 95% of target volume level."""
    state = manager.get_volume_level()
    if not state.get("enabled"):
        return

    target_vol = state.get("target_volume", 0.0)
    threshold_vol = state.get("threshold_volume", 0.0)
    armed_after = state.get("armed_after")
    if target_vol <= 0 or threshold_vol <= 0:
        return

    _closed, forming = await fetch_tradingview("1h", max_closed=2)
    if not forming:
        return

    try:
        vol = float(forming.get("volume", 0.0))
        start_ts = int(forming.get("start_timestamp", 0))
    except (ValueError, TypeError):
        return

    if not math.isfinite(vol) or vol < 0 or start_ts <= 0:
        return

    candle_id = f"1h_{start_ts}"

    # Check qualification
    if vol >= threshold_vol:
        dt_ist = datetime.fromtimestamp(start_ts, IST_TZ)
        end_ist = dt_ist + timedelta(hours=1)
        candle_time_str = f"{dt_ist.strftime('%H:%M')} – {end_ist.strftime('%H:%M')} IST"
        percent_of_target = (vol / target_vol) * 100

        text = (
            "🔊 *XAU/USD [1H] Volume Level Alert!*\n"
            f"• *Live Forming Bar*: `{candle_time_str}`\n"
            f"• *Current Tick Volume*: `{vol:,.0f}`\n"
            f"• *Target Level Volume*: `{target_vol:,.0f}` ({state.get('target_hour_ist', 'N/A')})\n"
            f"• *Progress*: `{percent_of_target:.1f}%` of target (threshold: 95.0% = `{threshold_vol:,.0f}`)\n\n"
            "ℹ️ _Alert triggered & disarmed (one-shot)._"
        )
        try:
            await bot.send_message(
                chat_id=config.ALLOWED_USER_ID,
                text=text,
                parse_mode="Markdown",
            )
            config.logger.info(
                f"Dispatched Volume Level Alert: {vol:,.0f} >= {threshold_vol:,.0f} (95% of {target_vol:,.0f})"
            )
            # Only disarm once notification is successfully dispatched
            manager.consume_volume_level(
                candle_id,
                expected_armed_after=armed_after,
                candle_start_utc=start_ts,
                not_before=started_at,
            )
        except Exception as exc:
            config.logger.warning("Failed to dispatch volume level alert (%s)", type(exc).__name__)
