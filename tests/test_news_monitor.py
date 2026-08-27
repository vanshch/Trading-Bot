"""Unit tests for Forex Factory news fetcher, filter, formatting, scheduling, and service."""

import asyncio
import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from src.config import NewsConfig
from src.news_monitor import (
    ForexNewsEvent,
    ForexNewsFetcher,
    ForexNewsService,
    compute_seconds_until_next_run,
    filter_events_for_day,
    format_daily_news_message,
)


class TestForexNewsFetcher(unittest.IsolatedAsyncioTestCase):
    """Tests for ForexNewsFetcher HTTP requests and ISO date parsing."""

    def setUp(self) -> None:
        self.fetcher = ForexNewsFetcher(
            feed_url="https://mock.faireconomy.media/ff_calendar_thisweek.json",
            timeout_seconds=2.0,
            max_retries=2,
            retry_delay_seconds=0.01,
        )

    async def asyncTearDown(self) -> None:
        await self.fetcher.close()

    def test_parse_event_datetime_valid(self) -> None:
        # ISO format with timezone offset
        dt1 = ForexNewsFetcher.parse_event_datetime("2026-08-27T08:30:00-04:00")
        self.assertIsNotNone(dt1)
        self.assertEqual(dt1.tzinfo, timezone.utc)
        self.assertEqual(dt1.hour, 12)
        self.assertEqual(dt1.minute, 30)

        # ISO format with 'Z' suffix
        dt2 = ForexNewsFetcher.parse_event_datetime("2026-08-27T12:30:00Z")
        self.assertIsNotNone(dt2)
        self.assertEqual(dt2.tzinfo, timezone.utc)
        self.assertEqual(dt2.hour, 12)

    def test_parse_event_datetime_invalid(self) -> None:
        self.assertIsNone(ForexNewsFetcher.parse_event_datetime(""))
        self.assertIsNone(ForexNewsFetcher.parse_event_datetime(None))
        self.assertIsNone(ForexNewsFetcher.parse_event_datetime("not-a-date"))

    @patch("aiohttp.ClientSession.get")
    async def test_fetch_events_success(self, mock_get: MagicMock) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = [
            {
                "title": "Core CPI m/m",
                "country": "USD",
                "date": "2026-08-27T08:30:00-04:00",
                "impact": "High",
                "forecast": "0.3%",
                "previous": "0.2%",
            },
            {
                "title": "ECB Monetary Policy Statement",
                "country": "EUR",
                "date": "2026-08-27T12:15:00Z",
                "impact": "High",
                "forecast": "",
                "previous": "",
            },
        ]
        mock_resp.__aenter__.return_value = mock_resp
        mock_get.return_value = mock_resp

        events = await self.fetcher.fetch_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].title, "Core CPI m/m")
        self.assertEqual(events[0].country, "USD")
        self.assertEqual(events[0].impact, "High")
        self.assertEqual(events[0].forecast, "0.3%")

    @patch("aiohttp.ClientSession.get")
    async def test_fetch_events_malformed_json_returns_empty(
        self, mock_get: MagicMock
    ) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {"error": "Invalid format, dict instead of list"}
        mock_resp.__aenter__.return_value = mock_resp
        mock_get.return_value = mock_resp

        events = await self.fetcher.fetch_events()
        self.assertEqual(events, [])

    @patch("aiohttp.ClientSession.get")
    async def test_fetch_events_http_error_retries_and_returns_empty(
        self, mock_get: MagicMock
    ) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text.return_value = "Server Internal Error"
        mock_resp.__aenter__.return_value = mock_resp
        mock_get.return_value = mock_resp

        events = await self.fetcher.fetch_events()
        self.assertEqual(events, [])
        self.assertEqual(mock_get.call_count, 2)


class TestFilterEventsForDay(unittest.TestCase):
    """Tests for timezone-aware date and impact filtering."""

    def setUp(self) -> None:
        self.tz_kolkata = ZoneInfo("Asia/Kolkata")
        # 2026-08-27 12:30 UTC = 2026-08-27 18:00 IST
        self.event_today_high = ForexNewsEvent(
            title="USD Non-Farm Employment Change",
            country="USD",
            date_utc=datetime(2026, 8, 27, 12, 30, tzinfo=timezone.utc),
            impact="High",
            forecast="185K",
            previous="175K",
        )
        # 2026-08-27 12:30 UTC = 2026-08-27 18:00 IST (Low impact)
        self.event_today_low = ForexNewsEvent(
            title="USD Unemployment Claims",
            country="USD",
            date_utc=datetime(2026, 8, 27, 12, 30, tzinfo=timezone.utc),
            impact="Low",
            forecast="",
            previous="",
        )
        # 2026-08-28 01:00 UTC = 2026-08-28 06:30 IST (Tomorrow)
        self.event_tomorrow = ForexNewsEvent(
            title="JPY Tokyo Core CPI y/y",
            country="JPY",
            date_utc=datetime(2026, 8, 28, 1, 0, tzinfo=timezone.utc),
            impact="High",
            forecast="2.2%",
            previous="2.1%",
        )

    def test_filter_matches_today_high_impact_only(self) -> None:
        events = [self.event_today_high, self.event_today_low, self.event_tomorrow]
        matched = filter_events_for_day(
            events=events,
            target_date=date(2026, 8, 27),
            target_tz=self.tz_kolkata,
            impact_levels=["High"],
        )
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].title, "USD Non-Farm Employment Change")

    def test_filter_by_currencies(self) -> None:
        events = [self.event_today_high, self.event_tomorrow]
        # Target tomorrow date in IST with only JPY
        matched = filter_events_for_day(
            events=events,
            target_date=date(2026, 8, 28),
            target_tz=self.tz_kolkata,
            impact_levels=["High"],
            currencies=["JPY"],
        )
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0].country, "JPY")


class TestFormatDailyNewsMessage(unittest.TestCase):
    """Tests for HTML message formatting."""

    def setUp(self) -> None:
        self.tz = ZoneInfo("Asia/Kolkata")

    def test_format_empty_events_message(self) -> None:
        msg = format_daily_news_message(
            events=[],
            target_date=date(2026, 8, 27),
            target_tz=self.tz,
            session_label="Morning Brief",
        )
        self.assertIn("FOREX FACTORY: HIGH-IMPACT NEWS", msg)
        self.assertIn("No Red Folder (High-Impact) news", msg)
        self.assertIn("Thursday, 27 Aug 2026 (Morning Brief)", msg)

    def test_format_with_events_and_html_escaping(self) -> None:
        event = ForexNewsEvent(
            title="Core CPI <m/m> & Rates",
            country="USD",
            date_utc=datetime(2026, 8, 27, 12, 30, tzinfo=timezone.utc),
            impact="High",
            forecast="0.3%",
            previous="0.2%",
        )
        msg = format_daily_news_message(
            events=[event],
            target_date=date(2026, 8, 27),
            target_tz=self.tz,
            session_label="Morning Brief",
        )
        self.assertIn("🇺🇸 <b>USD</b>", msg)
        self.assertIn("Core CPI &lt;m/m&gt; &amp; Rates", msg)
        self.assertIn("06:00 PM Asia/Kolkata", msg)
        self.assertIn("<i>Forecast:</i> <code>0.3%</code>", msg)
        self.assertIn("<i>Previous:</i> <code>0.2%</code>", msg)


class TestComputeSecondsUntilNextRun(unittest.TestCase):
    """Tests for next-run schedule calculations in local timezone."""

    def setUp(self) -> None:
        self.tz = ZoneInfo("Asia/Kolkata")
        self.schedule_times = ["08:00", "14:00"]

    def test_before_first_slot(self) -> None:
        # 06:00 IST -> next is 08:00 IST today (2 hours = 7200s)
        now_dt = datetime(2026, 8, 27, 6, 0, 0, tzinfo=self.tz)
        delay, next_str = compute_seconds_until_next_run(
            now_dt=now_dt,
            schedule_times=self.schedule_times,
            target_tz=self.tz,
        )
        self.assertEqual(next_str, "08:00")
        self.assertAlmostEqual(delay, 7200.0, delta=1.0)

    def test_between_slots(self) -> None:
        # 10:00 IST -> next is 14:00 IST today (4 hours = 14400s)
        now_dt = datetime(2026, 8, 27, 10, 0, 0, tzinfo=self.tz)
        delay, next_str = compute_seconds_until_next_run(
            now_dt=now_dt,
            schedule_times=self.schedule_times,
            target_tz=self.tz,
        )
        self.assertEqual(next_str, "14:00")
        self.assertAlmostEqual(delay, 14400.0, delta=1.0)

    def test_after_last_slot_rolls_to_tomorrow(self) -> None:
        # 16:00 IST -> next is 08:00 IST tomorrow (16 hours = 57600s)
        now_dt = datetime(2026, 8, 27, 16, 0, 0, tzinfo=self.tz)
        delay, next_str = compute_seconds_until_next_run(
            now_dt=now_dt,
            schedule_times=self.schedule_times,
            target_tz=self.tz,
        )
        self.assertEqual(next_str, "08:00")
        self.assertAlmostEqual(delay, 57600.0, delta=1.0)


class TestForexNewsService(unittest.IsolatedAsyncioTestCase):
    """Tests for ForexNewsService async lifecycle and dispatch."""

    def setUp(self) -> None:
        self.config = NewsConfig(
            enabled=True,
            schedule_times=["08:00", "14:00"],
            timezone="Asia/Kolkata",
            impact_levels=["High"],
        )
        self.mock_notifier = MagicMock()
        self.mock_notifier.send_news_alert = AsyncMock(return_value=True)

        self.mock_fetcher = MagicMock()
        self.mock_fetcher.fetch_events = AsyncMock(return_value=[])
        self.mock_fetcher.close = AsyncMock()

        self.service = ForexNewsService(
            config=self.config,
            notifier=self.mock_notifier,
            fetcher=self.mock_fetcher,
        )

    async def asyncTearDown(self) -> None:
        await self.service.close()

    async def test_dispatch_daily_news_update(self) -> None:
        result = await self.service.dispatch_daily_news_update("Morning Brief")
        self.assertTrue(result)
        self.mock_fetcher.fetch_events.assert_awaited_once()
        self.mock_notifier.send_news_alert.assert_awaited_once()

    async def test_run_stops_immediately_on_stop_event(self) -> None:
        stop_event = asyncio.Event()
        stop_event.set()
        await self.service.run(stop_event)
        # Should exit without hanging
        self.mock_fetcher.fetch_events.assert_not_awaited()
