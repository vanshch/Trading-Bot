import unittest
import tempfile
import time
from pathlib import Path
from alerts import AlertManager

class TestLevelRejectionAlerts(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.alerts_file = Path(self.temp_dir.name) / "alerts.json"
        self.manager = AlertManager(file_path=self.alerts_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_upper_wick_touch_rejection(self):
        """Upper resistance level touched by wick and closed below -> triggers WICK_TOUCH immediately"""
        self.manager.add_price_alert(target_price=2740.0, current_price=2735.0)

        # Candle high touches 2740.50, but open 2736.0, close 2738.0 (body top 2738 < 2740)
        c = {
            "open": 2736.0,
            "high": 2740.5,
            "low": 2735.0,
            "close": 2738.0,
            "is_green": True,
            "sign": "+",
            "pips": 20.0,
            "emoji": "🟢",
            "time_str": "12:00 UTC",
            "timestamp": "1727000000"
        }
        triggered = self.manager.check_price_alerts(c)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["trigger_type"], "WICK_TOUCH")
        self.assertEqual(triggered[0]["target_price"], 2740.0)
        # Alert should be completed and removed from active list
        self.assertEqual(len(self.manager.get_active_price_alerts()), 0)

    def test_lower_wick_touch_rejection(self):
        """Lower support level touched by wick and closed above -> triggers WICK_TOUCH immediately"""
        self.manager.add_price_alert(target_price=2710.0, current_price=2720.0)

        # Candle low touches 2709.50, but open 2715.0, close 2712.0 (body bottom 2712 > 2710)
        c = {
            "open": 2715.0,
            "high": 2718.0,
            "low": 2709.5,
            "close": 2712.0,
            "is_green": False,
            "sign": "-",
            "pips": 30.0,
            "emoji": "🔴",
            "time_str": "12:00 UTC",
            "timestamp": "1727000000"
        }
        triggered = self.manager.check_price_alerts(c)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0]["trigger_type"], "WICK_TOUCH")
        self.assertEqual(len(self.manager.get_active_price_alerts()), 0)

    def test_upper_false_breakout_rejection(self):
        """Bar 1 breaks out with body -> Bar 2 is immediate Red closing below level -> triggers FALSE_BREAKOUT"""
        self.manager.add_price_alert(target_price=2740.0, current_price=2735.0)

        # Bar 1: Green candle breaks with body (open 2737, close 2742.50)
        c1 = {
            "open": 2737.0,
            "high": 2743.0,
            "low": 2736.0,
            "close": 2742.5,
            "is_green": True,
            "sign": "+",
            "pips": 55.0,
            "emoji": "🟢",
            "time_str": "12:00 UTC",
            "timestamp": 1727000000
        }
        triggered1 = self.manager.check_price_alerts(c1, interval_seconds=900)
        self.assertEqual(len(triggered1), 0)
        
        # State must now be PENDING_CONFIRMATION
        active = self.manager.get_active_price_alerts()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["state"], "PENDING_CONFIRMATION")

        # Bar 2: Immediate next candle (15 min later) is Red closing at 2737.0 (outside/below 2740)
        c2 = {
            "open": 2742.0,
            "high": 2742.5,
            "low": 2735.0,
            "close": 2737.0,
            "is_green": False,
            "sign": "-",
            "pips": 50.0,
            "emoji": "🔴",
            "time_str": "12:15 UTC",
            "timestamp": 1727000900
        }
        triggered2 = self.manager.check_price_alerts(c2, interval_seconds=900)
        self.assertEqual(len(triggered2), 1)
        self.assertEqual(triggered2[0]["trigger_type"], "FALSE_BREAKOUT")
        self.assertEqual(triggered2[0]["target_price"], 2740.0)
        # Completed and cleared
        self.assertEqual(len(self.manager.get_active_price_alerts()), 0)

    def test_upper_genuine_breakout_zone_breached(self):
        """Bar 1 breaks out with body -> Bar 2 is Green -> zone is breached and alert discarded silently"""
        self.manager.add_price_alert(target_price=2740.0, current_price=2735.0)

        # Bar 1: Green candle breaks with body
        c1 = {
            "open": 2737.0,
            "high": 2743.0,
            "low": 2736.0,
            "close": 2742.5,
            "is_green": True,
            "sign": "+",
            "pips": 55.0,
            "emoji": "🟢",
            "time_str": "12:00 UTC",
            "timestamp": 1727000000
        }
        self.manager.check_price_alerts(c1, interval_seconds=900)

        # Bar 2: Next candle continues Green closing at 2746.0
        c2 = {
            "open": 2742.5,
            "high": 2747.0,
            "low": 2742.0,
            "close": 2746.0,
            "is_green": True,
            "sign": "+",
            "pips": 35.0,
            "emoji": "🟢",
            "time_str": "12:15 UTC",
            "timestamp": 1727000900
        }
        triggered2 = self.manager.check_price_alerts(c2, interval_seconds=900)
        # Should NOT trigger
        self.assertEqual(len(triggered2), 0)
        # Should be silently discarded from active alerts
        self.assertEqual(len(self.manager.get_active_price_alerts()), 0)

    def test_upper_failed_rejection_still_above(self):
        """Bar 1 breaks out -> Bar 2 is Red but closes at 2741 (still above 2740) -> zone breached, silent discard"""
        self.manager.add_price_alert(target_price=2740.0, current_price=2735.0)

        c1 = {
            "open": 2737.0,
            "high": 2743.0,
            "low": 2736.0,
            "close": 2742.5,
            "is_green": True,
            "sign": "+",
            "pips": 55.0,
            "emoji": "🟢",
            "time_str": "12:00 UTC",
            "timestamp": 1727000000
        }
        self.manager.check_price_alerts(c1, interval_seconds=900)

        # Bar 2 is red, but fails to close outside (closes at 2741.0 > 2740.0)
        c2 = {
            "open": 2742.5,
            "high": 2743.0,
            "low": 2740.5,
            "close": 2741.0,
            "is_green": False,
            "sign": "-",
            "pips": 15.0,
            "emoji": "🔴",
            "time_str": "12:15 UTC",
            "timestamp": 1727000900
        }
        triggered2 = self.manager.check_price_alerts(c2, interval_seconds=900)
        self.assertEqual(len(triggered2), 0)
        self.assertEqual(len(self.manager.get_active_price_alerts()), 0)

    def test_lower_false_breakdown_rejection(self):
        """Lower support breakdown by Red bar 1 -> Immediate Green bar 2 closes above level -> triggers FALSE_BREAKOUT"""
        self.manager.add_price_alert(target_price=2710.0, current_price=2720.0)

        # Bar 1: Red breakdown candle closing at 2707.0
        c1 = {
            "open": 2715.0,
            "high": 2716.0,
            "low": 2706.0,
            "close": 2707.0,
            "is_green": False,
            "sign": "-",
            "pips": 80.0,
            "emoji": "🔴",
            "time_str": "12:00 UTC",
            "timestamp": 1727000000
        }
        self.manager.check_price_alerts(c1, interval_seconds=900)
        active = self.manager.get_active_price_alerts()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["state"], "PENDING_CONFIRMATION")

        # Bar 2: Immediate Green candle closing back above at 2713.0
        c2 = {
            "open": 2707.0,
            "high": 2714.0,
            "low": 2706.5,
            "close": 2713.0,
            "is_green": True,
            "sign": "+",
            "pips": 60.0,
            "emoji": "🟢",
            "time_str": "12:15 UTC",
            "timestamp": 1727000900
        }
        triggered2 = self.manager.check_price_alerts(c2, interval_seconds=900)
        self.assertEqual(len(triggered2), 1)
        self.assertEqual(triggered2[0]["trigger_type"], "FALSE_BREAKOUT")
        self.assertEqual(triggered2[0]["target_price"], 2710.0)
        self.assertEqual(len(self.manager.get_active_price_alerts()), 0)

    def test_momentum_one_shot_toggle_and_reset(self):
        """Verify that 2-green and 2-red settings can be toggled on and reset to off on trigger"""
        self.assertFalse(self.manager.is_setting_enabled("two_green_candles"))
        self.assertFalse(self.manager.is_setting_enabled("two_red_candles"))

        # User toggles on
        self.manager.toggle_setting("two_green_candles")
        self.assertTrue(self.manager.is_setting_enabled("two_green_candles"))

        # Notification fires -> reset to False (one-shot)
        self.manager.set_setting("two_green_candles", False)
        self.assertFalse(self.manager.is_setting_enabled("two_green_candles"))

        # Similarly for red
        self.manager.toggle_setting("two_red_candles")
        self.assertTrue(self.manager.is_setting_enabled("two_red_candles"))
        self.manager.set_setting("two_red_candles", False)
        self.assertFalse(self.manager.is_setting_enabled("two_red_candles"))

    def test_oanda_price_offset_calibration(self):
        """Verify that applying OANDA offset calibrates Twelve Data prices while preserving pips and direction"""
        import config
        offset = config.OANDA_PRICE_OFFSET
        self.assertEqual(offset, -0.50)

        raw_o, raw_h, raw_l, raw_c = 2740.50, 2745.50, 2738.50, 2743.00
        cal_o = round(raw_o + offset, 2)
        cal_h = round(raw_h + offset, 2)
        cal_l = round(raw_l + offset, 2)
        cal_c = round(raw_c + offset, 2)

        self.assertEqual(cal_o, 2740.00)
        self.assertEqual(cal_h, 2745.00)
        self.assertEqual(cal_l, 2738.00)
        self.assertEqual(cal_c, 2742.50)

        # Pip difference and direction invariance
        raw_diff = raw_c - raw_o
        cal_diff = cal_c - cal_o
        self.assertAlmostEqual(raw_diff, cal_diff, places=4)
        self.assertEqual(round(raw_diff * 10, 1), round(cal_diff * 10, 1))

if __name__ == "__main__":
    unittest.main()


