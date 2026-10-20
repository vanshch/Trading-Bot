"""Closed-hour OANDA tick-volume alarm; no spot/futures fallback."""

import math
import time
from datetime import datetime, timezone

import config
from tradingview_feed import SOURCE, fetch_tradingview

LOOKBACK = 20
VOLUME_MULTIPLIER = 1.20
SETTING_LABEL = "1H Volume ≥120% MA20"


def evaluate_hourly_volume(candles, *, now):
    """Compare the latest closed hour with the preceding 20 trading bars.

    Session/weekend gaps are allowed; native starts are never epoch-rounded.
    Forming bars, mixed providers and missing/invalid volume are rejected.
    """
    if len(candles) < LOOKBACK + 1:
        raise ValueError("Need 21 completed OANDA hourly candles")
    window = candles[-(LOOKBACK + 1) :]
    previous_end = -1
    volumes = []
    for candle in window:
        start = float(candle["start_timestamp"])
        end = float(candle["timestamp"])
        volume = float(candle["volume"])
        if (
            candle.get("source") != SOURCE
            or not all(math.isfinite(v) for v in (start, end, volume))
            or start < 0
            or start != int(start)
            or end != start + 3600
            or end > now
            or start < previous_end
            or volume < 0
        ):
            raise ValueError("Invalid closed OANDA hourly volume history")
        previous_end = end
        volumes.append(volume)
    average = math.fsum(v / LOOKBACK for v in volumes[:-1])
    if average <= 0:
        raise ValueError("Volume baseline must be positive")
    return {
        "timestamp": int(window[-1]["timestamp"]),
        "volume": volumes[-1],
        "average": average,
        "qualifies": volumes[-1] >= average * VOLUME_MULTIPLIER,
    }


async def check_hourly_volume(bot, manager, *, started_at):
    """Run one bounded check. Invalid/unavailable data leaves the alarm armed."""
    if not manager.is_hourly_volume_enabled():
        return
    closed, _forming = await fetch_tradingview("1h", max_closed=LOOKBACK + 1)
    try:
        result = evaluate_hourly_volume(closed, now=time.time())
    except (ValueError, KeyError, TypeError, OverflowError):
        config.logger.warning(
            "Hourly volume alarm: verified 21-bar history unavailable; alarm retained"
        )
        return
    # Re-read after the await: OFF/re-arming during a fetch must take effect.
    if not manager.process_hourly_volume_close(
        result["timestamp"], result["qualifies"], not_before=started_at
    ):
        return
    close_time = datetime.fromtimestamp(result["timestamp"], timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    percent = (result["volume"] / result["average"] - 1) * 100
    await bot.send_message(
        chat_id=config.ALLOWED_USER_ID,
        text=(
            "🔊 *XAU/USD [1H] High-volume candle occurred!*\n"
            f"• *Candle closed*: `{close_time}`\n"
            f"• *OANDA tick volume*: `{result['volume']:,.0f}`\n"
            f"• *Previous 20 closed hours' average*: `{result['average']:,.2f}`\n"
            f"• *Above average*: `{percent:.2f}%` (required: at least 20%)\n"
            "ℹ️ One-shot complete: hourly volume alarm is now OFF."
        ),
        parse_mode="Markdown",
    )
