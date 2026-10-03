"""Optional remote TradingView capture; no local browser or market-feed changes."""
import asyncio
import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from config import logger

INTERVALS = {"5m": "5", "15m": "15", "1h": "60", "4h": "240"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_RESPONSE_BYTES = ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 1024
CAPTURE_TIMEOUT = 60

# Runs exclusively on Browserless. Readiness checks deliberately avoid a fixed price.
CAPTURE_CODE = r"""
export default async ({ page, context }) => {
  await page.setViewport({width:1440, height:900, deviceScaleFactor:1});
  const nav = await page.goto(context.url, {waitUntil:'domcontentloaded', timeout:25000});
  if (!nav || nav.status() >= 400) throw new Error('Chart navigation failed');
  await page.waitForFunction((intervals) => {
    const text = document.body.innerText;
    const lines = text.split('\n').map(s => s.trim());
    const correctHeader = lines.some((line, i) => line === 'OANDA' &&
      intervals.includes(lines[i - 1]) && lines[i - 2] === 'Gold Spot / U.S. Dollar');
    return window.is_authenticated === true && document.title.includes('XAUUSD') &&
      correctHeader &&
      [...document.querySelectorAll('canvas')].some(c => c.width > 400 && c.height > 200);
  }, {timeout:20000}, context.intervalLabels);
  await new Promise(r => setTimeout(r, 2500));
  // The hosted function runs in a browser sandbox without Node's Buffer global.
  return {data:{screenshotBase64:await page.screenshot({type:'png', encoding:'base64'})},
          type:'application/json'};
};
"""


@dataclass(frozen=True)
class ScreenshotSettings:
    enabled: bool = False
    token: str = field(default="", repr=False)
    endpoint: str = "https://production-sfo.browserless.io"
    profile: str = "tradingview-test"
    chart_url: str = "https://www.tradingview.com/chart/"

    def validate(self):
        endpoint = urlsplit(self.endpoint)
        chart = urlsplit(self.chart_url)
        if not self.token or not self.profile:
            raise ValueError("Browserless token and profile are required")
        if (endpoint.scheme != "https" or not endpoint.hostname or
                not endpoint.hostname.endswith(".browserless.io") or
                endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment or
                endpoint.username or endpoint.password or endpoint.port):
            raise ValueError("Use an HTTPS Browserless regional origin")
        if (chart.scheme != "https" or chart.hostname != "www.tradingview.com" or
                not chart.path.startswith("/chart/") or chart.username or chart.password or chart.port):
            raise ValueError("Use an HTTPS TradingView chart URL")


def configured_settings():
    import config
    return ScreenshotSettings(
        enabled=config.CHART_SCREENSHOTS_ENABLED,
        token=config.BROWSERLESS_API_TOKEN,
        endpoint=config.BROWSERLESS_ENDPOINT,
        profile=config.BROWSERLESS_PROFILE,
        chart_url=config.TRADINGVIEW_CHART_URL,
    )


def chart_url_for_timeframe(url: str, timeframe: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update(symbol="OANDA:XAUUSD", interval=INTERVALS[timeframe])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


async def capture_chart(settings: ScreenshotSettings, timeframe: str, *, transport=None) -> bytes:
    settings.validate()
    chart_url = chart_url_for_timeframe(settings.chart_url, timeframe)
    # Header authentication prevents the API token appearing in HTTP request URLs/logs.
    async with httpx.AsyncClient(timeout=CAPTURE_TIMEOUT, transport=transport) as client:
        async with client.stream(
            "POST", settings.endpoint.rstrip("/") + "/function",
            params={"profile": settings.profile, "timeout": "55000"},
            headers={"Authorization": f"Bearer {settings.token}"},
            json={"code": CAPTURE_CODE, "context": {
                "url": chart_url, "intervalLabels": [INTERVALS[timeframe], timeframe],
            }},
        ) as response:
            if response.status_code != 200:
                raise ValueError(f"Browserless HTTP {response.status_code}")
            if response.headers.get("content-type", "").split(";")[0] != "application/json":
                raise ValueError("Browserless did not return a chart payload")
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                if len(payload) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ValueError("Browserless chart exceeds response size limit")
                payload.extend(chunk)
    data = json.loads(payload)
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if not isinstance(data, dict) or not isinstance(data.get("screenshotBase64"), str):
        raise ValueError("Browserless chart payload is missing")
    image = base64.b64decode(data["screenshotBase64"], validate=True)
    if len(image) > MAX_IMAGE_BYTES:
        raise ValueError("Browserless chart exceeds image size limit")
    if not image.startswith(PNG_SIGNATURE) or len(image) <= len(PNG_SIGNATURE):
        raise ValueError("Browserless returned an invalid PNG")
    return bytes(image)


async def send_alert_chart(bot, timeframe: str, candle_time: str, feed_source: str,
                           *, settings=None) -> bool:
    """Best effort, once per alerted candle, after all primary text messages."""
    import config
    settings = settings or configured_settings()
    if not settings.enabled:
        return False
    try:
        image = await asyncio.wait_for(capture_chart(settings, timeframe), timeout=CAPTURE_TIMEOUT)
        captured_at = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        caption = (
            f"OANDA XAU/USD · {timeframe} · TradingView visual context\n"
            f"Alert candle close: {candle_time}\n"
            f"Captured: {captured_at} (may include a forming candle)\n"
            f"Alert feed: {feed_source}\n"
            "Chart quotes may differ from the alert feed."
        )
        # Plain text avoids Markdown injection; conservative limit covers UTF-16 too.
        await asyncio.wait_for(bot.send_photo(
            chat_id=config.ALLOWED_USER_ID,
            photo=BytesIO(image), filename="tradingview-xauusd.png", caption=caption[:500],
        ), timeout=20)
        return True
    except Exception as exc:
        # Exception strings/response bodies can contain credentials; log the type only.
        logger.warning("Optional TradingView chart skipped (%s); text alert unaffected", type(exc).__name__)
        return False
