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

# Default currency to impacted traded instruments
DEFAULT_CURRENCY_INSTRUMENTS: dict[str, list[str]] = {
    "USD": ["XAUUSD (Gold)", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "US500 / US30"],
    "EUR": ["EURUSD", "EURGBP", "EURJPY", "DAX40"],
    "GBP": ["GBPUSD", "EURGBP", "GBPJPY", "FTSE100"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY", "Nikkei 225"],
    "CAD": ["USDCAD", "CADJPY", "Crude Oil (WTI)"],
    "AUD": ["AUDUSD", "AUDJPY", "AUDNZD"],
    "NZD": ["NZDUSD", "EURNZD", "AUDNZD"],
    "CHF": ["USDCHF", "EURCHF"],
    "CNY": ["USDCNH", "China A50", "Emerging Markets"],
    "INR": ["USDINR", "Nifty 50", "BankNifty"],
    "OIL": ["USOIL", "UKOIL", "USDCAD"],
    "XAU": ["XAUUSD (Gold)", "XAGUSD (Silver)"],
}


def get_impacted_instruments(currency: str) -> str:
    """Return human-readable comma-separated list of instruments impacted by currency news."""
    instruments = DEFAULT_CURRENCY_INSTRUMENTS.get(currency.upper())
    if instruments:
        return ", ".join(instruments)
    return f"{currency.upper()} Pairs"


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


def format_pre_event_warning_message(
    events: list[ForexNewsEvent],
    target_tz: ZoneInfo,
    minutes_until: int = 30,
) -> str:
    """Format Telegram HTML countdown warning message for high-impact news releasing in ~30 mins."""
    if not events:
        return ""

    first_event = events[0]
    local_dt = first_event.date_utc.astimezone(target_tz)
    time_str = local_dt.strftime("%I:%M %p")
    tz_abbr = target_tz.key if hasattr(target_tz, "key") else "IST"
    country = first_event.country
    flag = CURRENCY_FLAGS.get(country, "🌐")
    impacted = get_impacted_instruments(country)

    lines = [
        f"🚨 <b>HIGH-IMPACT NEWS IN {minutes_until} MINUTES</b> ⏰",
        "━━━━━━━━━━━━━━━━━━━━",
        f"⏰ <b>Release Time:</b> {time_str} {tz_abbr} (in ~{minutes_until}m)",
        f"🌐 <b>Currency / Region:</b> {flag} <b>{html.escape(country)}</b>",
        f"🎯 <b>Impacted Instruments:</b> <code>{html.escape(impacted)}</code>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    if len(events) == 1:
        ev = events[0]
        lines.append(f"📌 <b>Event:</b> <b>{html.escape(ev.title)}</b>")
        details: list[str] = []
        if ev.forecast:
            details.append(f"<i>Forecast:</i> <code>{html.escape(ev.forecast)}</code>")
        if ev.previous:
            details.append(f"<i>Previous:</i> <code>{html.escape(ev.previous)}</code>")
        if details:
            lines.append(f"📊 {' | '.join(details)}")
        else:
            lines.append("📊 <i>Impact:</i> <b>High (Red Folder)</b>")
    else:
        lines.append("📌 <b>Releasing Events:</b>")
        for ev in events:
            details = []
            if ev.forecast:
                details.append(f"Forecast: <code>{html.escape(ev.forecast)}</code>")
            if ev.previous:
                details.append(f"Prev: <code>{html.escape(ev.previous)}</code>")
            detail_str = f" ({' | '.join(details)})" if details else ""
            lines.append(f"• <b>{html.escape(ev.title)}</b>{detail_str}")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Volatility warning: Expect rapid price swings and spread widening. Protect open trades or tighten stops!</i>",
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
    """Background service managing scheduled daily news updates and pre-event proximity alerts."""

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

        self._cached_events: list[ForexNewsEvent] = []
        self._cached_date: date | None = None
        self._last_fetch_utc: datetime | None = None
        self._alerted_event_keys: set[str] = set()

    async def close(self) -> None:
        """Close underlying resources."""
        await self.fetcher.close()

    @staticmethod
    def get_event_key(event: ForexNewsEvent) -> str:
        """Construct unique string identifier for deduplication."""
        return f"{event.country}:{event.title}:{event.date_utc.isoformat()}"

    async def refresh_today_events(self) -> list[ForexNewsEvent]:
        """Fetch and refresh in-memory cache of today's high-impact news events."""
        now_utc = datetime.now(timezone.utc)
        today_local = now_utc.astimezone(self.tz).date()

        # If calendar day changed, prune past alerted keys
        if self._cached_date is not None and self._cached_date != today_local:
            self._alerted_event_keys.clear()

        events = await self.fetcher.fetch_events()
        filtered = filter_events_for_day(
            events=events,
            target_date=today_local,
            target_tz=self.tz,
            impact_levels=self.config.impact_levels,
            currencies=self.config.currencies,
        )
        self._cached_events = filtered
        self._cached_date = today_local
        self._last_fetch_utc = now_utc
        return filtered

    async def check_and_dispatch_pre_event_alerts(
        self, now_utc: datetime | None = None
    ) -> list[str]:
        """Check cached events and dispatch proximity warnings for events releasing in ~30 mins."""
        if not self.config.pre_event_alert_enabled:
            return []

        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        lead_seconds = self.config.pre_event_minutes * 60.0
        due_groups: dict[tuple[datetime, str], list[ForexNewsEvent]] = {}

        for event in self._cached_events:
            event_key = self.get_event_key(event)
            if event_key in self._alerted_event_keys:
                continue

            time_until = (event.date_utc - now_utc).total_seconds()
            # If event is already in the past, mark as processed and skip
            if time_until <= 0:
                self._alerted_event_keys.add(event_key)
                continue

            # Check if within lead window (e.g. <= 30 minutes)
            if time_until <= lead_seconds:
                group_key = (event.date_utc, event.country)
                due_groups.setdefault(group_key, []).append(event)

        dispatched_keys: list[str] = []
        for (event_dt, country), group_events in due_groups.items():
            mins_remaining = max(
                1, round((event_dt - now_utc).total_seconds() / 60)
            )
            message = format_pre_event_warning_message(
                events=group_events,
                target_tz=self.tz,
                minutes_until=mins_remaining,
            )
            success = await self.notifier.send_news_alert(message)
            if success:
                logger.info(
                    f"Dispatched {mins_remaining}m pre-event warning to Telegram for {len(group_events)} {country} release(s)"
                )
                for ev in group_events:
                    k = self.get_event_key(ev)
                    self._alerted_event_keys.add(k)
                    dispatched_keys.append(k)
            else:
                logger.error(
                    f"Failed to deliver pre-event warning to Telegram for {country} news at {event_dt.isoformat()}"
                )

        return dispatched_keys

    async def dispatch_daily_news_update(self, session_label: str = "") -> bool:
        """Fetch today's events, format HTML digest, and dispatch to Telegram."""
        now_utc = datetime.now(timezone.utc)
        today_local = now_utc.astimezone(self.tz).date()

        logger.info(
            f"Fetching Forex Factory news for {today_local} ({self.tz})..."
        )
        filtered = await self.refresh_today_events()

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
        """Continuously loop and dispatch scheduled daily briefs and pre-event proximity warnings."""
        if not self.config.enabled or stop_event.is_set():
            logger.info(
                "Forex Factory news monitoring is disabled or stop event is set."
            )
            return

        logger.info(
            f"Forex Factory news monitor started. Scheduled times ({self.tz}): {', '.join(self.config.schedule_times)}"
            f" | 30m pre-event alerts: {'enabled' if self.config.pre_event_alert_enabled else 'disabled'}"
        )

        # Initial fetch of today's events upon startup
        try:
            await self.refresh_today_events()
            logger.info(
                f"Forex Factory news cache initialized with {len(self._cached_events)} high-impact event(s) for today"
            )
        except Exception:
            logger.exception("Failed initial fetch of Forex Factory news on service start")

        last_dispatched_slot: str | None = None

        while not stop_event.is_set():
            now_utc = datetime.now(timezone.utc)
            delay_to_schedule, _next_schedule_time = compute_seconds_until_next_run(
                now_dt=now_utc,
                schedule_times=self.config.schedule_times,
                target_tz=self.tz,
            )

            local_now = now_utc.astimezone(self.tz)
            today_local_str = local_now.strftime("%Y-%m-%d")
            now_time_str = local_now.strftime("%H:%M")
            slot_id = f"{today_local_str}_{now_time_str}"

            # Check if scheduled daily brief is due
            if now_time_str in self.config.schedule_times and slot_id != last_dispatched_slot:
                last_dispatched_slot = slot_id
                try:
                    session_label = f"Schedule {now_time_str}"
                    await self.dispatch_daily_news_update(session_label=session_label)
                except Exception:
                    logger.exception("Unexpected error during scheduled Forex Factory news dispatch")

            # Periodic cache refresh if empty, older than 1 hour, or date rolled over
            if (
                not self._cached_events
                or self._last_fetch_utc is None
                or (now_utc - self._last_fetch_utc) > timedelta(hours=1)
                or (self._cached_date != local_now.date())
            ):
                try:
                    await self.refresh_today_events()
                except Exception:
                    logger.warning("Periodic Forex Factory news refresh encountered error", exc_info=True)

            # Check and dispatch pre-event countdown alerts (e.g. 30m before release)
            if self.config.pre_event_alert_enabled:
                try:
                    await self.check_and_dispatch_pre_event_alerts(now_utc=now_utc)
                except Exception:
                    logger.exception("Unexpected error checking pre-event news alerts")

            # Calculate sleep duration: wake up for the next poll or at the exact scheduled brief time
            sleep_duration = min(self.config.poll_interval_seconds, max(1.0, delay_to_schedule))

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_duration)
                break
            except asyncio.TimeoutError:
                pass

        logger.info("Forex Factory news service stopped cleanly.")
