"""Bounded, user-requested photos independent of automatic alert settings."""
import asyncio
from datetime import datetime, timezone
from io import BytesIO
from telegram.error import NetworkError

import config
from config import logger
from hourly_chart import HourlyHistoryUnavailable, capture_hourly_chart
from screenshots import capture_chart, configured_settings, CAPTURE_TIMEOUT


async def request_chart(context, kind, timeframe):
    """Reserve before task creation so repeated taps never queue captures."""
    state = context.application.bot_data
    if state.get('manual_chart_busy'):
        await _send_notice(context.bot, 'A chart is already being prepared. Please wait.')
        return
    state['manual_chart_busy'] = True
    task = deliver_chart(context.bot, state, kind, timeframe, announce=True)
    try:
        context.application.create_task(task)
    except BaseException:
        task.close()
        state['manual_chart_busy'] = False
        raise


async def _send_notice(bot, text):
    """Retry informational text once; uncertain photo sends are never repeated."""
    for attempt in range(2):
        try:
            await asyncio.wait_for(bot.send_message(config.ALLOWED_USER_ID, text), timeout=5)
            return True
        except (NetworkError, asyncio.TimeoutError) as exc:
            logger.warning('Chart notice delivery failed (%s), attempt %s/2',
                           type(exc).__name__, attempt + 1)
            if attempt == 0:
                await asyncio.sleep(1)
        except Exception as exc:
            logger.warning('Chart notice delivery failed (%s)', type(exc).__name__)
            return False
    return False


async def _capture_hourly():
    for attempt in range(2):
        try:
            return await capture_hourly_chart()
        except (HourlyHistoryUnavailable, asyncio.TimeoutError):
            if attempt:
                raise
            logger.warning('Verified hourly chart data unavailable; retrying once')
            await asyncio.sleep(1)


async def deliver_chart(bot, state, kind, timeframe, *, announce=False):
    phase = 'capture'
    try:
        if announce:
            await _send_notice(bot, 'Preparing the 1H chart…' if kind == 'hourly'
                               else 'Capturing the screenshot now…')
        if kind == 'hourly':
            image, caption = await asyncio.wait_for(_capture_hourly(), timeout=55)
            filename = 'oanda-xauusd-1h-volume.png'
        else:
            # The automatic screenshot flag does not disable an explicit request.
            image = await asyncio.wait_for(capture_chart(configured_settings(), timeframe), timeout=CAPTURE_TIMEOUT)
            caption = (f'OANDA XAU/USD · {timeframe} · Requested TradingView screenshot\n'
                       f'Captured: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC\n'
                       'Visual context; may include a forming candle.')
            filename = 'tradingview-xauusd.png'
        phase = 'delivery'
        await asyncio.wait_for(bot.send_photo(chat_id=config.ALLOWED_USER_ID,
            photo=BytesIO(image), filename=filename, caption=caption), timeout=20)
    except Exception as exc:
        logger.warning('Requested %s chart failed (%s)', kind, type(exc).__name__)
        if phase == 'delivery':
            message = ('Chart delivery could not be confirmed. It may still arrive; '
                       'please request it again if needed.')
        elif kind == 'hourly' and isinstance(exc, (HourlyHistoryUnavailable, asyncio.TimeoutError)):
            message = ('Verified OANDA hourly data is temporarily unavailable. '
                       'Please try /chart1h again shortly. No fallback volume was used.')
        elif kind == 'hourly':
            message = 'The 1H chart could not be prepared or delivered. Please try /chart1h again.'
        else:
            message = 'The screenshot could not be captured or delivered. Please try /screenshot again.'
        await _send_notice(bot, message)
    finally:
        state['manual_chart_busy'] = False
