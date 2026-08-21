"""Speech-to-Text transcriber using Groq OpenAI-compatible audio transcription API."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import wave
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np
from pydantic import SecretStr

from src.config import AudioSTTConfig, ConfigurationError

logger = logging.getLogger("trade_bot.audio_transcriber")

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_TRANSCRIPTIONS_URL = f"{GROQ_BASE_URL}/audio/transcriptions"
GROQ_MODELS_URL = f"{GROQ_BASE_URL}/models"


def pcm_to_wav_bytes(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Convert raw signed-16-bit PCM bytes into in-memory WAV container bytes."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buffer.getvalue()


class AudioTranscriber:
    """Wraps Groq Audio Transcription API to transcribe in-memory PCM chunks and audio files."""

    def __init__(
        self,
        config: AudioSTTConfig | None = None,
        api_key: str | SecretStr | list[str] | list[SecretStr] | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self.config = config or AudioSTTConfig()
        keys: list[SecretStr] = []
        if api_key is not None:
            if isinstance(api_key, (list, tuple)):
                for k in api_key:
                    val = (
                        k.get_secret_value()
                        if isinstance(k, SecretStr)
                        else str(k).strip()
                    )
                    if val:
                        keys.append(SecretStr(val))
            elif isinstance(api_key, SecretStr):
                if api_key.get_secret_value().strip():
                    keys.append(api_key)
            elif isinstance(api_key, str) and api_key.strip():
                keys.append(SecretStr(api_key.strip()))

        if not keys:
            env_keys = os.getenv("GROQ_API_KEYS", "").strip()
            if env_keys:
                for k in env_keys.split(","):
                    if k.strip():
                        keys.append(SecretStr(k.strip()))
            single_env = os.getenv("GROQ_API_KEY", "").strip()
            if single_env and not any(k.get_secret_value() == single_env for k in keys):
                keys.insert(0, SecretStr(single_env))

        if not keys:
            raise ConfigurationError(
                "Missing required Groq API key for AudioTranscriber. "
                "Define GROQ_API_KEY or GROQ_API_KEYS in your environment or pass api_key."
            )

        self._api_keys: list[SecretStr] = keys
        self._current_key_idx: int = 0
        self._session: aiohttp.ClientSession | None = session
        self._session_loop: asyncio.AbstractEventLoop | None = None

    @property
    def api_keys(self) -> list[SecretStr]:
        """Return all configured Groq API keys."""
        return list(self._api_keys)

    @property
    def _api_key(self) -> SecretStr:
        """Return the current active Groq API key (for backward compatibility)."""
        return self._get_current_key()

    def _get_current_key(self) -> SecretStr:
        """Return the currently selected API key."""
        return self._api_keys[self._current_key_idx % len(self._api_keys)]

    def _rotate_key(self, reason: str = "") -> SecretStr:
        """Advance to the next API key in the pool for rotation and rate-limit fallback."""
        prev_idx = self._current_key_idx
        self._current_key_idx = (self._current_key_idx + 1) % len(self._api_keys)
        if len(self._api_keys) > 1 and reason:
            logger.info(
                "Rotating Groq API key (Key #%s -> Key #%s): %s",
                prev_idx + 1,
                self._current_key_idx + 1,
                reason,
            )
        return self._get_current_key()

    def _get_session(self) -> aiohttp.ClientSession:
        """Lazily initialize and return the reusable aiohttp.ClientSession."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError(
                "AudioTranscriber HTTP operations require a running event loop."
            ) from exc

        if self._session is not None and not bool(
            getattr(self._session, "closed", False)
        ):
            if self._session_loop is None:
                # An explicitly injected session is adopted by the first loop that uses it.
                self._session_loop = current_loop
            elif self._session_loop is not current_loop:
                raise RuntimeError(
                    "AudioTranscriber cannot reuse an open HTTP session across event loops; "
                    "close it before using the transcriber from another loop."
                )
            return self._session

        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._session_loop = current_loop
        return self._session

    async def check_connection(self) -> bool:
        """Explicit connection preflight verifying Groq API credentials and endpoint accessibility."""
        any_success = False
        session = self._get_session()
        for idx, key in enumerate(self._api_keys, start=1):
            headers = {
                "Authorization": f"Bearer {key.get_secret_value()}",
            }
            try:
                async with session.get(GROQ_MODELS_URL, headers=headers) as resp:
                    if 200 <= resp.status < 300:
                        logger.info(f"Groq STT API key #{idx} connection preflight passed.")
                        any_success = True
                    else:
                        logger.warning(
                            f"Groq STT API key #{idx} preflight returned HTTP status {resp.status}."
                        )
            except Exception as exc:  # noqa: BLE001 - preflight error boundary
                logger.warning(
                    f"Groq STT API key #{idx} preflight failed with exception: {type(exc).__name__}"
                )

        return any_success

    async def close(self) -> None:
        """Close the underlying aiohttp.ClientSession."""
        if (
            self._session is not None
            and not getattr(self._session, "closed", False)
            and hasattr(self._session, "close")
        ):
            res = self._session.close()
            if asyncio.iscoroutine(res):
                await res
        self._session = None
        self._session_loop = None

    async def __aenter__(self) -> AudioTranscriber:  # noqa: PYI034 - Python 3.10 compatibility
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        await self.close()

    async def transcribe_pcm_chunk(
        self,
        pcm_bytes: bytes,
        sample_rate: int = 16000,
    ) -> str:
        """Transcribe raw 16-bit mono PCM audio chunk via Groq Speech-to-Text asynchronously.

        Args:
            pcm_bytes: Raw 16-bit signed PCM byte string (16 kHz mono).
            sample_rate: Audio sampling rate in Hz (default: 16000).

        Returns:
            str: Stripped transcript string, or empty string on silence/error.
        """
        if not pcm_bytes:
            return ""

        try:
            # Check RMS energy to avoid sending silent/ambient chunks to API
            audio_array = (
                np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            )
            if len(audio_array) == 0:
                return ""
            rms = float(np.sqrt(np.mean(audio_array**2)))
            if rms < 0.005:  # Very low energy / ambient silence
                return ""
        except Exception as exc:  # noqa: BLE001 - audio buffer processing
            logger.warning(f"Error checking PCM audio energy: {type(exc).__name__}")
            return ""

        wav_bytes = pcm_to_wav_bytes(pcm_bytes, sample_rate=sample_rate)
        return await self._transcribe_audio_bytes(
            audio_bytes=wav_bytes,
            filename="audio.wav",
            content_type="audio/wav",
        )

    async def transcribe_file(
        self,
        audio_file_path: str | Path,
    ) -> list[dict[str, Any]]:
        """Transcribe a full audio file asynchronously via Groq Speech-to-Text.

        Args:
            audio_file_path: Local path to the audio file (wav, mp3, m4a, etc.).

        Returns:
            list[dict]: List containing segment dicts or full transcript payload.
        """
        file_path = Path(audio_file_path)
        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError(f"Audio file not found: {file_path}")

        audio_bytes = file_path.read_bytes()
        filename = file_path.name
        content_type = (
            "audio/wav" if file_path.suffix.lower() == ".wav" else "audio/mpeg"
        )

        text = await self._transcribe_audio_bytes(
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
        )
        if not text:
            return []
        return [{"start": 0.0, "end": 0.0, "text": text}]

    async def _transcribe_audio_bytes(
        self,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> str:
        """Internal helper handling multipart upload, retries, rate limits, and response parsing."""
        for attempt in range(1, self.config.max_retries + 1):
            current_key = self._get_current_key()
            headers = {
                "Authorization": f"Bearer {current_key.get_secret_value()}",
            }

            form = aiohttp.FormData()
            form.add_field(
                "file",
                audio_bytes,
                filename=filename,
                content_type=content_type,
            )
            form.add_field("model", self.config.model)
            form.add_field("temperature", str(self.config.temperature))
            if self.config.language:
                form.add_field("language", self.config.language)
            if self.config.initial_prompt:
                form.add_field("prompt", self.config.initial_prompt)
            form.add_field("response_format", "json")

            try:
                session = self._get_session()
                async with session.post(
                    GROQ_TRANSCRIPTIONS_URL,
                    data=form,
                    headers=headers,
                ) as resp:
                    if 200 <= resp.status < 300:
                        try:
                            data = await resp.json()
                        except Exception:  # noqa: BLE001
                            logger.warning(
                                "Groq STT returned non-JSON response payload."
                            )
                            return ""

                        if not isinstance(data, dict) or "text" not in data:
                            logger.warning(
                                "Groq STT returned malformed response schema (missing 'text')."
                            )
                            return ""

                        raw_text = data.get("text")
                        if not isinstance(raw_text, str):
                            logger.warning("Groq STT 'text' field is not a string.")
                            return ""

                        # Rotate key for next chunk to balance load across keys
                        if len(self._api_keys) > 1:
                            self._rotate_key()

                        return raw_text.strip()

                    if resp.status == 429:
                        # Failover immediately to next key if multiple keys configured
                        if len(self._api_keys) > 1:
                            self._rotate_key("HTTP 429 Rate Limit encountered")
                            if attempt < self.config.max_retries:
                                await asyncio.sleep(0.1)
                                continue

                        retry_after_hdr = resp.headers.get("Retry-After")
                        delay = self.config.retry_delay_seconds * (2 ** (attempt - 1))
                        if retry_after_hdr:
                            try:
                                delay = max(float(retry_after_hdr), 0.1)
                            except ValueError:
                                pass
                        logger.warning(
                            f"Groq STT rate limit (429) on attempt {attempt}/{self.config.max_retries}. "
                            f"Retrying in {delay:.2f}s..."
                        )
                        if attempt < self.config.max_retries:
                            await asyncio.sleep(delay)
                            continue
                        logger.error("Groq STT rate limit retries exhausted.")
                        return ""

                    if resp.status >= 500:
                        delay = self.config.retry_delay_seconds * (2 ** (attempt - 1))
                        logger.warning(
                            f"Groq STT server error (HTTP {resp.status}) on attempt {attempt}/{self.config.max_retries}. "
                            f"Retrying in {delay:.2f}s..."
                        )
                        if attempt < self.config.max_retries:
                            await asyncio.sleep(delay)
                            continue
                        logger.error(
                            f"Groq STT server error (HTTP {resp.status}) retries exhausted."
                        )
                        return ""

                    # Other 4xx client/auth errors (400, 401, 403, 404, 422) - do NOT retry
                    logger.warning(
                        f"Groq STT request failed with client error HTTP {resp.status}. Not retrying."
                    )
                    return ""

            except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                delay = self.config.retry_delay_seconds * (2 ** (attempt - 1))
                logger.warning(
                    f"Groq STT transient request error ({type(exc).__name__}) on attempt {attempt}/{self.config.max_retries}. "
                    f"Retrying in {delay:.2f}s..."
                )
                if attempt < self.config.max_retries:
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    f"Groq STT request error ({type(exc).__name__}) retries exhausted."
                )
                return ""
            except Exception as exc:  # noqa: BLE001 - unexpected boundary
                logger.error(
                    f"Unexpected error during Groq STT transcription: {type(exc).__name__}"
                )
                return ""

        return ""
