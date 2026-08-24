"""Gemini Multimodal Trade Verification integrating live screenshot and spoken commentary."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Literal

import cv2
import numpy as np
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from src.config import AIConfig

logger = logging.getLogger("trade_bot.multimodal_verifier")

MULTIMODAL_PROMPT_TEMPLATE = """You are an expert algorithmic trade auditor analyzing a live trading broadcast.
An audio speech-to-text trigger has flagged that the trader might have entered a live trade position.

Analyze BOTH:
1. The recent spoken transcript commentary from the trader leading up to this moment.
2. The live video frame / chart screenshot captured at the exact moment of the trigger.

Trader Profile / Channel Context:
{trader_profile}

Trigger Phrase Detected:
"{trigger_phrase}"

Recent Spoken Commentary (last 2-3 minutes):
\"\"\"
{transcript_context}
\"\"\"

Your Task:
- Determine if the trader has genuinely taken/entered an ACTIVE live trade (or explicitly executed an order).
- Extract the instrument / strike, direction (CALL/PUT/BUY/SELL), entry price or range, stop-loss (SL), and target (TGT) from the spoken context and/or visible chart/order book.
- Distinguish between hypothetical planning ("if it breaks 22400 we will buy") vs. ACTUAL execution ("bought", "entered", "order executed", "humne call le liya").

Output Schema:
Respond ONLY with a valid JSON object matching these exact keys:
{{
  "is_trade_active": true | false,
  "direction": "BUY" | "SELL" | "CALL" | "PUT" | "NONE",
  "instrument": "<ticker or strike, e.g. NIFTY 24500 CE, BANKNIFTY, BTCUSDT, UNKNOWN>",
  "entry_price": "<entry price/range or 'MARKET' if not specified, or null>",
  "stop_loss": "<stop loss level or null>",
  "target": "<target price or null>",
  "trigger_quote": "<the exact key spoken quote where entry was declared>",
  "confidence": <float 0.0 to 1.0>,
  "reasoning": "<concise explanation of spoken and visual evidence>"
}}
"""


class MultimodalTradeVerification(BaseModel):
    """Structured validated trade verification result combining visual and spoken data."""

    is_trade_active: bool = Field(
        ..., description="Whether a valid trade entry or active position is confirmed"
    )
    direction: Literal["BUY", "SELL", "CALL", "PUT", "NONE"] = Field(
        default="NONE", description="Trade direction or contract type"
    )
    instrument: str = Field(
        default="UNKNOWN",
        max_length=100,
        description="Financial instrument, index, or option strike name",
    )
    entry_price: str | None = Field(
        default=None,
        description="Detected entry price or execution range",
    )
    stop_loss: str | None = Field(
        default=None,
        description="Mentioned or charted stop loss level",
    )
    target: str | None = Field(
        default=None,
        description="Mentioned or charted target level",
    )
    trigger_quote: str = Field(
        default="",
        max_length=300,
        description="Key snippet spoken by the trader declaring the trade",
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0"
    )
    reasoning: str = Field(
        default="",
        max_length=600,
        description="Brief explanation of findings across audio and image",
    )

    @field_validator("direction", mode="before")
    @classmethod
    def normalize_direction(cls, v: Any) -> str:
        if not isinstance(v, str):
            return "NONE"
        clean = v.upper().strip()
        if clean in ("BUY", "LONG", "B"):
            return "BUY"
        if clean in ("SELL", "SHORT", "S"):
            return "SELL"
        if clean in ("CALL", "CE"):
            return "CALL"
        if clean in ("PUT", "PE"):
            return "PUT"
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
    def validate_active_direction(self) -> MultimodalTradeVerification:
        if self.is_trade_active and self.direction == "NONE":
            # Default to BUY if active but direction wasn't specifically parsed
            self.direction = "BUY"
        if not self.is_trade_active:
            self.direction = "NONE"
        return self


class MultimodalVerifier:
    """Evaluates live trade alerts by querying Gemini with both screenshot and spoken transcript."""

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
        """Lazily initialize Google GenAI SDK client."""
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def close(self) -> None:
        """Close the GenAI SDK client."""
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
                asyncio.to_thread(client.models.get, model=self.config.model_name),
                timeout=self.config.timeout_seconds,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            import logging as _log
            _log.getLogger("trade_bot.multimodal_verifier").error(
                "Gemini preflight failed for model %s (%s)",
                self.config.model_name,
                type(exc).__name__,
            )
            return False

    def _frame_to_jpeg_bytes(self, frame: np.ndarray | bytes) -> bytes:
        """Convert input frame representation to JPEG byte buffer."""
        if isinstance(frame, bytes):
            return frame
        elif isinstance(frame, np.ndarray):
            success, encoded = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90]
            )
            if not success or encoded is None:
                raise ValueError("Failed to encode numpy array frame to JPEG format")
            return encoded.tobytes()
        else:
            raise TypeError(f"Unsupported frame type: {type(frame)}")

    def _call_gemini_multimodal_sync(
        self,
        client: Any,
        jpeg_bytes: bytes,
        prompt_text: str,
        model_name: str | None = None,
    ) -> str:
        """Synchronous Gemini call sending screenshot image and transcript prompt."""
        from google import genai  # noqa: F401 (unused variable kept for type hint)
        from google.genai import types
        image_part = types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg")

        config = types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=MultimodalTradeVerification,
            http_options=types.HttpOptions(
                timeout=int(self.config.timeout_seconds * 1000)
            ),
        )

        target_model = model_name or self.config.model_name
        response = client.models.generate_content(
            model=target_model,
            contents=[image_part, prompt_text],
            config=config,
        )

        if not response or not response.text:
            raise ValueError("Empty response received from Gemini Multimodal API")

        return response.text

    def _parse_response(self, text_output: str) -> MultimodalTradeVerification:
        """Parse Gemini output into validated MultimodalTradeVerification."""
        try:
            verification = MultimodalTradeVerification.model_validate_json(text_output)
        except ValidationError:
            # Fallback JSON regex extraction
            match = re.search(r"\{.*\}", text_output, re.DOTALL)
            if match:
                raw_json = json.loads(match.group(0))
                verification = MultimodalTradeVerification.model_validate(raw_json)
            else:
                raise

        if (
            verification.is_trade_active
            and verification.confidence < self.config.confidence_threshold
        ):
            logger.info(
                "Multimodal trade confidence %.2f is below threshold %.2f; suppressing",
                verification.confidence,
                self.config.confidence_threshold,
            )
            verification.is_trade_active = False
            verification.direction = "NONE"

        return verification

    async def verify_multimodal_trade(
        self,
        frame: np.ndarray | bytes,
        transcript_context: str,
        trigger_phrase: str = "",
        trader_profile: str = "",
    ) -> MultimodalTradeVerification:
        """Analyze a chart frame + spoken transcript with Gemini and return structured verification."""
        jpeg_bytes = self._frame_to_jpeg_bytes(frame)
        prompt_text = MULTIMODAL_PROMPT_TEMPLATE.format(
            trader_profile=trader_profile or "General Financial Trader (Hindi/English)",
            trigger_phrase=trigger_phrase,
            transcript_context=transcript_context or "No transcript commentary available.",
        )

        models_to_try = [self.config.model_name]
        for fallback in ("gemini-2.5-flash", "gemini-3.7-flash"):
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        client = self._get_client()
        delay = self.config.retry_delay_seconds

        for attempt in range(1, self.config.max_retries + 1):
            model_to_use = models_to_try[(attempt - 1) % len(models_to_try)]
            try:
                raw_text = await asyncio.to_thread(
                    self._call_gemini_multimodal_sync,
                    client,
                    jpeg_bytes,
                    prompt_text,
                    model_to_use,
                )
                verification = self._parse_response(raw_text)
                logger.info(
                    f"Multimodal Verification result ({model_to_use}): is_active={verification.is_trade_active}, "
                    f"direction={verification.direction}, instrument={verification.instrument}, "
                    f"confidence={verification.confidence:.2f}"
                )
                return verification
            except Exception as exc:  # noqa: BLE001 - SDK boundary exposes varied exceptions
                logger.warning(
                    f"Gemini Multimodal attempt {attempt}/{self.config.max_retries} with model {model_to_use} failed: {exc}"
                )
                if attempt < self.config.max_retries:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2.0, 30.0)

        # Safe fallback
        return MultimodalTradeVerification(
            is_trade_active=False,
            direction="NONE",
            instrument="UNKNOWN",
            confidence=0.0,
            reasoning="Gemini verification failed or timed out",
        )
