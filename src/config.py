"""Configuration loader and validated dataclasses for YouTube Live Stream Trade Monitoring Bot."""

import os
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class ConfigurationError(Exception):
    """Raised when application configuration is missing, invalid, or malformed."""


class ChannelMode(str, Enum):
    """Monitoring mode for a configured YouTube channel.

    - FULL_ANALYSIS: Full trade analysis pipeline including live status notifications,
      real-time audio translation, English keyword detection, Gemini multimodal
      verification, and trade alerts.
    - NOTIFICATIONS_ONLY: Live status notifications only via Telegram without audio capture,
      audio translation, Gemini verification, session analysis, or trade alerts.
    """

    FULL_ANALYSIS = "full_analysis"
    NOTIFICATIONS_ONLY = "notifications_only"


class ChannelConfig(BaseModel):
    """Configuration for an individual YouTube channel or live stream."""

    name: str = Field(
        ..., min_length=1, description="Friendly identifier for the trader/channel"
    )
    url: str = Field(..., min_length=5, description="YouTube live or channel URL")
    enabled: bool = Field(
        default=True, description="Whether monitoring is active for this channel"
    )
    mode: ChannelMode = Field(
        default=ChannelMode.FULL_ANALYSIS,
        description="Monitoring mode: 'full_analysis' (full trade analysis) or 'notifications_only' (live status notifications only)",
    )
    trigger_keywords: list[str] = Field(
        default_factory=list,
        description="Channel-specific trigger keywords/phrases to detect trade entry",
    )
    trader_profile: str | None = Field(
        default=None,
        description="Custom context, style notes, or instructions for this trader",
    )

    @field_validator("url")
    @classmethod
    def validate_youtube_url(cls, v: str) -> str:
        v = v.strip()
        parsed = urlparse(v)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https":
            raise ValueError("Channel URL must use HTTPS")
        if not (
            host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")
        ):
            raise ValueError("Channel URL must point to youtube.com or youtu.be")
        return v


class CaptureConfig(BaseModel):
    """Video stream frame capture parameters."""

    screenshots_enabled: bool = Field(
        default=True,
        description="Whether trade triggers may open the video stream to capture chart frames",
    )

    frame_interval_seconds: float = Field(
        default=5.0, gt=0.0, description="Interval between captured frames"
    )
    live_poll_interval_seconds: float = Field(
        default=60.0, gt=0.0, description="Interval to poll offline channels"
    )
    stream_timeout_seconds: float = Field(
        default=15.0, gt=0.0, description="Network timeout for stream resolution"
    )
    max_consecutive_stream_errors: int = Field(
        default=5, ge=1, description="Threshold before resetting stream capture"
    )
    restart_initial_delay_seconds: float = Field(
        default=1.0,
        gt=0.0,
        description="Initial retry delay in seconds for exponential restart backoff",
    )
    restart_max_delay_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Maximum retry delay in seconds for exponential restart backoff",
    )
    restart_backoff_factor: float = Field(
        default=2.0,
        ge=1.0,
        description="Multiplier for exponential restart backoff",
    )
    restart_reset_window_seconds: float = Field(
        default=300.0,
        gt=0.0,
        description="Sustained healthy capture duration in seconds before resetting backoff",
    )
    restart_jitter_ratio: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Bounded random jitter ratio for restart delays (0.0 in unit tests)",
    )

    @model_validator(mode="after")
    def check_restart_delay_bounds(self) -> "CaptureConfig":
        if self.restart_max_delay_seconds < self.restart_initial_delay_seconds:
            raise ValueError(
                "restart_max_delay_seconds must be greater than or equal to "
                "restart_initial_delay_seconds"
            )
        return self


class ROICropConfig(BaseModel):
    """Normalized Region of Interest (0.0 to 1.0) for chart area focusing."""

    top: float = Field(default=0.0, ge=0.0, le=1.0)
    bottom: float = Field(default=1.0, ge=0.0, le=1.0)
    left: float = Field(default=0.0, ge=0.0, le=1.0)
    right: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def check_roi_bounds(self) -> "ROICropConfig":
        if self.top >= self.bottom:
            raise ValueError(
                f"ROI top ({self.top}) must be strictly less than bottom ({self.bottom})"
            )
        if self.left >= self.right:
            raise ValueError(
                f"ROI left ({self.left}) must be strictly less than right ({self.right})"
            )
        return self


class HSVRange(BaseModel):
    """Lower and upper HSV bounds for color segmentation."""

    lower: list[int] = Field(..., min_length=3, max_length=3)
    upper: list[int] = Field(..., min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_hsv_ranges(self) -> "HSVRange":
        h_low, s_low, v_low = self.lower
        h_high, s_high, v_high = self.upper

        if not (0 <= h_low <= 180 and 0 <= h_high <= 180):
            raise ValueError(
                f"Hue values must be between 0 and 180, got lower={h_low}, upper={h_high}"
            )
        if not (0 <= s_low <= 255 and 0 <= s_high <= 255):
            raise ValueError(
                f"Saturation values must be between 0 and 255, got lower={s_low}, upper={s_high}"
            )
        if not (0 <= v_low <= 255 and 0 <= v_high <= 255):
            raise ValueError(
                f"Value/Brightness values must be between 0 and 255, got lower={v_low}, upper={v_high}"
            )
        return self


class CVFilterConfig(BaseModel):
    """OpenCV color masking and horizontal line detection settings."""

    min_line_length_ratio: float = Field(
        default=0.10,
        gt=0.01,
        le=1.0,
        description="Minimum line length relative to frame width",
    )
    max_line_angle_degrees: float = Field(
        default=5.0,
        ge=0.0,
        le=45.0,
        description="Maximum deviation from horizontal in degrees",
    )
    roi_crop: ROICropConfig | None = Field(
        default_factory=lambda: ROICropConfig(top=0.1, bottom=0.80, left=0.1, right=0.9)
    )
    blue_hsv_ranges: list[HSVRange] = Field(
        default_factory=lambda: [HSVRange(lower=[95, 80, 80], upper=[135, 255, 255])]
    )
    red_hsv_ranges: list[HSVRange] = Field(
        default_factory=lambda: [
            HSVRange(lower=[0, 80, 80], upper=[10, 255, 255]),
            HSVRange(lower=[170, 80, 80], upper=[180, 255, 255]),
        ]
    )
    morph_kernel_width: int = Field(
        default=15,
        ge=3,
        description="Horizontal kernel width for morphological filtering",
    )
    morph_kernel_height: int = Field(
        default=1,
        ge=1,
        description="Vertical kernel height for morphological filtering",
    )
    hough_threshold: int = Field(
        default=25, ge=5, description="Accumulator threshold for HoughLinesP"
    )
    hough_min_line_length: int = Field(
        default=30, ge=10, description="Minimum line segment length in pixels"
    )
    hough_max_line_gap: int = Field(
        default=10,
        ge=1,
        description="Maximum allowed gap between points on the same line",
    )


class AIConfig(BaseModel):
    """Gemini Vision AI verification settings."""

    model_name: str = Field(
        default="gemini-3.6-flash", min_length=1, description="Gemini Vision model name"
    )
    confidence_threshold: float = Field(
        default=0.70, ge=0.0, le=1.0, description="Minimum confidence to fire alerts"
    )
    max_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max retry attempts on transient API failures",
    )
    retry_delay_seconds: float = Field(
        default=2.0, gt=0.0, description="Initial retry delay in seconds"
    )
    timeout_seconds: float = Field(
        default=15.0, gt=1.0, description="Per-request timeout in seconds"
    )


class AudioSTTConfig(BaseModel):
    """Groq Speech-To-Text / Audio Translation (Whisper) and streaming parameters."""

    model: str = Field(
        default="whisper-large-v3",
        min_length=1,
        description="Groq Whisper model name for English translation (e.g. whisper-large-v3)",
    )
    chunk_duration_seconds: float = Field(
        default=10.0,
        gt=0.5,
        le=30.0,
        description="Duration of each live audio chunk processed in seconds",
    )
    context_buffer_seconds: int = Field(
        default=180,
        ge=30,
        le=600,
        description="Rolling window duration for recent spoken context retained in memory",
    )
    language: str | None = Field(
        default=None,
        description="Language code hint (unused with /translations endpoint; kept for backward compatibility)",
    )
    initial_prompt: str = Field(
        default="Trade active. Entry taken. Buy executed. Sell executed. Short position. Long position. Stop loss. Target hit. Order placed. Scalp. Scalping. Book profit. Move SL to CTC. Trade closed. Gold, XAUUSD, BTC, Bitcoin, Forex.",
        description="Initial vocabulary prompt to bias Whisper translation toward English trading terms and tickers",
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Sampling temperature for translation (0 for deterministic output)",
    )
    timeout_seconds: float = Field(
        default=10.0,
        gt=1.0,
        description="Per-request HTTP timeout in seconds",
    )
    max_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max retry attempts on transient API failures",
    )
    retry_delay_seconds: float = Field(
        default=1.0,
        gt=0.0,
        description="Initial retry delay in seconds for exponential backoff",
    )


class TelegramConfig(BaseModel):
    """Telegram alert dispatcher settings."""

    include_screenshot: bool = Field(
        default=True, description="Whether to attach frame screenshot to alerts"
    )
    caption_timezone: str = Field(
        default="Asia/Kolkata", description="Timezone name for alert timestamps"
    )
    max_retries: int = Field(
        default=3, ge=1, le=10, description="Max retry attempts for dispatch"
    )
    retry_delay_seconds: float = Field(
        default=2.0, gt=0.0, description="Initial retry delay in seconds"
    )
    timeout_seconds: float = Field(
        default=10.0, gt=1.0, description="Network timeout for Telegram requests"
    )


class AppConfig(BaseModel):
    """Root application configuration combining YAML config and environment secrets."""

    channels: list[ChannelConfig] = Field(
        ..., min_length=1, description="Configured channels to monitor"
    )
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    cv_filter: CVFilterConfig = Field(default_factory=CVFilterConfig)
    audio: AudioSTTConfig = Field(default_factory=AudioSTTConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

    # Secrets from environment
    groq_api_key: SecretStr = Field(..., description="Groq Speech-to-Text API key")
    groq_api_keys: list[SecretStr] = Field(
        default_factory=list,
        description="Pool of Groq Speech-to-Text API keys for round-robin rotation and fallback",
    )
    gemini_api_key: SecretStr = Field(..., description="Google Gemini Vision API key")
    telegram_bot_token: SecretStr = Field(..., description="Telegram bot API token")
    telegram_chat_id: str = Field(
        ..., min_length=1, description="Telegram target chat or user ID"
    )
    log_level: str = Field(default="INFO")

    @field_validator("telegram_chat_id")
    @classmethod
    def validate_chat_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("telegram_chat_id cannot be empty")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        v = v.upper().strip()
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v not in valid:
            raise ValueError(f"Invalid log_level '{v}'. Must be one of {valid}")
        return v

def mask_secret(secret: str | SecretStr) -> str:
    """Safely mask sensitive strings for display/logging without secret leakage."""
    val = secret.get_secret_value() if isinstance(secret, SecretStr) else str(secret)
    if not val:
        return "<empty>"
    if len(val) <= 8:
        return "***"
    return f"{val[:3]}...{val[-3:]}"


def load_config(
    yaml_path: str | Path | None = None,
    env_path: str | Path | None = None,
) -> AppConfig:
    """Load, validate, and return the application configuration fail-fast.

    Args:
        yaml_path: Optional path to config.yaml (defaults to ./config.yaml).
        env_path: Optional path to .env file (defaults to ./.env).

    Returns:
        AppConfig: Fully validated configuration instance.

    Raises:
        ConfigurationError: If any required settings are missing, malformed, or invalid.
    """
    if env_path is not None:
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()

    yaml_file = Path(yaml_path) if yaml_path else Path("config.yaml")
    raw_yaml: dict[str, Any] = {}

    if yaml_file.exists():
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    raw_yaml = loaded
                elif loaded is not None:
                    raise ConfigurationError(
                        f"Configuration file {yaml_file} must be a YAML mapping/dict."
                    )
        except yaml.YAMLError as exc:
            raise ConfigurationError(
                f"Failed to parse YAML file {yaml_file}: {exc}"
            ) from exc
        except Exception as exc:
            raise ConfigurationError(
                f"Error reading configuration file {yaml_file}: {exc}"
            ) from exc
    else:
        raise ConfigurationError(
            f"Required configuration file not found at: {yaml_file.resolve()}"
        )

    # Parse Groq API key pool (supporting both GROQ_API_KEYS and legacy GROQ_API_KEY)
    groq_keys_raw = os.getenv("GROQ_API_KEYS", "").strip()
    groq_keys: list[str] = (
        [k.strip() for k in groq_keys_raw.split(",") if k.strip()]
        if groq_keys_raw
        else []
    )
    single_groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if single_groq_key and single_groq_key not in groq_keys:
        groq_keys.insert(0, single_groq_key)

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    log_level = os.getenv("LOG_LEVEL", raw_yaml.get("log_level", "INFO")).strip()

    if not groq_keys:
        raise ConfigurationError(
            "Missing required environment variable 'GROQ_API_KEY' or 'GROQ_API_KEYS'. Please define it in your .env or environment."
        )
    if not gemini_key:
        raise ConfigurationError(
            "Missing required environment variable 'GEMINI_API_KEY'. Please define it in your .env or environment."
        )
    if not tg_token:
        raise ConfigurationError(
            "Missing required environment variable 'TELEGRAM_BOT_TOKEN'. Please define it in your .env or environment."
        )
    if not tg_chat_id:
        raise ConfigurationError(
            "Missing required environment variable 'TELEGRAM_CHAT_ID'. Please define it in your .env or environment."
        )

    raw_yaml["groq_api_key"] = groq_keys[0]
    raw_yaml["groq_api_keys"] = groq_keys
    raw_yaml["gemini_api_key"] = gemini_key
    raw_yaml["telegram_bot_token"] = tg_token
    raw_yaml["telegram_chat_id"] = tg_chat_id
    raw_yaml["log_level"] = log_level

    try:
        config = AppConfig.model_validate(raw_yaml)
        return config
    except Exception as exc:
        raise ConfigurationError(f"Configuration validation failed: {exc}") from exc


def load_benchmark_config(
    yaml_path: str | Path | None = None,
) -> tuple[CaptureConfig, CVFilterConfig]:
    """Load CaptureConfig and CVFilterConfig fail-fast without requiring provider credentials.

    Args:
        yaml_path: Optional path to config.yaml (defaults to ./config.yaml if it exists).

    Returns:
        tuple[CaptureConfig, CVFilterConfig]: Validated capture and cv_filter configurations.

    Raises:
        ConfigurationError: If the specified configuration file is missing, malformed, or invalid.
    """
    yaml_file = Path(yaml_path) if yaml_path else Path("config.yaml")
    raw_yaml: dict[str, Any] = {}

    if yaml_file.exists():
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict):
                    raw_yaml = loaded
                elif loaded is not None:
                    raise ConfigurationError(
                        f"Configuration file {yaml_file} must be a YAML mapping/dict."
                    )
        except yaml.YAMLError as exc:
            raise ConfigurationError(
                f"Failed to parse YAML file {yaml_file}: {exc}"
            ) from exc
        except Exception as exc:
            raise ConfigurationError(
                f"Error reading configuration file {yaml_file}: {exc}"
            ) from exc
    elif yaml_path is not None:
        raise ConfigurationError(
            f"Required configuration file not found at: {yaml_file.resolve()}"
        )

    try:
        capture_cfg = CaptureConfig.model_validate(raw_yaml.get("capture", {}))
        cv_cfg = CVFilterConfig.model_validate(raw_yaml.get("cv_filter", {}))
        return capture_cfg, cv_cfg
    except Exception as exc:
        raise ConfigurationError(f"Configuration validation failed: {exc}") from exc
