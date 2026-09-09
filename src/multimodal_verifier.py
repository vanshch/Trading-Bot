"""Gemini Multimodal Trade Verification integrating live screenshot and spoken commentary."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Literal

import cv2
import numpy as np
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from src.config import AIConfig, LLMRoute
from src.llm_fallback import chat_completion, failure_policy, model_accessible

logger = logging.getLogger("trade_bot.multimodal_verifier")

SignalType = Literal["ENTRY", "PARTIAL_EXIT", "MOVE_STOP", "EXIT", "NONE"]

MULTIMODAL_PROMPT_TEMPLATE = """You are an expert algorithmic trade auditor analyzing a live trading broadcast.
An audio speech-to-text trigger has flagged that the trader might have entered a live trade position or given a position-management instruction.

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
- Classify the trader's statement into EXACTLY ONE signal_type:
  * "ENTRY": Trader confirms taking an active live entry or gives an immediate execution instruction (e.g., "You can enter with one lot. I am taking a small scalp.", "Bought here", "Entered now", "Humne call le liya").
  * "PARTIAL_EXIT": Trader executes or instructs booking partial profits / scaling out (e.g., "Book some profit and move SL to CTC", "Booking partial here", "Book 50%").
  * "MOVE_STOP": Trader executes or instructs moving the stop loss to CTC / breakeven or trailing SL (e.g., "Move SL to CTC", "SL cost to cost kar do", "Trail stop to breakeven").
  * "EXIT": Trader explicitly closes, exits, or cuts the entire remaining position (e.g., "My remaining trade is closed/cut", "Exited full position", "Position closed", "Target hit out", "SL hit cut").
  * "NONE": No live execution or management action taking place.

STRICT FAIL-CLOSED RULES (Output signal_type: "NONE", confidence: 0.0 to 0.3):
1. REJECT Conditional / Hypothetical Statements: Statements with "if", "jab tak", "agar", planning hypothetical setups (e.g., "If it breaks 37 we will buy", "Agar 24500 break hoga tab enter karenge").
2. REJECT Negated / Prohibitive Statements: Warnings not to execute (e.g., "Do not enter", "Don't buy yet", "No entry here").
3. REJECT Educational / Retrospective-only Analysis: Explaining past chart history, theory, or general concepts (e.g., "Yesterday 37 was strong resistance", "This is how support works").
4. REJECT Viewer Request / Q&A: Answering viewer questions or discussing stocks asked in comments (e.g., "Viewer asking about 37 level", "Someone asked about Tata Motors").
5. REJECT General commentary, market chatter, or ambient noise without immediate live trade execution or position management.

Output Schema:
Respond ONLY with a valid JSON object matching these exact keys:
{{
  "signal_type": "ENTRY" | "PARTIAL_EXIT" | "MOVE_STOP" | "EXIT" | "NONE",
  "is_trade_active": true | false,
  "direction": "BUY" | "SELL" | "CALL" | "PUT" | "NONE",
  "instrument": "<ticker or strike, e.g. NIFTY 24500 CE, BANKNIFTY, GOLD, XAUUSD, BTCUSDT, UNKNOWN>",
  "entry_price": "<entry price/range or null>",
  "stop_loss": "<stop loss level or 'CTC' / 'Breakeven' or null>",
  "target": "<target price or null>",
  "trigger_quote": "<the exact key spoken quote where entry or management was declared>",
  "confidence": <float 0.0 to 1.0>,
  "reasoning": "<concise explanation of spoken and visual evidence>"
}}
"""


class MultimodalTradeVerification(BaseModel):
    """Structured validated trade verification result combining visual and spoken data."""

    verification_unavailable: bool = Field(default=False, exclude=True)

    signal_type: SignalType = Field(
        default="NONE",
        description="Explicit signal category: ENTRY, PARTIAL_EXIT, MOVE_STOP, EXIT, or NONE",
    )
    is_trade_active: bool = Field(
        default=False,
        description="Whether a valid trade entry or active position is confirmed",
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
        max_length=100,
        description="Detected entry price or execution range",
    )
    stop_loss: str | None = Field(
        default=None,
        max_length=100,
        description="Mentioned or charted stop loss level or CTC/breakeven",
    )
    target: str | None = Field(
        default=None,
        max_length=100,
        description="Mentioned or charted target level",
    )
    trigger_quote: str = Field(
        default="",
        max_length=300,
        description="Key snippet spoken by the trader declaring the trade or management action",
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0"
    )
    reasoning: str = Field(
        default="",
        max_length=600,
        description="Brief explanation of findings across audio and image",
    )

    @field_validator("signal_type", mode="before")
    @classmethod
    def normalize_signal_type(cls, v: Any) -> str:
        if not isinstance(v, str):
            return "NONE"
        clean = v.upper().strip().replace("-", "_").replace(" ", "_")
        if clean in ("ENTRY", "ENTER", "NEW_TRADE", "NEW", "OPEN", "BUY", "SELL", "CALL", "PUT"):
            return "ENTRY"
        if clean in (
            "PARTIAL_EXIT",
            "PARTIAL",
            "PARTIAL_PROFIT",
            "BOOK_PROFIT",
            "BOOKED_PROFIT",
            "BOOKING_PROFIT",
            "PARTIAL_BOOKING",
            "SCALE_OUT",
        ):
            return "PARTIAL_EXIT"
        if clean in (
            "MOVE_STOP",
            "MOVE_SL",
            "SL_TO_CTC",
            "CTC",
            "COST_TO_COST",
            "BREAKEVEN",
            "TRAIL_SL",
            "TRAILING_SL",
            "TRAIL_STOP",
            "TRAILING_STOP",
        ):
            return "MOVE_STOP"
        if clean in ("EXIT", "CLOSE", "CLOSED", "CUT", "SL_HIT", "TARGET_HIT"):
            return "EXIT"
        return "NONE"

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
    def validate_signal_and_active_state(self) -> MultimodalTradeVerification:
        # Backward compatibility is allowed only when the new field was omitted.
        # An explicit NONE always wins so an inconsistent provider response fails closed.
        signal_type_was_supplied = "signal_type" in self.__pydantic_fields_set__
        if (
            self.signal_type == "NONE"
            and self.is_trade_active
            and not signal_type_was_supplied
        ):
            self.signal_type = "ENTRY"

        if self.signal_type in ("ENTRY", "PARTIAL_EXIT", "MOVE_STOP"):
            self.is_trade_active = True
        elif self.signal_type in ("EXIT", "NONE"):
            self.is_trade_active = False

        if self.signal_type == "ENTRY" and self.direction == "NONE":
            self.direction = "BUY"
        if not self.is_trade_active and self.signal_type == "NONE":
            self.direction = "NONE"
        return self


class MultimodalVerifier:
    """Evaluates live trade alerts by querying Gemini with both screenshot and spoken transcript."""

    def __init__(
        self,
        api_key: str,
        config: AIConfig | None = None,
        client: Any = None,
        provider_keys: dict[str, str] | None = None,
    ) -> None:
        self.api_key = api_key
        self.config = config or AIConfig()
        self._client = client
        self._owns_client = client is None
        self._provider_keys = {"gemini": api_key, **(provider_keys or {})}
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._route_lock = asyncio.Lock()
        from src.logger import register_secret_for_redaction
        for key in self._provider_keys.values():
            register_secret_for_redaction(key)

    def _routes(self):
        routes = [LLMRoute(provider="gemini", model=self.config.model_name)]
        seen = set()
        for route in routes + self.config.fallback_models:
            ident = (route.provider, route.model)
            if ident not in seen and self._provider_keys.get(route.provider):
                seen.add(ident)
                yield route

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
        """Check model access on any configured route (does not guarantee quota)."""
        for route in self._routes():
            try:
                if route.provider == "gemini":
                    await asyncio.wait_for(
                        asyncio.to_thread(self._get_client().models.get, model=route.model),
                        timeout=self.config.timeout_seconds,
                    )
                    return True
                if await model_accessible(route.provider, route.model,
                                          self._provider_keys[route.provider], self.config.timeout_seconds):
                    return True
            except Exception as exc:
                logger.warning("Verification preflight provider=%s model=%s error=%s",
                               route.provider, route.model, type(exc).__name__)
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
        jpeg_bytes: bytes | None,
        prompt_text: str,
        model_name: str | None = None,
    ) -> str:
        """Synchronous Gemini call using transcript text and an optional screenshot."""
        from google import genai  # noqa: F401 (unused variable kept for type hint)
        from google.genai import types

        contents: list[Any] = [prompt_text]
        if jpeg_bytes is not None:
            image_part = types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg")
            contents.insert(0, image_part)

        config = types.GenerateContentConfig(
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=MultimodalTradeVerification,
            http_options=types.HttpOptions(
                timeout=int(self.config.timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

        target_model = model_name or self.config.model_name
        response = client.models.generate_content(
            model=target_model,
            contents=contents,
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
            verification.signal_type != "NONE"
            and verification.confidence < self.config.confidence_threshold
        ):
            logger.info(
                "Multimodal signal %s confidence %.2f is below threshold %.2f; suppressing (fail-closed)",
                verification.signal_type,
                verification.confidence,
                self.config.confidence_threshold,
            )
            verification.signal_type = "NONE"
            verification.is_trade_active = False
            verification.direction = "NONE"

        return verification

    async def verify_multimodal_trade(
        self,
        frame: np.ndarray | bytes | None,
        transcript_context: str,
        trigger_phrase: str = "",
        trader_profile: str = "",
    ) -> MultimodalTradeVerification:
        """Analyze spoken context and an optional chart frame with Gemini."""
        jpeg_bytes = self._frame_to_jpeg_bytes(frame) if frame is not None else None
        prompt_text = MULTIMODAL_PROMPT_TEMPLATE.format(
            trader_profile=trader_profile or "General Financial Trader (Hindi/English)",
            trigger_phrase=trigger_phrase,
            transcript_context=transcript_context or "No transcript commentary available.",
        )
        if jpeg_bytes is None:
            prompt_text += (
                "\n\nIMPORTANT: Screenshot capture is disabled. Use only the spoken "
                "transcript. Do not infer chart state, order fills, position markers, "
                "instrument, direction, or prices from visual evidence."
            )

        # Serialize provider selection so simultaneous channels cannot stampede an
        # exhausted model. Cooldowns are shared across channels in this worker.
        deadline = time.monotonic() + self.config.verification_timeout_seconds
        async with self._route_lock:
            for route in self._routes():
                ident = (route.provider, route.model)
                now = time.monotonic()
                if now >= deadline:
                    break
                if max(self._cooldowns.get(ident, 0), self._cooldowns.get((route.provider, "*"), 0)) > now:
                    continue
                try:
                    timeout = min(self.config.timeout_seconds, deadline - now)
                    if route.provider == "gemini":
                        raw_text = await asyncio.wait_for(asyncio.to_thread(
                            self._call_gemini_multimodal_sync, self._get_client(),
                            jpeg_bytes, prompt_text, route.model,
                        ), timeout=timeout)
                    else:
                        text_prompt = prompt_text + (
                            "\n\nThis provider receives NO screenshot. Use only explicit spoken evidence. "
                            "Do not infer chart state, instrument, direction or prices from unseen visuals. "
                            "Trader profile is background only, not evidence of the current instrument. "
                            "If instrument or direction is ambiguous for ENTRY, return NONE."
                        )
                        raw_text = await chat_completion(
                            route.provider, route.model, self._provider_keys[route.provider], text_prompt, timeout,
                        )
                    verification = self._parse_response(raw_text)
                    # Only the transport may mark a result unavailable.
                    verification.verification_unavailable = False
                    logger.info(
                        "Verification result provider=%s model=%s signal=%s confidence=%.2f",
                        route.provider, route.model, verification.signal_type, verification.confidence,
                    )
                    # A valid NONE is a completed decision, not a reason to shop models.
                    return verification
                except Exception as exc:
                    delay, category, provider_wide = failure_policy(exc, self.config)
                    if route.provider == "openrouter" and category in ("daily_quota", "rate_limit"):
                        provider_wide = True
                    scope = (route.provider, "*") if provider_wide else ident
                    self._cooldowns[scope] = time.monotonic() + delay
                    logger.warning(
                        "Verification fallback provider=%s model=%s reason=%s cooldown=%.0fs",
                        route.provider, route.model, category, delay,
                    )

        return MultimodalTradeVerification(
            verification_unavailable=True,
            reasoning="All configured verification routes failed, are cooling down, or exceeded the deadline",
        )
