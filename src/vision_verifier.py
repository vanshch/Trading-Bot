"""Gemini Vision AI integration for trade verification with structured JSON schema output."""

import asyncio
import json
import logging
import re
from typing import Any, Literal

import cv2
import numpy as np
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from src.config import AIConfig

logger = logging.getLogger("trade_bot.vision_verifier")

TRADE_VERIFICATION_PROMPT = """Analyze this live trading chart screenshot to determine if there is a genuine ACTIVE / OPEN filled trade position.

Key Verification Rules:
1. ACTIVE FILLED POSITION (COUNTS AS AN ACTIVE TRADE):
   - Look for a genuine filled active position row or badge on the chart (e.g., TradingView position tag showing filled entry price, position quantity such as -100 or +qty, live floating PnL such as +40.00 USD, and a close "X" control).
   - Horizontal position entry line (typically Red for Short/Sell, Blue/Green for Long/Buy) associated with an active filled execution.
   - Broker/platform position tracker widgets showing filled open quantities and live PnL.

2. NON-TRADE / INSUFFICIENT EVIDENCE (DO NOT COUNT AS AN ACTIVE TRADE):
   - Pending Limit Orders alone (e.g., "Sell Limit", "Buy Limit", unfilled resting orders) are NOT active trades.
   - TradingView Risk/Reward drawings alone (e.g., Long/Short Position planning tool, red risk / green or teal profit projection zones, target/stop levels) are hypothetical visual planning drawings and DO NOT prove an active live trade.
   - Support/resistance horizontal lines, trend lines, moving averages, or indicator levels alone are not trades.

3. OVERLAYS TO IGNORE:
   - Completely ignore video player chrome and controls (e.g., YouTube progress bar, seek bar, timecode, live indicator, player controls at the bottom).
   - Completely ignore presenter webcam/avatar, QR codes, social media handles, promotional banners, and disclaimer watermarks.

Output Requirements:
- "is_trade_active": true ONLY if a confirmed filled open position is visible on the chart; false otherwise.
- "direction": MUST be "BUY" (for Long/Call), "SELL" (for Short/Put), or "NONE". If is_trade_active is false, direction MUST be "NONE".
- "instrument": The financial ticker or instrument name (e.g., NIFTY, BANKNIFTY, BTCUSDT, EURUSD, XAUUSD, CRUDEOIL, ES, MES, NQ). If unknown, use "UNKNOWN".
- "entry_or_pnl": Concise summary of detected entry price, position size, and live PnL (e.g., "Short -100 @ 4353.26, PnL +40.00 USD").
- "confidence": Float between 0.0 and 1.0. Set 0.0 to 0.3 if no active trade or only drawings/pending orders are present; set 0.7 to 1.0 if a genuine active position is confirmed.
- "reasoning": Concise factual explanation distinguishing active filled positions from pending orders or drawings.
"""


class TradeVerification(BaseModel):
    """Structured validated trade verification result from Gemini Vision."""

    is_trade_active: bool = Field(
        ..., description="Whether a valid open trade position is detected"
    )
    direction: Literal["BUY", "SELL", "NONE"] = Field(
        default="NONE", description="Trade direction: BUY (Long), SELL (Short), or NONE"
    )
    instrument: str = Field(
        default="UNKNOWN",
        max_length=100,
        description="Financial instrument or ticker name",
    )
    entry_or_pnl: str = Field(
        default="",
        max_length=250,
        description="Detected entry price, current PnL, or position size details",
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0"
    )
    reasoning: str = Field(
        default="",
        max_length=500,
        description="Brief explanation of the visual findings",
    )

    @field_validator("direction", mode="before")
    @classmethod
    def normalize_direction(cls, v: Any) -> str:
        if not isinstance(v, str):
            return "NONE"
        clean = v.upper().strip()
        if clean in ("BUY", "LONG", "CALL", "B"):
            return "BUY"
        if clean in ("SELL", "SHORT", "PUT", "S"):
            return "SELL"
        return "NONE"

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, v: Any) -> float:
        try:
            val = float(v)
            return max(0.0, min(1.0, val))
        except (ValueError, TypeError):
            return 0.0

    @model_validator(mode="after")
    def validate_active_direction(self) -> "TradeVerification":
        if self.is_trade_active and self.direction == "NONE":
            raise ValueError("An active trade must have BUY or SELL direction")
        if not self.is_trade_active:
            self.direction = "NONE"
        return self


def extract_json_from_text(text: str) -> dict[str, Any]:
    """Deterministically parse JSON from LLM output, stripping markdown fences if present."""
    clean = text.strip()

    # Remove markdown code block fences if present
    if "```" in clean:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", clean, re.DOTALL)
        if match:
            clean = match.group(1).strip()
        else:
            # Fallback: strip leading and trailing backticks
            clean = re.sub(r"^```(?:json)?\s*", "", clean)
            clean = re.sub(r"\s*```$", "", clean).strip()

    # Find the outermost JSON object if there's surrounding text
    start = clean.find("{")
    end = clean.rfind("}")
    if start != -1 and end != -1 and end > start:
        clean = clean[start : end + 1]

    return json.loads(clean)


class VisionVerifier:
    """Multimodal Vision verifier using Gemini Vision API with structured extraction."""

    def __init__(
        self,
        api_key: str,
        config: AIConfig | None = None,
        client: Any = None,
    ) -> None:
        self.api_key = api_key
        self.config = config or AIConfig()
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from google import genai

        self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def close(self) -> None:
        """Close the lazily-created SDK client and its connection pool."""
        if self._owns_client and self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                await asyncio.to_thread(close)
            self._client = None

    async def check_connection(self) -> bool:
        """Verify that the API key can access the configured Gemini model."""
        client = self._get_client()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.get,
                    model=self.config.model_name,
                ),
                timeout=self.config.timeout_seconds,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - SDK boundary exposes varied exceptions
            logger.error(
                "Gemini preflight failed for model %s (%s)",
                self.config.model_name,
                type(exc).__name__,
            )
            return False

    @staticmethod
    def _is_transient_error(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status in {408, 409, 425, 429, 500, 502, 503, 504}:
            return True
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "408",
                "429",
                "500",
                "502",
                "503",
                "504",
                "timeout",
                "temporarily unavailable",
                "connection reset",
                "rate limit",
            )
        )

    def _frame_to_jpeg_bytes(self, frame: np.ndarray | bytes) -> bytes:
        """Convert input frame representation to JPEG byte buffer."""
        if isinstance(frame, bytes):
            return frame
        elif isinstance(frame, np.ndarray):
            # frame is in BGR format from OpenCV
            success, encoded = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90]
            )
            if not success or encoded is None:
                raise ValueError("Failed to encode numpy array frame to JPEG format")
            return encoded.tobytes()
        else:
            raise TypeError(f"Unsupported frame type: {type(frame)}")

    async def verify_frame(
        self, frame: np.ndarray | bytes
    ) -> TradeVerification:
        """Analyze a chart frame with Gemini Vision and return structured TradeVerification.

        Handles transient network and rate-limit errors with exponential backoff.
        """
        jpeg_bytes = self._frame_to_jpeg_bytes(frame)
        client = self._get_client()

        last_error: Exception | None = None
        delay = self.config.retry_delay_seconds
        attempts_made = 0

        for attempt in range(1, self.config.max_retries + 1):
            attempts_made = attempt
            try:
                # Call Gemini model in thread pool to prevent blocking the async loop
                response = await asyncio.to_thread(
                    self._call_gemini_sync, client, jpeg_bytes
                )
                return self._parse_response(response)
            except Exception as exc:  # noqa: BLE001 - SDK boundary exposes varied exceptions
                last_error = exc
                logger.warning(
                    "Gemini API call attempt %s/%s failed (%s)",
                    attempt,
                    self.config.max_retries,
                    type(exc).__name__,
                )
                if not self._is_transient_error(exc):
                    logger.error(
                        "Gemini request failed permanently; retries suppressed"
                    )
                    break
                if attempt < self.config.max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2.0  # Exponential backoff

        logger.error(
            "Gemini Vision verification unavailable after %s attempt(s) (%s)",
            attempts_made,
            type(last_error).__name__ if last_error else "unknown error",
        )
        return TradeVerification(
            is_trade_active=False,
            direction="NONE",
            instrument="UNKNOWN",
            entry_or_pnl="",
            confidence=0.0,
            reasoning="AI verification unavailable after retries",
        )

    def _call_gemini_sync(self, client: Any, jpeg_bytes: bytes) -> Any:
        """Synchronous call to Gemini API using google-genai SDK."""
        from google.genai import types

        image_part = types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg")

        response = client.models.generate_content(
            model=self.config.model_name,
            contents=[image_part, TRADE_VERIFICATION_PROMPT],
            config=types.GenerateContentConfig(
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=TradeVerification,
                http_options=types.HttpOptions(
                    timeout=int(self.config.timeout_seconds * 1000)
                ),
            ),
        )
        return response

    def _parse_response(self, response: Any) -> TradeVerification:
        """Deterministically parse and validate raw Gemini response into TradeVerification."""
        text = ""
        if hasattr(response, "text") and response.text:
            text = response.text
        elif hasattr(response, "candidates") and response.candidates:
            # Fallback extraction from candidates
            candidate = response.candidates[0]
            if hasattr(candidate, "content") and candidate.content:
                parts = candidate.content.parts
                if parts and hasattr(parts[0], "text"):
                    text = parts[0].text

        if not text:
            logger.warning("Empty response received from Gemini Vision model")
            return TradeVerification(
                is_trade_active=False,
                direction="NONE",
                instrument="UNKNOWN",
                entry_or_pnl="",
                confidence=0.0,
                reasoning="Empty response from vision model",
            )

        try:
            parsed_json = extract_json_from_text(text)
            verification = TradeVerification.model_validate(parsed_json)

            # Check if confidence meets configured threshold
            if (
                verification.is_trade_active
                and verification.confidence < self.config.confidence_threshold
            ):
                logger.info(
                    f"Trade detected but confidence ({verification.confidence:.2f}) is below threshold ({self.config.confidence_threshold:.2f}). Suppressing."
                )
                verification.is_trade_active = False
                verification.direction = "NONE"

            return verification
        except (
            json.JSONDecodeError,
            ValidationError,
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            logger.warning(
                "Failed to parse structured Gemini response (%s)", type(exc).__name__
            )
            return TradeVerification(
                is_trade_active=False,
                direction="NONE",
                instrument="UNKNOWN",
                entry_or_pnl="",
                confidence=0.0,
                reasoning=f"JSON parsing error: {exc}",
            )
