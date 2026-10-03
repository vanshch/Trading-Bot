"""One-shot device test without polling Telegram or changing alert state."""
import argparse
import asyncio
from dataclasses import replace
from pathlib import Path

from telegram import Bot

import config
from screenshots import capture_chart, configured_settings, send_alert_chart, CAPTURE_TIMEOUT


async def run(args):
    settings = replace(configured_settings(), enabled=True)
    if args.send:
        async with Bot(config.TELEGRAM_BOT_TOKEN) as bot:
            await bot.send_message(
                chat_id=config.ALLOWED_USER_ID,
                text="TEST ONLY: Browserless chart delivery check. No market alert was triggered.",
            )
            success = await send_alert_chart(bot, args.timeframe, "TEST ONLY", "Device test", settings=settings)
            if not success:
                raise RuntimeError("Chart delivery failed; check the sanitized log")
        print("Telegram accepted the test text and chart. Confirm both arrived on your device.")
    else:
        image = await asyncio.wait_for(capture_chart(settings, args.timeframe), CAPTURE_TIMEOUT)
        path = Path(args.output).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image)
        print(f"Saved chart: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Send labeled test text and photo to ALLOWED_USER_ID")
    parser.add_argument("--timeframe", choices=config.VALID_TIMEFRAMES, default=config.DEFAULT_TIMEFRAME)
    parser.add_argument("--output", default=".chart-artifacts/tradingview-xauusd.png")
    try:
        asyncio.run(run(parser.parse_args()))
    except Exception as exc:
        # Do not expose HTTP exceptions containing headers/credentials.
        print(f"Chart test failed ({type(exc).__name__}). Check token, profile and chart access.")
        raise SystemExit(1)
