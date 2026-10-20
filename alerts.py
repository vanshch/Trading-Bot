import json
import math
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import logger

DATA_DIR = Path(__file__).parent / "data"
ALERTS_FILE = DATA_DIR / "alerts.json"

DEFAULT_DATA = {
    "hourly_volume": {"enabled": False, "armed_after": 0, "last_processed": 0},
    "settings": {
        "two_green_candles": False,
        "two_red_candles": False,
    },
    "price_alerts": [],
    "next_alert_id": 1,
}

class AlertManager:
    def __init__(self, file_path: Optional[Path] = None):
        self.file_path = file_path or ALERTS_FILE
        self._ensure_file()

    def _ensure_file(self):
        if not self.file_path.exists():
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self._write(DEFAULT_DATA)

    def _read(self) -> Dict[str, Any]:
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    raise TypeError("Invalid alert state")
                volume = data.get("hourly_volume")
                if (
                    not isinstance(volume, dict)
                    or type(volume.get("enabled")) is not bool
                    or any(
                        type(volume.get(key)) not in (int, float)
                        or not math.isfinite(volume[key])
                        or volume[key] < 0
                        for key in ("armed_after", "last_processed")
                    )
                ):
                    data["hourly_volume"] = DEFAULT_DATA["hourly_volume"].copy()
                if "settings" not in data:
                    data["settings"] = DEFAULT_DATA["settings"].copy()
                else:
                    for k, v in DEFAULT_DATA["settings"].items():
                        if k not in data["settings"]:
                            data["settings"][k] = v
                if "price_alerts" not in data:
                    data["price_alerts"] = []
                if "next_alert_id" not in data:
                    data["next_alert_id"] = 1
                return data
        except Exception as e:
            logger.error(f"Failed to read alerts.json: {e}. Resetting to defaults.")
            return deepcopy(DEFAULT_DATA)

    def _write(self, data: Dict[str, Any]):
        tmp_file = self.file_path.with_suffix(".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp_file.replace(self.file_path)
            return True
        except Exception as e:
            logger.error(f"Failed to write alerts.json: {e}")
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)
            return False

    def is_hourly_volume_enabled(self) -> bool:
        return self._read()["hourly_volume"]["enabled"] is True

    def toggle_hourly_volume(self) -> bool:
        data = self._read()
        enabled = data["hourly_volume"]["enabled"] is not True
        data["hourly_volume"] = {
            "enabled": enabled,
            "armed_after": time.time(),
            "last_processed": 0,
        }
        if not self._write(data):
            raise OSError("Hourly volume alarm could not be saved")
        return enabled

    def process_hourly_volume_close(self, timestamp: int, qualifies: bool, *, not_before: float) -> bool:
        data = self._read()
        alarm = data["hourly_volume"]
        if alarm["enabled"] is not True or timestamp <= max(
            not_before, alarm["armed_after"], alarm["last_processed"]
        ):
            return False
        alarm["last_processed"] = timestamp
        if qualifies:
            alarm["enabled"] = False
        if not self._write(data):
            raise OSError("Hourly volume alarm result could not be saved")
        return qualifies

    def is_setting_enabled(self, setting_name: str) -> bool:
        data = self._read()
        return data.get("settings", {}).get(setting_name, False)

    def set_setting(self, setting_name: str, value: bool) -> bool:
        data = self._read()
        data.setdefault("settings", {})[setting_name] = value
        self._write(data)
        logger.info(f"Set setting '{setting_name}' to {value}")
        return value

    def toggle_setting(self, setting_name: str) -> bool:
        data = self._read()
        current = data.get("settings", {}).get(setting_name, False)
        new_val = not current
        data.setdefault("settings", {})[setting_name] = new_val
        self._write(data)
        logger.info(f"Toggled setting '{setting_name}' to {new_val}")
        return new_val

    def add_price_alert(
        self,
        target_price: float,
        current_price: Optional[float] = None,
        side: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates a Level Rejection & False Breakout alert.
        If target_price >= current_price, side is 'resistance'.
        If target_price < current_price, side is 'support'.
        """
        data = self._read()
        alert_id = data.get("next_alert_id", 1)
        created_str = time.strftime("%H:%M UTC", time.gmtime())

        target = round(float(target_price), 2)
        if side and side.lower() in ("resistance", "support"):
            resolved_side = side.lower()
        elif current_price is not None:
            resolved_side = "resistance" if target >= float(current_price) else "support"
        else:
            resolved_side = "resistance"

        alert = {
            "id": alert_id,
            "target_price": target,
            "side": resolved_side,
            "state": "ACTIVE",  # ACTIVE | PENDING_CONFIRMATION
            "breakout_candle": None,
            "created_at": created_str,
        }
        data["price_alerts"].append(alert)
        data["next_alert_id"] = alert_id + 1
        self._write(data)
        logger.info(f"Added Level Rejection alert #{alert_id}: {resolved_side.upper()} ${target:,.2f}")
        return alert

    def remove_price_alert(self, alert_id: int) -> bool:
        data = self._read()
        initial_len = len(data["price_alerts"])
        data["price_alerts"] = [a for a in data["price_alerts"] if a["id"] != alert_id]
        if len(data["price_alerts"]) < initial_len:
            self._write(data)
            logger.info(f"Removed price alert #{alert_id}")
            return True
        return False

    def clear_all_price_alerts(self) -> int:
        data = self._read()
        count = len(data["price_alerts"])
        data["price_alerts"] = []
        self._write(data)
        logger.info(f"Cleared all {count} price alerts")
        return count

    def get_active_price_alerts(self) -> List[Dict[str, Any]]:
        data = self._read()
        return data.get("price_alerts", [])

    def check_price_alerts(
        self,
        candle: Dict[str, Any],
        interval_seconds: int = 900
    ) -> List[Dict[str, Any]]:
        """
        Evaluates active Level Rejection & False Breakout alerts on a closed candle.
        
        Rules:
        1. Resistance Level P (Upper):
           - Wick Touch: High >= P and max(Open, Close) < P -> Triggered!
           - Body Breakout: Close >= P -> Move to PENDING_CONFIRMATION (save breakout candle).
           - Pending Check on next candle:
             - If next candle is RED and Close < P -> Triggered (False Breakout / Rejection)!
             - If next candle is GREEN or Close >= P -> Breached (silently discarded).
        
        2. Support Level P (Lower):
           - Wick Touch: Low <= P and min(Open, Close) > P -> Triggered!
           - Body Breakdown: Close <= P -> Move to PENDING_CONFIRMATION (save breakout candle).
           - Pending Check on next candle:
             - If next candle is GREEN and Close > P -> Triggered (False Breakdown / Rejection)!
             - If next candle is RED or Close <= P -> Breached (silently discarded).
        """
        data = self._read()
        active_alerts = data.get("price_alerts", [])
        if not active_alerts:
            return []

        c_open = candle["open"]
        c_high = candle["high"]
        c_low = candle["low"]
        c_close = candle["close"]
        c_is_green = candle.get("is_green", (c_close > c_open))
        c_ts = int(candle.get("timestamp", time.time()))

        triggered = []
        remaining = []
        modified = False

        for alert in active_alerts:
            target = alert["target_price"]
            side = alert.get("side", "resistance")
            state = alert.get("state", "ACTIVE")

            # ---------------------------------------------------------
            # State: PENDING_CONFIRMATION (Evaluating immediate next bar)
            # ---------------------------------------------------------
            if state == "PENDING_CONFIRMATION":
                b_candle = alert.get("breakout_candle") or {}
                b_ts = int(b_candle.get("timestamp", 0))

                # Ensure this is the immediate next candle window (allowing buffer for jitter)
                is_immediate_next = (0 < (c_ts - b_ts) <= interval_seconds * 1.5)

                if side == "resistance":
                    # Breakout was above level. Rejection requires:
                    # 1. Opposite color: RED (Close < Open)
                    # 2. Closes back outside zone: Close < target
                    if is_immediate_next and (not c_is_green) and (c_close < target):
                        logger.info(f"Alert #{alert['id']} FALSE BREAKOUT REJECTION confirmed at ${target:,.2f} by Red close ${c_close:,.2f}")
                        triggered_event = alert.copy()
                        triggered_event["trigger_type"] = "FALSE_BREAKOUT"
                        triggered_event["trigger_candle"] = candle
                        triggered_event["breakout_candle"] = b_candle
                        triggered.append(triggered_event)
                        modified = True
                        # Alert completed, do not keep in remaining
                        continue
                    else:
                        # Breached zone: either failed opposite color, failed to close below, or missed bar
                        logger.info(f"Alert #{alert['id']} resistance level ${target:,.2f} ZONE BREACHED. Alert discarded silently.")
                        modified = True
                        # Discard silently, do not keep in remaining
                        continue

                elif side == "support":
                    # Breakdown was below level. Rejection requires:
                    # 1. Opposite color: GREEN (Close > Open)
                    # 2. Closes back outside zone: Close > target
                    if is_immediate_next and c_is_green and (c_close > target):
                        logger.info(f"Alert #{alert['id']} FALSE BREAKDOWN REJECTION confirmed at ${target:,.2f} by Green close ${c_close:,.2f}")
                        triggered_event = alert.copy()
                        triggered_event["trigger_type"] = "FALSE_BREAKOUT"
                        triggered_event["trigger_candle"] = candle
                        triggered_event["breakout_candle"] = b_candle
                        triggered.append(triggered_event)
                        modified = True
                        # Alert completed
                        continue
                    else:
                        logger.info(f"Alert #{alert['id']} support level ${target:,.2f} ZONE BREACHED. Alert discarded silently.")
                        modified = True
                        # Discard silently
                        continue

            # ---------------------------------------------------------
            # State: ACTIVE (Evaluating first interaction with level)
            # ---------------------------------------------------------
            if side == "resistance":
                # Resistance level above:
                if c_high < target:
                    # Level not reached
                    remaining.append(alert)
                    continue

                body_top = max(c_open, c_close)
                if body_top < target:
                    # 1. Wick Touch: High touched/crossed target, but body closed strictly below
                    logger.info(f"Alert #{alert['id']} RESISTANCE WICK TOUCH at ${target:,.2f} (High ${c_high:,.2f}, Body top ${body_top:,.2f})")
                    triggered_event = alert.copy()
                    triggered_event["trigger_type"] = "WICK_TOUCH"
                    triggered_event["trigger_candle"] = candle
                    triggered.append(triggered_event)
                    modified = True
                    # Alert completed
                    continue

                elif c_close >= target:
                    # 2. Body Breakout: Candle closed above or at level with body
                    logger.info(f"Alert #{alert['id']} resistance ${target:,.2f} BROKEN WITH BODY (Close ${c_close:,.2f}). Waiting confirmation on next bar.")
                    alert["state"] = "PENDING_CONFIRMATION"
                    alert["breakout_candle"] = {
                        "open": c_open,
                        "high": c_high,
                        "low": c_low,
                        "close": c_close,
                        "is_green": c_is_green,
                        "time_str": candle.get("time_str", ""),
                        "timestamp": c_ts,
                    }
                    remaining.append(alert)
                    modified = True
                    continue
                else:
                    # Opened above but closed below target (rare gap or volatile bar)
                    remaining.append(alert)
                    continue

            elif side == "support":
                # Support level below:
                if c_low > target:
                    # Level not reached
                    remaining.append(alert)
                    continue

                body_bottom = min(c_open, c_close)
                if body_bottom > target:
                    # 1. Wick Touch: Low touched/crossed target, but body closed strictly above
                    logger.info(f"Alert #{alert['id']} SUPPORT WICK TOUCH at ${target:,.2f} (Low ${c_low:,.2f}, Body bottom ${body_bottom:,.2f})")
                    triggered_event = alert.copy()
                    triggered_event["trigger_type"] = "WICK_TOUCH"
                    triggered_event["trigger_candle"] = candle
                    triggered.append(triggered_event)
                    modified = True
                    # Alert completed
                    continue

                elif c_close <= target:
                    # 2. Body Breakdown: Candle closed below or at level with body
                    logger.info(f"Alert #{alert['id']} support ${target:,.2f} BROKEN WITH BODY (Close ${c_close:,.2f}). Waiting confirmation on next bar.")
                    alert["state"] = "PENDING_CONFIRMATION"
                    alert["breakout_candle"] = {
                        "open": c_open,
                        "high": c_high,
                        "low": c_low,
                        "close": c_close,
                        "is_green": c_is_green,
                        "time_str": candle.get("time_str", ""),
                        "timestamp": c_ts,
                    }
                    remaining.append(alert)
                    modified = True
                    continue
                else:
                    remaining.append(alert)
                    continue

        if modified:
            data["price_alerts"] = remaining
            self._write(data)

        return triggered

alert_manager = AlertManager()
