import os
import sys
import time
import logging
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Configure Logging
log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=getattr(logging, log_level_name, logging.INFO)
)
logger = logging.getLogger("xauusd_bot")
# HTTPX request URLs include Telegram credentials; suppress request-level logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USER_ID_STR = os.getenv("ALLOWED_USER_ID", "").strip()
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
DEFAULT_TIMEFRAME = os.getenv("DEFAULT_TIMEFRAME", "15m").strip().lower()
# Calibration offset to align Twelve Data institutional mid-price feed with TradingView OANDA spot quotes
# (Empirical analysis showed Twelve Data sits +$0.50 to +$0.60 higher than OANDA; -0.50 calibrates it to OANDA)
OANDA_PRICE_OFFSET = float(os.getenv("OANDA_PRICE_OFFSET", "-0.50"))

# Optional chart enrichment. Credentials stay in .env; off until device testing passes.
CHART_SCREENSHOTS_ENABLED = os.getenv("CHART_SCREENSHOTS_ENABLED", "false").lower() in ("true", "1", "yes")
BROWSERLESS_API_TOKEN = os.getenv("BROWSERLESS_API_TOKEN", "").strip()
BROWSERLESS_ENDPOINT = os.getenv("BROWSERLESS_ENDPOINT", "https://production-sfo.browserless.io").strip()
BROWSERLESS_PROFILE = os.getenv("BROWSERLESS_PROFILE", "tradingview-test").strip()
TRADINGVIEW_CHART_URL = os.getenv("TRADINGVIEW_CHART_URL", "https://www.tradingview.com/chart/").strip()


is_testing = bool(os.getenv("TESTING") or os.getenv("PYTEST_CURRENT_TEST") or "unittest" in sys.modules)
if not is_testing:
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is missing in .env")

    if not ALLOWED_USER_ID_STR:
        raise ValueError("ALLOWED_USER_ID is missing in .env")

    try:
        ALLOWED_USER_ID = int(ALLOWED_USER_ID_STR)
    except ValueError:
        raise ValueError(f"ALLOWED_USER_ID must be a numeric integer, got: {ALLOWED_USER_ID_STR}")
else:
    ALLOWED_USER_ID = int(ALLOWED_USER_ID_STR) if ALLOWED_USER_ID_STR.isdigit() else 123456789

VALID_TIMEFRAMES = ["5m", "15m", "1h", "4h"]
if DEFAULT_TIMEFRAME not in VALID_TIMEFRAMES:
    DEFAULT_TIMEFRAME = "15m"

def get_interval_seconds(tf: str) -> int:
    mapping = {
        "5m": 5 * 60,
        "15m": 15 * 60,
        "1h": 60 * 60,
        "4h": 4 * 60 * 60,
    }
    return mapping.get(tf, 15 * 60)

class BotState:
    def __init__(self):
        self.alerts_enabled: bool = True
        self.current_timeframe: str = DEFAULT_TIMEFRAME
        self.last_alerted_candle_time: str = ""
        self.last_feed_source: str = "Initializing..."
        self.bot_start_time: float = time.time()
        self.timeframe_changed_event: asyncio.Event = asyncio.Event()

    def toggle_alerts(self) -> bool:
        self.alerts_enabled = not self.alerts_enabled
        return self.alerts_enabled

    def cycle_timeframe(self) -> str:
        idx = VALID_TIMEFRAMES.index(self.current_timeframe)
        self.current_timeframe = VALID_TIMEFRAMES[(idx + 1) % len(VALID_TIMEFRAMES)]
        self.timeframe_changed_event.set()
        return self.current_timeframe

    def get_uptime_str(self) -> str:
        elapsed = int(time.time() - self.bot_start_time)
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        days, hours = divmod(hours, 24)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m {seconds}s"

state = BotState()
