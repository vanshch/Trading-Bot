"""Asynchronous Telegram alert dispatcher with photo attachments and retry backoff."""

import asyncio
import html
import io
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp

from src.config import TelegramConfig, mask_secret
from src.state_tracker import TradeInfo

logger = logging.getLogger("trade_bot.notifier")


class TelegramNotifier:
    """Dispatches trade entry and exit alerts to Telegram with screenshots."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        config: TelegramConfig | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.bot_token = bot_token.strip()
        self.chat_id = str(chat_id).strip()
        self.config = config or TelegramConfig()
        self._session = session
        self._owns_session = session is None

        # Masked token for safe logging
        self._masked_token = mask_secret(self.bot_token)

        # Set display timezone
        tz_name = (self.config.caption_timezone or "UTC").strip()
        if tz_name.upper() in ("UTC", "GMT"):
            self._tz = timezone.utc
        else:
            try:
                self._tz = ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, ValueError):
                logger.warning(
                    f"Could not load timezone '{tz_name}', falling back to UTC."
                )
                self._tz = timezone.utc

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the underlying aiohttp ClientSession if owned."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    def _format_timestamp(self, dt: datetime) -> str:
        """Format UTC datetime into localized string."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        try:
            local_dt = dt.astimezone(self._tz)
            tz_abbr = local_dt.strftime("%Z") or self.config.caption_timezone
            return local_dt.strftime(f"%Y-%m-%d %H:%M:%S {tz_abbr}")
        except (OverflowError, OSError, ValueError):
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

    def _format_direction(self, direction: str) -> str:
        d = direction.upper()
        if d == "BUY":
            return "📈 BUY (Long)"
        elif d == "SELL":
            return "📉 SELL (Short)"
        return "⚖️ NONE"

    def format_new_trade_message(self, trade: TradeInfo, stream_url: str) -> str:
        """Construct formatted alert message for a newly detected trade entry."""
        time_str = self._format_timestamp(trade.opened_at)
        direction_str = self._format_direction(trade.direction)
        details = (
            trade.entry_or_pnl
            if trade.entry_or_pnl
            else "Position marker detected on chart"
        )
        confidence_pct = int(trade.confidence * 100)

        lines = [
            f"🚨 <b>TRADE DETECTED: {html.escape(trade.channel_name)}</b>",
            "",
            f"📊 <b>Instrument:</b> <code>{html.escape(trade.instrument)}</code>",
            f"🎯 <b>Direction:</b> {direction_str}",
            f"💰 <b>Details:</b> {html.escape(details)}",
            f"🎯 <b>Confidence:</b> {confidence_pct}%",
            f"⏰ <b>Timestamp:</b> {html.escape(time_str)}",
            f'🔗 <b>Stream:</b> <a href="{html.escape(stream_url, quote=True)}">Watch Live</a>',
        ]
        if trade.reasoning:
            lines.append(f"💡 <b>Note:</b> <i>{html.escape(trade.reasoning)}</i>")

        return "\n".join(lines)

    def format_trade_closed_message(self, trade: TradeInfo, stream_url: str) -> str:
        """Construct formatted alert message when an active trade closes."""
        time_str = self._format_timestamp(trade.closed_at or datetime.now(timezone.utc))
        direction_str = self._format_direction(trade.direction)

        lines = [
            f"🏁 <b>TRADE CLOSED: {html.escape(trade.channel_name)}</b>",
            "",
            f"📊 <b>Instrument:</b> <code>{html.escape(trade.instrument)}</code>",
            f"🎯 <b>Direction:</b> {direction_str}",
            f"⏱️ <b>Duration:</b> {html.escape(trade.formatted_duration)}",
            f"⏰ <b>Closed At:</b> {html.escape(time_str)}",
            f'🔗 <b>Stream:</b> <a href="{html.escape(stream_url, quote=True)}">Watch Live</a>',
        ]
        return "\n".join(lines)

    async def _post_telegram_request(
        self,
        endpoint: str,
        data: Any = None,
        form_data_factory: Callable[[], aiohttp.FormData] | None = None,
    ) -> bool:
        """Execute a POST request to Telegram Bot API with retries and backoff."""
        url = f"https://api.telegram.org/bot{self.bot_token}/{endpoint}"
        session = await self._get_session()
        delay = self.config.retry_delay_seconds

        for attempt in range(1, self.config.max_retries + 1):
            try:
                if form_data_factory is not None:
                    async with session.post(url, data=form_data_factory()) as resp:
                        if resp.status == 200:
                            return True
                        body = await resp.text()
                        logger.warning(
                            "Telegram API %s returned HTTP %s (attempt %s/%s): %s",
                            endpoint,
                            resp.status,
                            attempt,
                            self.config.max_retries,
                            body.replace("\n", " ")[:200],
                        )
                else:
                    async with session.post(url, json=data) as resp:
                        if resp.status == 200:
                            return True
                        body = await resp.text()
                        logger.warning(
                            "Telegram API %s returned HTTP %s (attempt %s/%s): %s",
                            endpoint,
                            resp.status,
                            attempt,
                            self.config.max_retries,
                            body.replace("\n", " ")[:200],
                        )

                if 400 <= resp.status < 500 and resp.status != 429:
                    logger.error(
                        "Telegram request rejected permanently; retries suppressed"
                    )
                    return False

            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                logger.warning(
                    "Telegram network error on %s (attempt %s/%s): %s",
                    endpoint,
                    attempt,
                    self.config.max_retries,
                    type(exc).__name__,
                )

            if attempt < self.config.max_retries:
                await asyncio.sleep(delay)
                delay *= 2.0

        logger.error(
            f"Failed to dispatch Telegram message to chat {self.chat_id} after {self.config.max_retries} attempts."
        )
        return False

    async def check_connection(self) -> bool:
        """Verify that the configured bot token is accepted by Telegram."""
        return await self._post_telegram_request("getMe")

    async def send_new_trade_alert(
        self, trade: TradeInfo, stream_url: str, frame_bytes: bytes | None = None
    ) -> bool:
        """Send a new trade alert message, optionally with the captured frame photo."""
        text = self.format_new_trade_message(trade, stream_url)
        img_bytes = frame_bytes or trade.last_frame_bytes

        if self.config.include_screenshot and img_bytes:

            def build_form() -> aiohttp.FormData:
                form = aiohttp.FormData()
                form.add_field("chat_id", self.chat_id)
                form.add_field("caption", text)
                form.add_field("parse_mode", "HTML")
                form.add_field(
                    "photo",
                    io.BytesIO(img_bytes),
                    filename="trade_frame.jpg",
                    content_type="image/jpeg",
                )
                return form

            return await self._post_telegram_request(
                "sendPhoto", form_data_factory=build_form
            )
        else:
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
            return await self._post_telegram_request("sendMessage", data=payload)

    def format_fast_audio_trade_message(
        self,
        channel_name: str,
        verification: Any,  # MultimodalTradeVerification
        stream_url: str,
        elapsed_seconds: float | None = None,
    ) -> str:
        """Construct fast minimalist alert for an audio-triggered multimodal trade."""
        now = datetime.now(timezone.utc)
        time_str = self._format_timestamp(now)
        direction = getattr(verification, "direction", "BUY").upper()

        if direction in ("CALL", "CE"):
            direction_badge = "🟢 CALL (CE)"
        elif direction in ("PUT", "PE"):
            direction_badge = "🔴 PUT (PE)"
        elif direction == "BUY":
            direction_badge = "📈 BUY (Long)"
        elif direction == "SELL":
            direction_badge = "📉 SELL (Short)"
        else:
            direction_badge = "⚡ " + direction

        instrument = getattr(verification, "instrument", "UNKNOWN")
        quote = getattr(verification, "trigger_quote", "")
        entry = getattr(verification, "entry_price", None)
        sl = getattr(verification, "stop_loss", None)
        target = getattr(verification, "target", None)
        confidence = int(getattr(verification, "confidence", 1.0) * 100)
        reasoning = getattr(verification, "reasoning", "")

        # Format stream link with timestamp if available
        stream_link = stream_url
        if elapsed_seconds is not None and elapsed_seconds > 0:
            h = int(elapsed_seconds // 3600)
            m = int((elapsed_seconds % 3600) // 60)
            s = int(elapsed_seconds % 60)
            t_param = f"{h}h{m}m{s}s" if h > 0 else f"{m}m{s}s"
            delim = "&" if "?" in stream_url else "?"
            stream_link = f"{stream_url}{delim}t={t_param}"

        lines = [
            f"⚡ <b>LIVE TRADE SIGNAL: {html.escape(channel_name)}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🎯 <b>Instrument:</b> <code>{html.escape(instrument)}</code>",
            f"📊 <b>Direction:</b> {direction_badge}",
        ]

        if entry:
            lines.append(f"💵 <b>Entry:</b> <code>{html.escape(entry)}</code>")
        if sl or target:
            sl_text = f"SL: {html.escape(sl)}" if sl else ""
            tgt_text = f"TGT: {html.escape(target)}" if target else ""
            lines.append(f"🛡️ <b>Levels:</b> <code>{' | '.join(filter(None, [sl_text, tgt_text]))}</code>")

        if quote:
            lines.append(f'🗣️ <b>Spoken Trigger:</b> <i>"{html.escape(quote)}"</i>')

        lines.extend([
            f"⏰ <b>Time:</b> {html.escape(time_str)} (Conf: {confidence}%)",
            f'🔗 <b>Live Stream:</b> <a href="{html.escape(stream_link, quote=True)}">Watch at timestamp</a>',
        ])

        if reasoning:
            lines.append(f"💡 <i>{html.escape(reasoning)}</i>")

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    async def send_fast_audio_trade_alert(
        self,
        channel_name: str,
        verification: Any,
        stream_url: str,
        frame_bytes: bytes | None = None,
        elapsed_seconds: float | None = None,
    ) -> bool:
        """Send a fast minimalist trade alert with live screenshot and stream timestamp link."""
        text = self.format_fast_audio_trade_message(
            channel_name=channel_name,
            verification=verification,
            stream_url=stream_url,
            elapsed_seconds=elapsed_seconds,
        )

        if self.config.include_screenshot and frame_bytes:

            def build_form() -> aiohttp.FormData:
                form = aiohttp.FormData()
                form.add_field("chat_id", self.chat_id)
                form.add_field("caption", text)
                form.add_field("parse_mode", "HTML")
                form.add_field(
                    "photo",
                    io.BytesIO(frame_bytes),
                    filename="live_trade.jpg",
                    content_type="image/jpeg",
                )
                return form

            return await self._post_telegram_request(
                "sendPhoto", form_data_factory=build_form
            )
        else:
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
            return await self._post_telegram_request("sendMessage", data=payload)

    async def send_trade_closed_alert(self, trade: TradeInfo, stream_url: str) -> bool:
        """Send a trade closed alert message."""
        text = self.format_trade_closed_message(trade, stream_url)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        return await self._post_telegram_request("sendMessage", data=payload)

    def format_stream_live_message(
        self,
        channel_name: str,
        stream_url: str,
        title: str = "",
    ) -> str:
        """Construct formatted alert message when a channel starts streaming live."""
        now = datetime.now(timezone.utc)
        time_str = self._format_timestamp(now)
        lines = [
            f"🔴 <b>STREAM IS LIVE: {html.escape(channel_name)}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
        ]
        if title:
            lines.append(f"📺 <b>Title:</b> {html.escape(title)}")
        lines.extend([
            f"⏰ <b>Started At:</b> {html.escape(time_str)}",
            f'🔗 <b>Watch Stream:</b> <a href="{html.escape(stream_url, quote=True)}">Open YouTube Live</a>',
            "",
            "🤖 <i>Trade bot listener is actively monitoring audio & charts.</i>",
            "━━━━━━━━━━━━━━━━━━━━",
        ])
        return "\n".join(lines)

    async def send_stream_live_alert(
        self,
        channel_name: str,
        stream_url: str,
        title: str = "",
    ) -> bool:
        """Send a notification that the channel has gone live."""
        text = self.format_stream_live_message(
            channel_name=channel_name,
            stream_url=stream_url,
            title=title,
        )
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        return await self._post_telegram_request("sendMessage", data=payload)

    def format_stream_ended_message(
        self,
        channel_name: str,
        stream_url: str,
    ) -> str:
        """Construct formatted alert message when a channel live stream concludes."""
        now = datetime.now(timezone.utc)
        time_str = self._format_timestamp(now)
        lines = [
            f"⏹️ <b>STREAM ENDED: {html.escape(channel_name)}</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"⏰ <b>Ended At:</b> {html.escape(time_str)}",
            f'🔗 <b>Channel:</b> <a href="{html.escape(stream_url, quote=True)}">YouTube Channel</a>',
            "━━━━━━━━━━━━━━━━━━━━",
        ]
        return "\n".join(lines)

    async def send_stream_ended_alert(
        self,
        channel_name: str,
        stream_url: str,
    ) -> bool:
        """Send a notification that the channel live stream has ended."""
        text = self.format_stream_ended_message(
            channel_name=channel_name,
            stream_url=stream_url,
        )
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        return await self._post_telegram_request("sendMessage", data=payload)
