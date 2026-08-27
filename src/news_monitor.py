"""Forex Factory High-Impact (Red Folder) News Monitor and Scheduled Telegram Alert Dispatcher.

Decoupled worker that queries the Fair Economy / Forex Factory calendar feed,
filters today's High-Impact (Red Folder) events in the configured timezone (e.g. Asia/Kolkata),
and delivers scheduled HTML briefs to Telegram (default 08:00 and 14:00 IST).
"""

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp

from src.config import NewsConfig
from src.notifier import TelegramNotifier

logger = logging.getLogger("trade_bot.news")

# Country / Currency Flag Badges
CURRENCY_FLAGS: dict[str, str] = {
    "USD": "🇺🇸",
    "EUR": "🇪🇺",
    "GBP": "🇬🇧",
    "JPY": "🇯🇵",
    "CAD": "🇨🇦",
    "AUD": "🇦🇺",
    "NZD": "🇳🇿",
    "CHF": "🇨🇭",
    "CNY": "🇨🇳",
    "INR": "🇮🇳",
    "XAU": "🥇",
    "OIL": "🛢️",
}


@dataclass(frozen=True)
class ForexNewsEvent:
    """Individual economic calendar event from Forex Factory."""

    title: str
    country: str
    date_utc: datetime
    impact: str
    forecast: str = ""
    previous: str = ""


class ForexNewsFetcher:
    """Async HTTP client to fetch and parse Forex Factory weekly calendar events."""

    def __init__(
        self,
        feed_url: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        timeout_seconds: float = 15.0,
        max_retries: int = 3,
        retry_delay_seconds: float = 3.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.feed_url = feed_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close underlying HTTP session if owned."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def parse_event_datetime(date_str: str) -> datetime | None:
        """Parse ISO 8601 date string to UTC datetime."""
        if not date_str or not isinstance(date_str, str):
            return None
        cleaned = date_str.strip()
        try:
            # Handle standard ISO-8601 formats (e.g. 2026-08-27T08:30:00-04:00 or 2026-08-27T12:30:00Z)
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError) as exc:
            logger.warning(f"Failed to parse news event datetime '{date_str}': {exc}")
            return None

    async def fetch_events(self) -> list[ForexNewsEvent]:
        """Fetch raw JSON from Forex Factory and parse into structured ForexNewsEvent objects."""
        session = await self._get_session()
        delay = self.retry_delay_seconds

        for attempt in range(1, self.max_retries + 1):
            try:
                async with session.get(self.feed_url) as resp:
                    if resp.status == 200:
                        raw_data = await resp.json(content_type=None)
                        if not isinstance(raw_data, list):
                            logger.error(
                                f"Unexpected Forex Factory JSON format: expected list, got {type(raw_data).__name__}"
                            )
                            return []

                        events: list[ForexNewsEvent] = []
                        for item in raw_data:
                            if not isinstance(item, dict):
                                continue
                            dt_utc = self.parse_event_datetime(item.get("date", ""))
                            if dt_utc is None:
                                continue

                            event = ForexNewsEvent(
                                title=str(item.get("title", "")).strip(),
                                country=str(item.get("country", "")).strip().upper(),
                                date_utc=dt_utc,
                                impact=str(item.get("impact", "")).strip(),
                                forecast=str(item.get("forecast", "")).strip(),
                                previous=str(item.get("previous", "")).strip(),
                            )
                            events.append(event)

                        logger.info(
                            f"Successfully fetched {len(events)} calendar events from Forex Factory"
                        )
                        return events

                    body = await resp.text()
                    logger.warning(
                        f"Forex Factory feed returned HTTP {resp.status} (attempt {attempt}/{self.max_retries}): {body[:150]}"
                    )

            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                logger.warning(
                    f"Forex Factory network error on attempt {attempt}/{self.max_retries}: {type(exc).__name__} ({exc})"
                )

            if attempt < self.max_retries:
                await asyncio.sleep(delay)
                delay *= 2.0

        logger.error(
            f"Failed to fetch Forex Factory calendar events after {self.max_retries} attempts"
        )
        return []


def filter_events_for_day(
    events: list[ForexNewsEvent],
    target_date: date,
    target_tz: ZoneInfo,
    impact_levels: list[str] | set[str] | None = None,
    currencies: list[str] | set[str] | None = None,
) -> list[ForexNewsEvent]:
    """Filter news events for a specific calendar date and impact rating in target timezone."""
    impact_filter = (
        {lvl.strip().lower() for lvl in impact_levels}
        if impact_levels
        else {"high"}
    )
    currency_filter = (
        {c.strip().upper() for c in currencies if c.strip()}
        if currencies
        else None
    )

    matched: list[ForexNewsEvent] = []
    for event in events:
        if event.impact.lower() not in impact_filter:
            continue
        if currency_filter and event.country not in currency_filter:
            continue

        local_dt = event.date_utc.astimezone(target_tz)
        if local_dt.date() == target_date:
            matched.append(event)

    # Sort chronologically by event time
    matched.sort(key=lambda e: e.date_utc)
    return matched


def format_daily_news_message(
    events: list[ForexNewsEvent],
    target_date: date,
    target_tz: ZoneInfo,
    session_label: str = "Daily Brief",
) -> str:
    """Construct formatted Telegram HTML message for daily high-impact economic news."""
    tz_abbr = target_tz.key if hasattr(target_tz, "key") else "IST"
    date_str = target_date.strftime("%A, %d %b %Y")
    label_text = f" ({session_label})" if session_label else ""

    lines = [
        "🔴 <b>FOREX FACTORY: HIGH-IMPACT NEWS</b>",
        f"📅 <b>Date:</b> {html.escape(date_str)}{html.escape(label_text)}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    if not events:
        lines.extend([
            "✅ <i>No Red Folder (High-Impact) news events scheduled for today.</i>",
            "━━━━━━━━━━━━━━━━━━━━",
            "🛡️ <i>Regular trading conditions expected.</i>",
        ])
        return "\n".join(lines)

    for i, event in enumerate(events):
        local_dt = event.date_utc.astimezone(target_tz)
        time_str = local_dt.strftime("%I:%M %p")
        flag = CURRENCY_FLAGS.get(event.country, "🌐")

        lines.append(f"⏰ <b>{time_str} {tz_abbr}</b> | {flag} <b>{html.escape(event.country)}</b>")
        lines.append(f"📌 <b>{html.escape(event.title)}</b>")

        details: list[str] = []
        if event.forecast:
            details.append(f"<i>Forecast:</i> <code>{html.escape(event.forecast)}</code>")
        if event.previous:
            details.append(f"<i>Previous:</i> <code>{html.escape(event.previous)}</code>")

        if details:
            lines.append(f"📊 {' | '.join(details)}")
        else:
            lines.append("📊 <i>Impact:</i> <b>High (Red Folder)</b>")

        if i < len(events) - 1:
            lines.append("────────────────────")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Expect heightened market volatility and spread widening around release times.</i>",
    ])
    return "\n".join(lines)


def compute_seconds_until_next_run(
    now_dt: datetime,
    schedule_times: list[str],
    target_tz: ZoneInfo,
) -> tuple[float, str]:
    """Compute the seconds to sleep from now_dt until the next scheduled target time in target_tz.

    Returns:
        tuple[float, str]: (seconds_to_wait, next_scheduled_time_str)
    """
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    local_now = now_dt.astimezone(target_tz)

    candidate_dts: list[tuple[datetime, str]] = []
    for t_str in schedule_times:
        h, m = [int(x) for x in t_str.split(":")]
        today_cand = local_now.replace(
            hour=h, minute=m, second=0, microsecond=0
        )
        if today_cand > local_now:
            candidate_dts.append((today_cand, t_str))
        else:
            tomorrow_cand = (local_now + timedelta(days=1)).replace(
                hour=h, minute=m, second=0, microsecond=0
            )
            candidate_dts.append((tomorrow_cand, t_str))

    candidate_dts.sort(key=lambda x: x[0])
    next_dt, next_time_str = candidate_dts[0]
    delay = max(1.0, (next_dt - local_now).total_seconds())
    return delay, next_time_str


class ForexNewsService:
    """Background service managing scheduled daily news updates to Telegram with decoupled isolation."""

    def __init__(
        self,
        config: NewsConfig,
        notifier: TelegramNotifier,
        fetcher: ForexNewsFetcher | None = None,
    ) -> None:
        self.config = config
        self.notifier = notifier
        self.fetcher = fetcher or ForexNewsFetcher(
            feed_url=config.feed_url,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
            retry_delay_seconds=config.retry_delay_seconds,
        )

        try:
            self.tz = ZoneInfo(config.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(
                f"Invalid timezone '{config.timezone}', falling back to Asia/Kolkata"
            )
            self.tz = ZoneInfo("Asia/Kolkata")

    async def close(self) -> None:
        """Close underlying resources."""
        await self.fetcher.close()

    async def dispatch_daily_news_update(self, session_label: str = "") -> bool:
        """Fetch today's events, format HTML digest, and dispatch to Telegram."""
        now_utc = datetime.now(timezone.utc)
        today_local = now_utc.astimezone(self.tz).date()

        logger.info(
            f"Fetching Forex Factory news for {today_local} ({self.tz})..."
        )
        events = await self.fetcher.fetch_events()
        filtered = filter_events_for_day(
            events=events,
            target_date=today_local,
            target_tz=self.tz,
            impact_levels=self.config.impact_levels,
            currencies=self.config.currencies,
        )

        message = format_daily_news_message(
            events=filtered,
            target_date=today_local,
            target_tz=self.tz,
            session_label=session_label,
        )

        success = await self.notifier.send_news_alert(message)
        if success:
            logger.info(
                f"Dispatched Forex Factory news update to Telegram ({len(filtered)} high-impact event(s) reported)"
            )
        else:
            logger.error("Failed to deliver Forex Factory news alert to Telegram")
        return success

    async def run(self, stop_event: asyncio.Event) -> None:
        """Continuously loop and dispatch alerts at configured schedule times."""
        if not self.config.enabled:
            logger.info("Forex Factory news monitoring is disabled in configuration.")
            return

        logger.info(
            f"Forex Factory news monitor started. Scheduled times ({self.tz}): {', '.join(self.config.schedule_times)}"
        )

        while not stop_event.is_set():
            now_utc = datetime.now(timezone.utc)
            delay, next_time = compute_seconds_until_next_run(
                now_dt=now_utc,
                schedule_times=self.config.schedule_times,
                target_tz=self.tz,
            )

            logger.info(
                f"Next Forex Factory news dispatch scheduled at {next_time} {self.tz} (in {delay:.1f}s / {delay / 60:.1f}m)"
            )

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                # Stop event was set
                break
            except asyncio.TimeoutError:
                # Scheduled time arrived!
                pass

            if stop_event.is_set():
                break

            # Isolated execution boundary: failure here never crashes orchestrator
            try:
                session_label = f"Schedule {next_time}"
                await self.dispatch_daily_news_update(session_label=session_label)
            except Exception:
                logger.exception(
                    "Unexpected error during Forex Factory news dispatch"
                )

            # Short cooldown to prevent duplicate triggers within the same minute
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

        logger.info("Forex Factory news service stopped cleanly.")
