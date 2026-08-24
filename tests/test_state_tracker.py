"""Unit tests for the trade state machine and lifecycle tracker."""

import unittest
from datetime import datetime, timedelta, timezone

from src.state_tracker import StateTransition, TradeInfo, TradeState, TradeStateTracker
from src.vision_verifier import TradeVerification


class TestTradeStateTracker(unittest.TestCase):
    """Tests for channel state tracking, debouncing, and lifecycle transitions."""

    def setUp(self) -> None:
        self.tracker = TradeStateTracker(miss_threshold=2)
        self.channel_name = "Trader Alpha"

    def test_initial_state_idle(self) -> None:
        self.assertEqual(self.tracker.get_state(self.channel_name), TradeState.IDLE)
        self.assertIsNone(self.tracker.get_active_trade(self.channel_name))

    def test_new_trade_entry_transition(self) -> None:
        v = TradeVerification(
            is_trade_active=True,
            direction="BUY",
            instrument="NIFTY",
            entry_or_pnl="Entry 24500",
            confidence=0.90,
            reasoning="Open long position",
        )
        transition, trade = self.tracker.process_verification(
            self.channel_name, v, frame_bytes=b"jpg_frame"
        )

        self.assertEqual(transition, StateTransition.NEW_TRADE)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.channel_name, self.channel_name)
        self.assertEqual(trade.direction, "BUY")
        self.assertEqual(trade.instrument, "NIFTY")
        self.assertEqual(
            self.tracker.get_state(self.channel_name), TradeState.ACTIVE_TRADE
        )
        self.assertIsNotNone(trade.opened_at)
        self.assertIsNotNone(trade.opened_at.tzinfo)

    def test_still_active_trade_transition(self) -> None:
        # First frame opens trade
        v1 = TradeVerification(
            is_trade_active=True, direction="BUY", instrument="NIFTY", confidence=0.9
        )
        self.tracker.process_verification(self.channel_name, v1)

        # Second frame continues active trade
        v2 = TradeVerification(
            is_trade_active=True,
            direction="BUY",
            instrument="NIFTY",
            entry_or_pnl="PnL: +$200",
            confidence=0.95,
        )
        transition, trade = self.tracker.process_verification(self.channel_name, v2)

        self.assertEqual(transition, StateTransition.STILL_ACTIVE)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.entry_or_pnl, "PnL: +$200")
        self.assertEqual(
            self.tracker.get_state(self.channel_name), TradeState.ACTIVE_TRADE
        )

    def test_debounced_miss_tolerance_and_closure(self) -> None:
        # 1. Open trade
        v_open = TradeVerification(
            is_trade_active=True,
            direction="SELL",
            instrument="BTCUSDT",
            confidence=0.88,
        )
        self.tracker.process_verification(self.channel_name, v_open)

        # 2. First missed frame (miss count = 1 < miss_threshold 2)
        v_miss1 = TradeVerification(
            is_trade_active=False, direction="NONE", confidence=0.0
        )
        t1, _trade1 = self.tracker.process_verification(self.channel_name, v_miss1)

        self.assertEqual(t1, StateTransition.STILL_ACTIVE)
        self.assertEqual(
            self.tracker.get_state(self.channel_name), TradeState.ACTIVE_TRADE
        )

        # 3. Second consecutive missed frame (miss count = 2 >= miss_threshold) -> Trade Closed!
        v_miss2 = TradeVerification(
            is_trade_active=False, direction="NONE", confidence=0.0
        )
        t2, trade2 = self.tracker.process_verification(self.channel_name, v_miss2)

        self.assertEqual(t2, StateTransition.TRADE_CLOSED)
        self.assertIsNotNone(trade2)
        self.assertEqual(trade2.instrument, "BTCUSDT")
        self.assertIsNotNone(trade2.closed_at)
        self.assertEqual(self.tracker.get_state(self.channel_name), TradeState.IDLE)
        self.assertIsNone(self.tracker.get_active_trade(self.channel_name))

    def test_intermittent_miss_recovery(self) -> None:
        # Open trade
        v_open = TradeVerification(
            is_trade_active=True, direction="BUY", instrument="GOLD", confidence=0.85
        )
        self.tracker.process_verification(self.channel_name, v_open)

        # One miss
        v_miss = TradeVerification(is_trade_active=False, direction="NONE")
        self.tracker.process_verification(self.channel_name, v_miss)

        # Recovery (trade visible again) resets miss counter
        v_recovered = TradeVerification(
            is_trade_active=True, direction="BUY", instrument="GOLD", confidence=0.9
        )
        t_rec, _ = self.tracker.process_verification(self.channel_name, v_recovered)
        self.assertEqual(t_rec, StateTransition.STILL_ACTIVE)

        # A subsequent single miss should not close the trade
        t_single, _ = self.tracker.process_verification(self.channel_name, v_miss)
        self.assertEqual(t_single, StateTransition.STILL_ACTIVE)
        self.assertEqual(
            self.tracker.get_state(self.channel_name), TradeState.ACTIVE_TRADE
        )

    def test_idle_remains_idle_on_negative_verification(self) -> None:
        v_none = TradeVerification(is_trade_active=False, direction="NONE")
        transition, trade = self.tracker.process_verification(self.channel_name, v_none)
        self.assertEqual(transition, StateTransition.NONE)
        self.assertIsNone(trade)
        self.assertEqual(self.tracker.get_state(self.channel_name), TradeState.IDLE)

    def test_multi_channel_isolation(self) -> None:
        ch1 = "Channel One"
        ch2 = "Channel Two"

        v1 = TradeVerification(
            is_trade_active=True, direction="BUY", instrument="AAPL", confidence=0.9
        )
        self.tracker.process_verification(ch1, v1)

        self.assertEqual(self.tracker.get_state(ch1), TradeState.ACTIVE_TRADE)
        self.assertEqual(self.tracker.get_state(ch2), TradeState.IDLE)

    def test_trade_duration_formatting(self) -> None:
        now = datetime.now(timezone.utc)
        past = now - timedelta(minutes=14, seconds=20)
        trade = TradeInfo(
            channel_name="Trader",
            direction="BUY",
            instrument="NIFTY",
            entry_or_pnl="",
            confidence=0.9,
            reasoning="",
            opened_at=past,
            last_seen_at=now,
            closed_at=now,
        )
        self.assertAlmostEqual(trade.duration_seconds, 860, delta=1.0)
        self.assertEqual(trade.formatted_duration, "14m 20s")

    def test_management_update_preserves_active_trade(self) -> None:
        """PARTIAL_EXIT and MOVE_STOP must preserve active trade and emit MANAGEMENT_UPDATE."""
        # 1. Open trade
        v_open = TradeVerification(is_trade_active=True, direction="BUY", instrument="GOLD", confidence=0.9)
        t_open, trade_open = self.tracker.process_verification(self.channel_name, v_open)
        self.assertEqual(t_open, StateTransition.NEW_TRADE)
        self.assertEqual(self.tracker.get_state(self.channel_name), TradeState.ACTIVE_TRADE)

        # 2. Emit PARTIAL_EXIT
        from src.multimodal_verifier import MultimodalTradeVerification
        v_partial = MultimodalTradeVerification(
            signal_type="PARTIAL_EXIT",
            direction="BUY",
            instrument="GOLD",
            stop_loss="CTC",
            confidence=0.92,
        )
        t_part, trade_part = self.tracker.process_verification(self.channel_name, v_partial)
        self.assertEqual(t_part, StateTransition.MANAGEMENT_UPDATE)
        self.assertIsNotNone(trade_part)
        self.assertEqual(self.tracker.get_state(self.channel_name), TradeState.ACTIVE_TRADE)
        self.assertIs(trade_part, trade_open)

    def test_duplicate_management_suppression_and_different_action_allowance(self) -> None:
        """Repeated management actions of same type within cooldown are suppressed, but different actions pass."""
        from src.multimodal_verifier import MultimodalTradeVerification
        self.tracker.management_cooldown_seconds = 30.0

        # Open trade
        v_open = MultimodalTradeVerification(signal_type="ENTRY", direction="BUY", instrument="GOLD", confidence=0.9)
        self.tracker.process_verification(self.channel_name, v_open)

        # 1. First PARTIAL_EXIT -> MANAGEMENT_UPDATE
        v_part1 = MultimodalTradeVerification(signal_type="PARTIAL_EXIT", direction="BUY", instrument="GOLD", confidence=0.9)
        t1, _ = self.tracker.process_verification(self.channel_name, v_part1)
        self.assertEqual(t1, StateTransition.MANAGEMENT_UPDATE)

        # 2. Immediate second PARTIAL_EXIT within cooldown -> STILL_ACTIVE (suppressed duplicate)
        v_part2 = MultimodalTradeVerification(signal_type="PARTIAL_EXIT", direction="BUY", instrument="GOLD", confidence=0.9)
        t2, _ = self.tracker.process_verification(self.channel_name, v_part2)
        self.assertEqual(t2, StateTransition.STILL_ACTIVE)

        # 3. MOVE_STOP (different action) -> MANAGEMENT_UPDATE (allowed!)
        v_stop = MultimodalTradeVerification(signal_type="MOVE_STOP", direction="BUY", instrument="GOLD", stop_loss="CTC", confidence=0.9)
        t3, _ = self.tracker.process_verification(self.channel_name, v_stop)
        self.assertEqual(t3, StateTransition.MANAGEMENT_UPDATE)

    def test_explicit_exit_closes_trade_immediately(self) -> None:
        """Explicit EXIT signal must close an active trade immediately without miss-threshold waiting."""
        from src.multimodal_verifier import MultimodalTradeVerification

        # Open trade
        v_open = MultimodalTradeVerification(signal_type="ENTRY", direction="BUY", instrument="GOLD", confidence=0.9)
        self.tracker.process_verification(self.channel_name, v_open)
        self.assertEqual(self.tracker.get_state(self.channel_name), TradeState.ACTIVE_TRADE)

        # Explicit exit
        v_exit = MultimodalTradeVerification(signal_type="EXIT", direction="BUY", instrument="GOLD", confidence=0.95)
        t_exit, trade_exit = self.tracker.process_verification(self.channel_name, v_exit)

        self.assertEqual(t_exit, StateTransition.TRADE_CLOSED)
        self.assertIsNotNone(trade_exit)
        self.assertIsNotNone(trade_exit.closed_at)
        self.assertEqual(self.tracker.get_state(self.channel_name), TradeState.IDLE)
        self.assertIsNone(self.tracker.get_active_trade(self.channel_name))

    def test_missed_entry_management_alert_without_fabricating_active_position(self) -> None:
        """A verified management instruction when IDLE (missed entry) emits MANAGEMENT_UPDATE without fabricating an active position."""
        from src.multimodal_verifier import MultimodalTradeVerification

        # Channel starts IDLE
        self.assertEqual(self.tracker.get_state(self.channel_name), TradeState.IDLE)

        # Spoken management signal arrives
        v_part = MultimodalTradeVerification(
            signal_type="PARTIAL_EXIT",
            direction="BUY",
            instrument="GOLD",
            stop_loss="CTC",
            confidence=0.90,
        )
        transition, trade = self.tracker.process_verification(self.channel_name, v_part)

        self.assertEqual(transition, StateTransition.MANAGEMENT_UPDATE)
        self.assertIsNotNone(trade)
        self.assertEqual(trade.instrument, "GOLD")
        # Channel state MUST remain IDLE; active position must NOT be fabricated!
        self.assertEqual(self.tracker.get_state(self.channel_name), TradeState.IDLE)
        self.assertIsNone(self.tracker.get_active_trade(self.channel_name))

    def test_missed_entry_explicit_exit_when_idle(self) -> None:
        """A verified explicit exit when IDLE emits TRADE_CLOSED without fabricating an active position."""
        from src.multimodal_verifier import MultimodalTradeVerification

        self.assertEqual(self.tracker.get_state(self.channel_name), TradeState.IDLE)

        v_exit = MultimodalTradeVerification(
            signal_type="EXIT",
            direction="BUY",
            instrument="GOLD",
            confidence=0.90,
        )
        transition, trade = self.tracker.process_verification(self.channel_name, v_exit)

        self.assertEqual(transition, StateTransition.TRADE_CLOSED)
        self.assertIsNotNone(trade)
        self.assertEqual(self.tracker.get_state(self.channel_name), TradeState.IDLE)
        self.assertIsNone(self.tracker.get_active_trade(self.channel_name))


if __name__ == "__main__":
    unittest.main()
