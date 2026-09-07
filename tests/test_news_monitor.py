"""Unit tests for Forex Factory news fetcher, filter, formatting, scheduling, and service."""

import asyncio
import unittest
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from src.config import NewsConfig
from src.news_monitor import (
    DEFAULT_CURRENCY_INSTRUMENTS,
    ForexNewsEvent,
    ForexNewsFetcher,
    ForexNewsService,
    compute_seconds_until_next_run,
    filter_events_for_day,
    format_daily_news_message,
    format_pre_event_warning_message,
    get_impacted_instruments,
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
        self.assertEqual(
            msg,
            "<b>FOREX NEWS — Thu, 27 Aug 2026</b>\n"
            "High impact · Asia/Kolkata · Morning Brief\n\n"
            "<i>No high-impact events today.</i>",
        )

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
        self.assertEqual(
            msg,
            "<b>FOREX NEWS — Thu, 27 Aug 2026</b>\n"
            "High impact · Asia/Kolkata · Morning Brief\n\n"
            "<b>6:00 PM · USD</b> 🇺🇸\n"
            "Core CPI &lt;m/m&gt; &amp; Rates\n"
            "Forecast <code>0.3%</code> · Previous <code>0.2%</code>\n\n"
            "<i>Expect volatility around release times.</i>",
        )
        self.assertNotIn("━", msg)
        self.assertNotIn("─", msg)


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


class TestPreEventWarningFormatting(unittest.TestCase):
    """Tests for 30-minute pre-event warning message construction and instrument mapping."""

    def setUp(self) -> None:
        self.tz = ZoneInfo("Asia/Kolkata")
        self.usd_event = ForexNewsEvent(
            title="Non-Farm Employment Change",
            country="USD",
            date_utc=datetime(2026, 8, 28, 12, 30, tzinfo=timezone.utc),
            impact="High",
            forecast="185K",
            previous="175K",
        )
        self.usd_event2 = ForexNewsEvent(
            title="Unemployment Rate",
            country="USD",
            date_utc=datetime(2026, 8, 28, 12, 30, tzinfo=timezone.utc),
            impact="High",
            forecast="4.3%",
            previous="4.3%",
        )

    def test_get_impacted_instruments(self) -> None:
        self.assertIn("USD", DEFAULT_CURRENCY_INSTRUMENTS)
        self.assertIn("XAUUSD (Gold)", get_impacted_instruments("USD"))
        self.assertIn("EURUSD", get_impacted_instruments("EUR"))
        self.assertIn("GBPUSD", get_impacted_instruments("GBP"))
        self.assertEqual(get_impacted_instruments("XYZ"), "XYZ Pairs")

    def test_format_empty_returns_empty(self) -> None:
        self.assertEqual(format_pre_event_warning_message([], self.tz), "")

    def test_format_single_event_warning(self) -> None:
        msg = format_pre_event_warning_message([self.usd_event], self.tz, minutes_until=30)
        self.assertIn("HIGH-IMPACT NEWS IN 30 MINUTES", msg)
        self.assertIn("06:00 PM Asia/Kolkata", msg)
        self.assertIn("🇺🇸 <b>USD</b>", msg)
        self.assertIn("XAUUSD (Gold)", msg)
        self.assertIn("Non-Farm Employment Change", msg)
        self.assertIn("<i>Forecast:</i> <code>185K</code>", msg)
        self.assertIn("<i>Previous:</i> <code>175K</code>", msg)
        self.assertIn("Volatility warning", msg)

    def test_format_co_occurring_events_warning(self) -> None:
        msg = format_pre_event_warning_message([self.usd_event, self.usd_event2], self.tz, minutes_until=29)
        self.assertIn("HIGH-IMPACT NEWS IN 29 MINUTES", msg)
        self.assertIn("Releasing Events:", msg)
        self.assertIn("Non-Farm Employment Change", msg)
        self.assertIn("Unemployment Rate", msg)
        self.assertIn("Forecast: <code>185K</code>", msg)


class TestForexNewsService(unittest.IsolatedAsyncioTestCase):
    """Tests for ForexNewsService async lifecycle, pre-event timers, and dispatch."""

    def setUp(self) -> None:
        self.config = NewsConfig(
            enabled=True,
            schedule_times=["08:00", "14:00"],
            timezone="Asia/Kolkata",
            impact_levels=["High"],
            pre_event_alert_enabled=True,
            pre_event_minutes=30,
            poll_interval_seconds=10.0,
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
        # Should exit without hanging or fetching
        self.mock_fetcher.fetch_events.assert_not_awaited()

    async def test_check_and_dispatch_pre_event_alerts_success(self) -> None:
        # Event at 12:30 UTC
        event = ForexNewsEvent(
            title="Core CPI m/m",
            country="USD",
            date_utc=datetime(2026, 8, 28, 12, 30, tzinfo=timezone.utc),
            impact="High",
            forecast="0.3%",
            previous="0.2%",
        )
        self.service._cached_events = [event]
        # Current time is 12:02 UTC (28 minutes before 12:30)
        now_utc = datetime(2026, 8, 28, 12, 2, tzinfo=timezone.utc)

        dispatched = await self.service.check_and_dispatch_pre_event_alerts(now_utc=now_utc)
        self.assertEqual(len(dispatched), 1)
        expected_key = self.service.get_event_key(event)
        self.assertIn(expected_key, dispatched)
        self.assertIn(expected_key, self.service._alerted_event_keys)
        self.mock_notifier.send_news_alert.assert_awaited_once()

        # Check message content sent to notifier
        sent_message = self.mock_notifier.send_news_alert.call_args[0][0]
        self.assertIn("HIGH-IMPACT NEWS IN 28 MINUTES", sent_message)
        self.assertIn("XAUUSD (Gold)", sent_message)
        self.assertIn("Core CPI m/m", sent_message)

    async def test_check_and_dispatch_pre_event_alerts_deduplication(self) -> None:
        event = ForexNewsEvent(
            title="Core CPI m/m",
            country="USD",
            date_utc=datetime(2026, 8, 28, 12, 30, tzinfo=timezone.utc),
            impact="High",
        )
        self.service._cached_events = [event]
        now_utc = datetime(2026, 8, 28, 12, 5, tzinfo=timezone.utc)

        # First call dispatches
        d1 = await self.service.check_and_dispatch_pre_event_alerts(now_utc=now_utc)
        self.assertEqual(len(d1), 1)
        self.assertEqual(self.mock_notifier.send_news_alert.await_count, 1)

        # Second call within same window is suppressed
        d2 = await self.service.check_and_dispatch_pre_event_alerts(now_utc=now_utc)
        self.assertEqual(len(d2), 0)
        self.assertEqual(self.mock_notifier.send_news_alert.await_count, 1)

    async def test_check_and_dispatch_pre_event_alerts_ignores_far_future_and_past(self) -> None:
        far_future = ForexNewsEvent(
            title="Far Future Event",
            country="USD",
            date_utc=datetime(2026, 8, 28, 14, 30, tzinfo=timezone.utc),
            impact="High",
        )
        past_event = ForexNewsEvent(
            title="Past Event",
            country="EUR",
            date_utc=datetime(2026, 8, 28, 11, 0, tzinfo=timezone.utc),
            impact="High",
        )
        self.service._cached_events = [far_future, past_event]
        # Current time 12:00 UTC (past is -60m, far future is +150m)
        now_utc = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)

        dispatched = await self.service.check_and_dispatch_pre_event_alerts(now_utc=now_utc)
        self.assertEqual(dispatched, [])
        self.mock_notifier.send_news_alert.assert_not_awaited()
        # Past event is marked processed
        self.assertIn(self.service.get_event_key(past_event), self.service._alerted_event_keys)

    async def test_check_and_dispatch_pre_event_alerts_disabled(self) -> None:
        self.service.config.pre_event_alert_enabled = False
        event = ForexNewsEvent(
            title="Core CPI m/m",
            country="USD",
            date_utc=datetime(2026, 8, 28, 12, 30, tzinfo=timezone.utc),
            impact="High",
        )
        self.service._cached_events = [event]
        now_utc = datetime(2026, 8, 28, 12, 10, tzinfo=timezone.utc)

        dispatched = await self.service.check_and_dispatch_pre_event_alerts(now_utc=now_utc)
        self.assertEqual(dispatched, [])
        self.mock_notifier.send_news_alert.assert_not_awaited()

    async def test_date_rollover_clears_alerted_keys(self) -> None:
        self.service._cached_date = date(2026, 8, 27)
        self.service._alerted_event_keys.add("USD:Old Event:2026-08-27T12:00:00+00:00")

        self.mock_fetcher.fetch_events.return_value = []
        await self.service.refresh_today_events()

        # Date rolled over to today -> past alerted keys cleared
        self.assertEqual(len(self.service._alerted_event_keys), 0)

class TestForexNewsFetcherRateLimiting(unittest.IsolatedAsyncioTestCase):
    """Tests for ForexNewsFetcher HTTP 429 backoff, cooldown, and mirror fallback."""

    def setUp(self) -> None:
        self.fetcher = ForexNewsFetcher(
            feed_url="https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout_seconds=2.0,
            max_retries=3,
            retry_delay_seconds=0.01,
            rate_limit_cooldown_seconds=600.0,
        )

    async def asyncTearDown(self) -> None:
        await self.fetcher.close()

    @patch("aiohttp.ClientSession.get")
    async def test_http_429_activates_cooldown_and_aborts_retries(
        self, mock_get: MagicMock
    ) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 429
        mock_resp.headers = {"Retry-After": "120"}
        mock_resp.__aenter__.return_value = mock_resp
        mock_get.return_value = mock_resp

        # 1. Fetch triggers 429
        events = await self.fetcher.fetch_events()
        self.assertEqual(events, [])
        self.assertFalse(self.fetcher.last_fetch_succeeded)
        # Should abort immediately after 1 attempt, NOT burn all 3 retries
        self.assertEqual(mock_get.call_count, 1)
        self.assertTrue(self.fetcher.is_rate_limited())
        # Retry-After of 120s was respected
        self.assertGreaterEqual(self.fetcher.remaining_cooldown_seconds(), 115.0)

        # 2. Subsequent call while rate-limited does NOT make any network request
        events2 = await self.fetcher.fetch_events()
        self.assertEqual(events2, [])
        self.assertEqual(mock_get.call_count, 1)

    @patch("aiohttp.ClientSession.get")
    async def test_http_429_default_cooldown_when_no_retry_after(
        self, mock_get: MagicMock
    ) -> None:
        mock_resp = AsyncMock()
        mock_resp.status = 429
        mock_resp.headers = {}
        mock_resp.__aenter__.return_value = mock_resp
        mock_get.return_value = mock_resp

        events = await self.fetcher.fetch_events()
        self.assertEqual(events, [])
        self.assertTrue(self.fetcher.is_rate_limited())
        # Default rate_limit_cooldown_seconds is 600.0
        self.assertGreaterEqual(self.fetcher.remaining_cooldown_seconds(), 590.0)

    @patch("aiohttp.ClientSession.get")
    async def test_server_error_falls_back_to_mirror_url(
        self, mock_get: MagicMock
    ) -> None:
        # First call to primary URL fails with 500, second call to mirror succeeds with 200
        mock_resp_fail = AsyncMock()
        mock_resp_fail.status = 500
        mock_resp_fail.text.return_value = "Server Error"

        mock_resp_ok = AsyncMock()
        mock_resp_ok.status = 200
        mock_resp_ok.json.return_value = []

        mock_resp_fail.__aenter__.return_value = mock_resp_fail
        mock_resp_ok.__aenter__.return_value = mock_resp_ok
        mock_get.side_effect = [mock_resp_fail, mock_resp_ok]

        events = await self.fetcher.fetch_events()
        self.assertEqual(events, [])
        self.assertTrue(self.fetcher.last_fetch_succeeded)
        self.assertEqual(mock_get.call_count, 2)
        # Verify second call used mirror URL cdn-nfs
        first_call_url = mock_get.call_args_list[0][0][0]
        second_call_url = mock_get.call_args_list[1][0][0]
        self.assertIn("nfs.faireconomy.media", first_call_url)
        self.assertIn("cdn-nfs.faireconomy.media", second_call_url)


class TestForexNewsServiceEmptyCacheAndThrottling(unittest.IsolatedAsyncioTestCase):
    """Tests that empty event lists are cached as valid results and not re-fetched every loop."""

    def setUp(self) -> None:
        self.config = NewsConfig(
            enabled=True,
            schedule_times=["08:00", "14:00"],
            timezone="Asia/Kolkata",
            impact_levels=["High"],
            pre_event_alert_enabled=True,
            pre_event_minutes=30,
            poll_interval_seconds=10.0,
            min_retry_interval_seconds=60.0,
        )
        self.mock_notifier = MagicMock()
        self.mock_notifier.send_news_alert = AsyncMock(return_value=True)

        self.mock_fetcher = MagicMock()
        self.mock_fetcher.fetch_events = AsyncMock(return_value=[])
        self.mock_fetcher.close = AsyncMock()
        self.mock_fetcher.is_rate_limited.return_value = False
        self.mock_fetcher.last_fetch_succeeded = True

        self.service = ForexNewsService(
            config=self.config,
            notifier=self.mock_notifier,
            fetcher=self.mock_fetcher,
        )

    async def asyncTearDown(self) -> None:
        await self.service.close()

    async def test_successful_empty_calendar_records_success_timestamp(self) -> None:
        self.mock_fetcher.fetch_events.return_value = []
        self.mock_fetcher.last_fetch_succeeded = True

        events = await self.service.refresh_today_events()
        self.assertEqual(events, [])
        self.assertIsNotNone(self.service._last_successful_fetch_utc)
        self.assertEqual(self.service._cached_events, [])

    async def test_failed_fetch_is_throttled_by_min_retry_interval(self) -> None:
        self.mock_fetcher.fetch_events.return_value = []
        self.mock_fetcher.last_fetch_succeeded = False

        # First call fails
        await self.service.refresh_today_events()
        self.assertIsNone(self.service._last_successful_fetch_utc)
        self.assertEqual(self.mock_fetcher.fetch_events.call_count, 1)

        # Immediate second call without force should be throttled
        await self.service.refresh_today_events(force=False)
        # Should NOT have called fetch_events again
        self.assertEqual(self.mock_fetcher.fetch_events.call_count, 1)

    async def test_run_loop_does_not_refetch_when_cached_events_empty(self) -> None:
        # Simulate successful startup with empty calendar (e.g. Sunday or no High-impact news)
        self.mock_fetcher.fetch_events.return_value = []
        self.mock_fetcher.last_fetch_succeeded = True

        stop_event = asyncio.Event()

        # Stop after 2 loop cycles
        loop_counter = 0
        original_sleep = asyncio.wait_for

        async def mocked_wait_for(coro, timeout):
            nonlocal loop_counter
            if asyncio.iscoroutine(coro):
                coro.close()
            loop_counter += 1
            if loop_counter >= 3:
                stop_event.set()
            raise asyncio.TimeoutError()

        with patch("asyncio.wait_for", side_effect=mocked_wait_for):
            await self.service.run(stop_event)

        # Initial startup fetch occurred once, but the 3 loop iterations did NOT re-fetch!
        self.assertEqual(self.mock_fetcher.fetch_events.call_count, 1)
