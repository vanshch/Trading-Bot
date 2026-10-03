"""Offline coverage and ingestion checks, run with the isolated Linux runtime."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from soak_tvkit import Soak, SYMBOL, TARGET_SECONDS, open_seconds, validate_bars, next_wake_at


class AuditChecks(unittest.TestCase):
    def test_simulated_full_run_delivers_every_close_at_ten_second_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [1790985600+899.0]
            with patch("soak_tvkit.time.time", side_effect=lambda: clock[0]), patch("soak_tvkit.rss_mb", return_value=80):
                soak = self.make_soak(Path(directory))
                deliveries = []
                async def fetch(tf):
                    for ts, in soak.db.execute("SELECT ts FROM expected WHERE tf=?", (tf,)).fetchall():
                        cursor = soak.db.execute("INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?,?)", (tf, ts, 10, 11, 9, 10, 1, clock[0]))
                        if cursor.rowcount:
                            deliveries.append(clock[0]-(ts+tf*60))
                    return True
                async def faults():
                    return {"status": "PASS"}
                async def sleep(seconds):
                    clock[0] += seconds
                soak.fetch = fetch
                soak.fault_checks = faults
                soak.event = lambda *args, **kwargs: None
                with patch("soak_tvkit.asyncio.sleep", side_effect=sleep):
                    asyncio.run(soak.run())
                self.assertEqual(len(deliveries), 120)
                self.assertTrue(all(delay == 10 for delay in deliveries), deliveries)
                self.assertEqual(soak.summary(terminal=True)["status"], "AUTOMATED_CHECKS_PASSED_REVIEW_PENDING")
                soak.db.close()

    def test_wakes_at_offset_even_after_crossing_boundary(self):
        last = 900000
        deadline = last+86400+1
        target = last+900+10
        for now in (last+850, last+900, last+904, last+909):
            self.assertEqual(next_wake_at(last, deadline), target)
            self.assertGreater(next_wake_at(last, deadline), now)
        self.assertEqual(next_wake_at(last+900, deadline), last+1800+10)
        final = int(deadline//900)*900
        self.assertEqual(next_wake_at(final, deadline), final+60)

    def make_soak(self, root):
        with patch("soak_tvkit.load_client", return_value=(None, [])):
            return Soak(root)

    def test_weekend_counts_continuously(self):
        self.assertEqual(open_seconds(1790985600, 1790985600+86400), 86400)

    def test_full_day_has_96_quarter_hours_and_24_hours(self):
        for offset in (0, 1, 899, 1801):
            with tempfile.TemporaryDirectory() as directory:
                soak = self.make_soak(Path(directory))
                start = 1790985600+offset
                soak.state.update(started_epoch=start, deadline_epoch=start+86400, last_boundary=0)
                soak.expect_through(start+86400+7200)
                for tf, count in ((15, 96), (60, 24)):
                    self.assertEqual(soak.db.execute("SELECT count(*) FROM expected WHERE tf=?", (tf,)).fetchone()[0], count)
                self.assertLessEqual(soak.db.execute("SELECT max(ts+tf*60) FROM expected").fetchone()[0], start+86400)
                soak.db.close()

    def test_bad_prices_duplicates_and_alignment_rejected(self):
        bars = [SimpleNamespace(timestamp=900000+i*900, open=10, high=11, low=9, close=10, volume=1) for i in range(15)]
        validate_bars(bars, 15)
        bars[-1].timestamp = bars[-2].timestamp
        with self.assertRaises(AssertionError):
            validate_bars(bars, 15)
        bars[-1].timestamp += 901
        with self.assertRaises(AssertionError):
            validate_bars(bars, 15)
        bars[-1].timestamp -= 1
        bars[-1].high = 8
        with self.assertRaises(AssertionError):
            validate_bars(bars, 15)

    def test_fresh_pending_close_is_not_overdue(self):
        with tempfile.TemporaryDirectory() as directory:
            soak = self.make_soak(Path(directory))
            soak.db.execute("INSERT INTO expected VALUES (15,900000)")
            with patch("soak_tvkit.time.time", return_value=900920), patch("soak_tvkit.rss_mb", return_value=80):
                report = soak.summary()
                self.assertEqual(report["missing_candles"], 1)
                self.assertEqual(report["missing_candles_overdue"], 0)
            with patch("soak_tvkit.time.time", return_value=900961), patch("soak_tvkit.rss_mb", return_value=80):
                self.assertEqual(soak.summary()["missing_candles_overdue"], 1)
            soak.db.close()

    def test_replay_dedup_forming_exclusion_and_revision_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            soak = self.make_soak(Path(directory))
            bars = [SimpleNamespace(timestamp=900000+i*900, open=10, high=11, low=9, close=10, volume=1, model_dump=lambda **kw: {}) for i in range(15)]
            class Client:
                def __init__(self, **kwargs):
                    pass
                async def __aenter__(self):
                    return self
                async def __aexit__(self, *args):
                    pass
                async def get_historical_ohlcv(self, *args, **kwargs):
                    soak.identities.append({"full_name": SYMBOL, "source_id": "BINANCE"})
                    return bars
            soak.OHLCV = Client
            with patch("soak_tvkit.time.time", return_value=912620):
                self.assertTrue(asyncio.run(soak.fetch(15)))
                self.assertTrue(asyncio.run(soak.fetch(15)))
                self.assertEqual(soak.db.execute("SELECT count(*) FROM candles").fetchone()[0], 14)
                self.assertEqual(soak.state["historical_replays_deduplicated"], 14)
                bars[0].close = 10.5
                self.assertTrue(asyncio.run(soak.fetch(15)))
                self.assertEqual(soak.db.execute("SELECT count(*) FROM issues").fetchone()[0], 1)
            soak.db.close()

    def test_terminal_status_requires_sufficient_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            soak = self.make_soak(Path(directory))
            self.assertEqual(soak.summary(terminal=True)["status"], "REVIEW_REQUIRED")
            soak.state.update(observed_market_open_seconds=TARGET_SECONDS, fault_checks={"status": "PASS"}, live_fault_checks={"status": "PASS"})
            for tf, count in ((15, 96), (60, 24)):
                for i in range(count):
                    ts = 900000+i*tf*60
                    soak.db.execute("INSERT INTO expected VALUES (?,?)", (tf, ts))
                    soak.db.execute("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)", (tf, ts, 10, 11, 9, 10, 1, ts+tf*60+20))
            soak.db.executemany("INSERT INTO memory VALUES (?,80)", ((i,) for i in range(70)))
            self.assertEqual(soak.summary(terminal=True)["status"], "AUTOMATED_CHECKS_PASSED_REVIEW_PENDING")
            soak.state["process_starts"] = 2
            self.assertEqual(soak.summary(terminal=True)["status"], "REVIEW_REQUIRED")
            soak.db.close()


if __name__ == "__main__":
    unittest.main()
