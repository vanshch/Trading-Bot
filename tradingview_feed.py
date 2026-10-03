"""Optional, anonymous OANDA history adapter; tvkit is imported only on use."""
import asyncio
import math
import sys
import time
from datetime import datetime, timezone

from config import get_interval_seconds, logger

SYMBOL = "OANDA:XAUUSD"
SOURCE = "TradingView (OANDA:XAUUSD)"
INTERVALS = {"5m": "5", "15m": "15", "1h": "60", "4h": "240"}
REQUEST_TIMEOUT_SECONDS = 25


def _client_type():
    if sys.version_info < (3, 11):
        raise RuntimeError("TradingView feed requires Python 3.11 or newer")
    from tvkit.api.chart.ohlcv import OHLCV

    class VerifiedOHLCV(OHLCV):
        # Observe only this client's stream, without modifying tvkit globally.
        async def _setup_services(self):
            await super()._setup_services()
            self.identities = []
            original = self.connection_service.get_data_stream

            async def observed():
                async for message in original():
                    if message.get("m") == "symbol_resolved":
                        identity = message["p"][-1]
                        if not isinstance(identity, dict):
                            raise ValueError("Invalid symbol identity")
                        self.identities.append(identity)
                    yield message

            self.connection_service.get_data_stream = observed

    return VerifiedOHLCV


def normalize_bars(bars, timeframe, max_closed, now):
    """Keep bar-start time for forming bars, bar-end time for closed bars."""
    interval = get_interval_seconds(timeframe)
    closed, forming = [], None
    previous = None
    for bar in bars:
        ts = float(bar.timestamp)
        values = tuple(float(getattr(bar, key)) for key in ("open", "high", "low", "close"))
        o, h, l, c = values
        if not math.isfinite(ts) or ts != int(ts) or ts < 0 or ts > now:
            raise ValueError("Invalid candle timestamp")
        if previous is not None and ts <= previous:
            raise ValueError("Duplicate or unordered candles")
        previous = ts
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError("Invalid candle price")
        if not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError("Invalid OHLC range")
        volume = float(bar.volume)
        if not math.isfinite(volume) or volume < 0:
            raise ValueError("Invalid candle volume")
        # OANDA's session boundaries need not align with UTC epoch multiples.
        # Prices are raw OANDA quotes; the Twelve Data offset never applies.
        pips = round((c - o) * 10, 1)
        candle = dict(open=o, high=h, low=l, close=c, pips=pips,
                      emoji="🟢" if c > o else ("🔴" if c < o else "⚪"),
                      sign="+" if pips > 0 else "", is_green=c > o, source=SOURCE)
        if ts + interval <= now:
            end = int(ts) + interval
            candle.update(timestamp=str(end), time_str=datetime.fromtimestamp(end, timezone.utc).strftime("%H:%M UTC"))
            closed.append(candle)
        else:
            candle.update(timestamp=str(int(ts)), start_time=datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M"))
            forming = candle
    return closed[-max_closed:], forming


async def fetch_tradingview(timeframe="15m", max_closed=2):
    if timeframe not in INTERVALS or not 1 <= max_closed <= 59:
        return [], None
    try:
        client_type = _client_type()

        async def request():
            # Explicit anonymous token avoids consulting browser/token env vars.
            async with client_type(max_attempts=3, base_backoff=1, max_backoff=4,
                                   auth_token="unauthorized_user_token") as client:
                bars = await client.get_historical_ohlcv(SYMBOL, INTERVALS[timeframe], bars_count=60)
                identities = getattr(client, "identities", [])
                if not identities or any(identity.get("full_name") != SYMBOL or identity.get("source_id") != "OANDA" for identity in identities):
                    raise ValueError("Wrong or unverified TradingView source")
                return normalize_bars(bars, timeframe, max_closed, time.time())

        return await asyncio.wait_for(request(), timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        # Provider exceptions may contain URLs; retain only the exception type.
        logger.warning("TradingView feed unavailable (%s); using fallback", type(exc).__name__)
        return [], None
