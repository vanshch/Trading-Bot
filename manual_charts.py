"""Bounded, user-requested photos independent of automatic alert settings."""
import asyncio
import time
from datetime import datetime, timezone
from io import BytesIO

import config
from config import logger
from hourly_chart import capture_hourly_chart
from screenshots import capture_chart, configured_settings, CAPTURE_TIMEOUT

COOLDOWN_SECONDS = 30


async def request_chart(context, kind, timeframe):
    """Reserve before task creation so repeated taps never queue captures."""
    state = context.application.bot_data
    now = time.monotonic()
    if state.get('manual_chart_busy'):
        await context.bot.send_message(config.ALLOWED_USER_ID, 'A chart is already being prepared. Please wait.')
        return
    if now < state.get('manual_chart_next', 0):
        await context.bot.send_message(config.ALLOWED_USER_ID, 'Please wait 30 seconds between chart requests.')
        return
    state['manual_chart_busy'] = True
    state['manual_chart_next'] = now + COOLDOWN_SECONDS
    try:
        await context.bot.send_message(config.ALLOWED_USER_ID,
                                       'Preparing the 1H chart…' if kind == 'hourly' else 'Capturing the screenshot now…')
        context.application.create_task(deliver_chart(context.bot, state, kind, timeframe))
    except BaseException:
        state['manual_chart_busy'] = False
        raise


async def deliver_chart(bot, state, kind, timeframe):
    try:
        if kind == 'hourly':
            image, caption = await asyncio.wait_for(capture_hourly_chart(), timeout=35)
            filename = 'oanda-xauusd-1h-volume.png'
        else:
            # The automatic screenshot flag does not disable an explicit request.
            image = await asyncio.wait_for(capture_chart(configured_settings(), timeframe), timeout=CAPTURE_TIMEOUT)
            caption = (f'OANDA XAU/USD · {timeframe} · Requested TradingView screenshot\n'
                       f'Captured: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC\n'
                       'Visual context; may include a forming candle.')
            filename = 'tradingview-xauusd.png'
        await asyncio.wait_for(bot.send_photo(chat_id=config.ALLOWED_USER_ID,
            photo=BytesIO(image), filename=filename, caption=caption), timeout=20)
    except Exception as exc:
        logger.warning('Requested %s chart failed (%s)', kind, type(exc).__name__)
        message = ('The 1H chart is unavailable. It needs tvkit on Python 3.11+ and 20 verified OANDA bars.'
                   if kind == 'hourly' else 'The screenshot is unavailable. Check the Browserless credentials and saved TradingView profile.')
        try:
            await asyncio.wait_for(bot.send_message(config.ALLOWED_USER_ID, message), timeout=20)
        except Exception as error:
            logger.warning('Chart failure notice failed (%s)', type(error).__name__)
    finally:
        state['manual_chart_busy'] = False
