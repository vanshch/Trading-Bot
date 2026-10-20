"""Small TradingView-style PNG renderer for verified OANDA hourly tick data."""
import asyncio
import math
from datetime import datetime, timezone
from io import BytesIO

from tradingview_feed import fetch_tradingview, SOURCE

CANDLE_COUNT = 20
SIZE = (1296, 800)
UP, DOWN = '#089981', '#f23645'
BACKGROUND, TEXT, MUTED = '#101010', '#d1d4dc', '#999da8'


class HourlyHistoryUnavailable(ValueError):
    """The verified provider did not return enough bars for an hourly chart."""


async def get_hourly_candles():
    # Dedicated on-demand OANDA request: never label a fallback as OANDA.
    closed, forming = await fetch_tradingview('1h', max_closed=CANDLE_COUNT)
    candles = (closed + ([forming] if forming else []))[-CANDLE_COUNT:]
    if len(candles) != CANDLE_COUNT:
        raise HourlyHistoryUnavailable('Twenty verified hourly candles are unavailable')
    return candles, forming is not None


def _font(size):
    from PIL import ImageFont
    for name in ('DejaVuSans.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                 'C:/Windows/Fonts/arial.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _volume(value):
    return f'{value / 1000:.2f}K' if value >= 1000 else f'{value:,.0f}'


def render_hourly_chart(candles, has_forming=False, *, captured_at=None):
    """Render exactly 20 ordered bar starts; tick volume is not traded contracts."""
    from PIL import Image, ImageDraw
    if len(candles) != CANDLE_COUNT:
        raise ValueError('Expected exactly twenty candles')
    previous = -1
    for c in candles:
        values = [c[k] for k in ('open', 'high', 'low', 'close', 'volume', 'start_timestamp')]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Non-finite chart data')
        if (c['source'] != SOURCE or c['start_timestamp'] <= previous or
                not 0 < c['low'] <= min(c['open'], c['close']) <= max(c['open'], c['close']) <= c['high'] or
                c['volume'] < 0):
            raise ValueError('Invalid chart data')
        previous = c['start_timestamp']
    image = Image.new('RGB', SIZE, BACKGROUND)
    draw = ImageDraw.Draw(image)
    normal, small, title = _font(19), _font(16), _font(23)
    left, right, top, bottom = 16, 1168, 126, 724
    last = candles[-1]
    color = UP if last['close'] >= last['open'] else DOWN
    draw.text((18, 14), 'Gold Spot / U.S. Dollar · 1h · OANDA', font=title, fill=TEXT)
    delta = last['close'] - last['open']
    draw.text((18, 52), f"O {last['open']:,.3f}   H {last['high']:,.3f}   L {last['low']:,.3f}   "
              f"C {last['close']:,.3f}   {delta:+.3f} ({delta / last['open'] * 100:+.2f}%)",
              font=normal, fill=color)
    draw.text((18, 87), f"Vol · Ticks  {_volume(last['volume'])}", font=normal, fill=color)
    status = 'Latest candle forming' if has_forming else 'Latest candle closed'
    draw.text((785, 88), f'20 hourly candles · {status}', font=small, fill=MUTED)

    low, high = min(c['low'] for c in candles), max(c['high'] for c in candles)
    span = max(high - low, max(high, 1) * .0005)
    low, high = low - span * .25, high + span * .10
    raw_step = (high - low) / 8
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(v * magnitude for v in (1, 2, 2.5, 5, 10) if v * magnitude >= raw_step)
    low, high = math.floor(low / step) * step, math.ceil(high / step) * step
    def y(price):
        return round(bottom - (price - low) / (high - low) * (bottom - top))
    def dotted(a, b, fill):
        if a[1] == b[1]:
            for x in range(a[0], b[0], 5):
                draw.point((x, a[1]), fill=fill)
        else:
            for yy in range(a[1], b[1], 5):
                draw.point((a[0], yy), fill=fill)
    for n in range(round((high - low) / step) + 1):
        price = low + n * step
        yy = y(price)
        dotted((left, yy), (right, yy), '#474747')
        draw.text((right + 12, yy - 11), f'{price:,.3f}', font=normal, fill=TEXT)
    # Equal bar spacing compresses market closures like the reference chart.
    spacing = (right - left) / (CANDLE_COUNT + 4)
    half = round(spacing * .32)
    vmax = max(c['volume'] for c in candles) or 1
    for i, c in enumerate(candles):
        x = round(left + spacing * (i + .8))
        if i % 4 == 0:
            dotted((x, top), (x, bottom), '#3a3a3a')
        bullish = c['close'] >= c['open']
        candle_color = UP if bullish else DOWN
        volume_color = '#164f47' if bullish else '#6e292f'
        height = round(c['volume'] / vmax * 145)
        if height:
            draw.rectangle((x - half, bottom - height, x + half, bottom), fill=volume_color)
        draw.line((x, y(c['high']), x, y(c['low'])), fill=candle_color, width=2)
        body_top, body_bottom = sorted((y(c['open']), y(c['close'])))
        draw.rectangle((x - half, body_top, x + half, max(body_top + 1, body_bottom)), fill=candle_color)
        if i % 4 == 0:
            dt = datetime.fromtimestamp(c['start_timestamp'], timezone.utc)
            draw.text((x - 23, bottom + 12), dt.strftime('%H:%M'), font=small, fill=TEXT)
            draw.text((x - 27, bottom + 35), dt.strftime('%d %b'), font=small, fill=MUTED)
    yy = y(last['close'])
    dotted((left, yy), (right, yy), color)
    # Clear any nearby scale label before drawing the current-price badge.
    draw.rectangle((right, yy - 26, SIZE[0] - 1, yy + 26), fill=BACKGROUND)
    draw.rectangle((right, yy - 15, SIZE[0] - 5, yy + 15), fill=color)
    draw.text((right + 9, yy - 12), f"{last['close']:,.3f}", font=normal, fill='white')
    draw.text((right - 60, bottom + 14), 'UTC', font=small, fill=MUTED)
    captured_at = captured_at or datetime.now(timezone.utc)
    draw.text((18, 779), f"tvkit · OANDA tick volume · Snapshot {captured_at:%Y-%m-%d %H:%M:%S} UTC",
              font=_font(13), fill=MUTED)
    output = BytesIO()
    image.save(output, format='PNG')
    image.close()
    return output.getvalue()


async def capture_hourly_chart():
    candles, forming = await get_hourly_candles()
    # Keep PNG compression/drawing off the scheduler's event loop.
    png = await asyncio.to_thread(render_hourly_chart, candles, forming)
    last_start = datetime.fromtimestamp(candles[-1]['start_timestamp'], timezone.utc)
    caption = (f'OANDA XAU/USD · 1H · latest 20 candles\n'
               f'Last bar starts: {last_start:%Y-%m-%d %H:%M} UTC '
               f"({'forming' if forming else 'closed'})\n"
               'Rendered from verified tvkit OHLC and OANDA tick volume. '
               'Snapshot; tick counts are not exchange-traded volume.')
    return png, caption
