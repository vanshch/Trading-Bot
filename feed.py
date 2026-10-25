import time
import config
import httpx
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

from config import logger, state, TWELVE_DATA_API_KEY, get_interval_seconds, OANDA_PRICE_OFFSET

TWELVE_DATA_INTERVALS = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
}

YAHOO_INTERVALS = {
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "1h",
}

async def fetch_twelve_data(timeframe: str = "15m", max_closed: int = 2) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    if not TWELVE_DATA_API_KEY:
        return [], None

    interval = TWELVE_DATA_INTERVALS.get(timeframe, "15min")
    url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={interval}&outputsize=8&timezone=UTC&apikey={TWELVE_DATA_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(f"Twelve Data returned status {resp.status_code}")
                return [], None
            data = resp.json()
            if data.get("status") != "ok" or not data.get("values"):
                return [], None

            values = data["values"]  # newest to oldest
            now_ts = int(time.time())
            interval_sec = get_interval_seconds(timeframe)

            closed_candidates = []
            forming_candidate = None

            # Process in chronological order (oldest to newest)
            for val in reversed(values):
                try:
                    dt = datetime.strptime(val["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    start_ts = int(dt.timestamp())
                except Exception:
                    continue

                raw_o = float(val["open"])
                raw_h = float(val["high"])
                raw_l = float(val["low"])
                raw_c = float(val["close"])

                o = round(raw_o + OANDA_PRICE_OFFSET, 2)
                h = round(raw_h + OANDA_PRICE_OFFSET, 2)
                l = round(raw_l + OANDA_PRICE_OFFSET, 2)
                c = round(raw_c + OANDA_PRICE_OFFSET, 2)
                diff = c - o
                pips = round(diff * 10, 1)

                is_green = diff > 0
                emoji = "🟢" if diff > 0 else ("🔴" if diff < 0 else "⚪")
                sign = "+" if pips > 0 else ""

                source_label = (
                    f"Twelve Data (XAU/USD [OANDA {OANDA_PRICE_OFFSET:+.2f}])"
                    if OANDA_PRICE_OFFSET != 0.0
                    else "Twelve Data (XAU/USD)"
                )

                if now_ts >= start_ts + interval_sec:
                    # Completed bar: close time is start_ts + interval_sec
                    close_ts = start_ts + interval_sec
                    close_dt = datetime.fromtimestamp(close_ts, tz=timezone.utc)
                    time_str = close_dt.strftime("%H:%M UTC")

                    closed_candidates.append({
                        "timestamp": str(close_ts),
                        "time_str": time_str,
                        "open": o,
                        "high": h,
                        "low": l,
                        "close": c,
                        "pips": pips,
                        "emoji": emoji,
                        "sign": sign,
                        "is_green": is_green,
                        "source": source_label
                    })
                else:
                    # Forming bar in progress
                    forming_candidate = {
                        "timestamp": str(start_ts),
                        "start_time": dt.strftime("%H:%M"),
                        "open": o,
                        "high": h,
                        "low": l,
                        "close": c,
                        "pips": pips,
                        "emoji": emoji,
                        "sign": sign,
                        "is_green": is_green,
                        "source": source_label
                    }

            return closed_candidates[-max_closed:], forming_candidate
    except Exception as e:
        logger.error(f"Error fetching from Twelve Data: {e}")
        return [], None

async def fetch_yahoo_finance(timeframe: str = "15m", max_closed: int = 2) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    interval = YAHOO_INTERVALS.get(timeframe, "15m")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval={interval}&range=2d"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(f"Yahoo Finance returned status {resp.status_code}")
                return [], None

            data = resp.json()
            result = data.get("chart", {}).get("result")
            if not result or not result[0]:
                return [], None

            res = result[0]
            timestamps = res.get("timestamp", [])
            quotes = res.get("indicators", {}).get("quote", [{}])[0]
            if not timestamps or not quotes:
                return [], None

            now_ts = int(time.time())
            interval_sec = get_interval_seconds(timeframe)
            if timeframe == "4h":
                interval_sec = 4 * 3600

            closed_candidates = []
            forming_candidate = None

            for i in range(len(timestamps)):
                ts = timestamps[i]
                o = quotes.get("open", [None])[i]
                h = quotes.get("high", [None])[i]
                l = quotes.get("low", [None])[i]
                c = quotes.get("close", [None])[i]

                if o is None or c is None:
                    continue

                open_p = float(o)
                close_p = float(c)
                high_p = float(h if h is not None else max(open_p, close_p))
                low_p = float(l if l is not None else min(open_p, close_p))
                diff = close_p - open_p
                pips = round(diff * 10, 1)

                is_green = diff > 0
                emoji = "🟢" if diff > 0 else ("🔴" if diff < 0 else "⚪")
                sign = "+" if pips > 0 else ""

                if now_ts >= ts + interval_sec:
                    close_ts = ts + interval_sec
                    close_dt = datetime.fromtimestamp(close_ts, tz=timezone.utc)
                    time_str = close_dt.strftime("%H:%M UTC")

                    closed_candidates.append({
                        "timestamp": str(close_ts),
                        "time_str": time_str,
                        "open": open_p,
                        "high": high_p,
                        "low": low_p,
                        "close": close_p,
                        "pips": pips,
                        "emoji": emoji,
                        "sign": sign,
                        "is_green": is_green,
                        "source": "Yahoo Finance (GC=F)"
                    })
                else:
                    start_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    forming_candidate = {
                        "timestamp": str(ts),
                        "start_time": start_dt.strftime("%H:%M"),
                        "open": open_p,
                        "high": high_p,
                        "low": low_p,
                        "close": close_p,
                        "pips": pips,
                        "emoji": emoji,
                        "sign": sign,
                        "is_green": is_green,
                        "source": "Yahoo Finance (GC=F)"
                    }

            return closed_candidates[-max_closed:], forming_candidate
    except Exception as e:
        logger.error(f"Error fetching from Yahoo Finance: {e}")
        return [], None

async def get_latest_closed_candles(timeframe: str = "15m", count: int = 2) -> List[Dict[str, Any]]:
    if config.TRADINGVIEW_FEED_ENABLED:
        from tradingview_feed import fetch_tradingview
        closed, _ = await fetch_tradingview(timeframe, max_closed=count)
        if closed:
            state.last_feed_source = closed[-1]["source"]
            return closed
    closed, _ = await fetch_twelve_data(timeframe, max_closed=count)
    if closed:
        state.last_feed_source = closed[-1]["source"]
        return closed

    closed, _ = await fetch_yahoo_finance(timeframe, max_closed=count)
    if closed:
        state.last_feed_source = closed[-1]["source"]
        return closed

    return []

async def get_latest_candle(timeframe: str = "15m") -> Optional[Dict[str, Any]]:
    closed = await get_latest_closed_candles(timeframe, count=1)
    return closed[-1] if closed else None

async def get_forming_candle(timeframe: str = "15m") -> Optional[Dict[str, Any]]:
    if config.TRADINGVIEW_FEED_ENABLED:
        from tradingview_feed import fetch_tradingview
        _, forming = await fetch_tradingview(timeframe)
        if forming:
            return forming
    _, forming = await fetch_twelve_data(timeframe)
    if forming:
        return forming

    _, forming = await fetch_yahoo_finance(timeframe)
    return forming

def format_candle_message(candle: Dict[str, Any], timeframe: str) -> str:
    tf_upper = timeframe.upper()
    close_str = f"{candle['close']:,.2f}"
    pips_str = f"{candle['pips']:.1f}"
    return f"🏁 *Last Closed Bar [{tf_upper}]:* `${close_str}` ({candle['sign']}{pips_str} pips {candle['emoji']}) · `{candle['time_str']}`"

def format_forming_candle_message(candle: Dict[str, Any], timeframe: str) -> str:
    tf_upper = timeframe.upper()
    close_str = f"{candle['close']:,.2f}"
    pips_str = f"{candle['pips']:.1f}"
    return (
        f"⏳ *Live Forming Bar [{tf_upper}]*\n"
        f"• *Current*: `${close_str}` ({candle['sign']}{pips_str} pips {candle['emoji']})\n"
        f"• *Range*: `${candle['low']:,.2f}` ➔ `${candle['high']:,.2f}` (Open: `${candle['open']:,.2f}`)\n"
        f"• *Started*: `{candle['start_time']} UTC`"
    )
