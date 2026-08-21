"""Deterministic offline unit and integration tests for Groq Speech-to-Text AudioTranscriber."""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import numpy as np
from pydantic import SecretStr

from src.audio_transcriber import (
    GROQ_MODELS_URL,
    GROQ_TRANSCRIPTIONS_URL,
    AudioTranscriber,
    pcm_to_wav_bytes,
)
from src.config import AudioSTTConfig, ConfigurationError


class MockClientResponse:
    """Mock aiohttp.ClientResponse for deterministic offline HTTP testing."""

    def __init__(
        self,
        status: int = 200,
        json_data: dict | list | None = None,
        headers: dict[str, str] | None = None,
        raise_json: bool = False,
    ) -> None:
        self.status = status
        self._json_data = json_data if json_data is not None else {"text": "default text"}
        self.headers = headers or {}
        self._raise_json = raise_json

    async def json(self) -> dict | list:
        if self._raise_json:
            raise ValueError("Invalid JSON response")
        return self._json_data

    async def __aenter__(self) -> MockClientResponse:  # noqa: PYI034 - Python 3.10 compatibility
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        pass


def create_mock_pcm(
    duration_seconds: float = 1.0,
    sample_rate: int = 16000,
    amplitude: float = 10000.0,
    frequency: float = 440.0,
) -> bytes:
    """Generate synthetic signed-16-bit PCM audio with strong signal energy."""
    t = np.linspace(0, duration_seconds, int(sample_rate * duration_seconds), endpoint=False)
    signal = (amplitude * np.sin(2 * np.pi * frequency * t)).astype(np.int16)
    return signal.tobytes()


class TestPCMToWavBytes(unittest.TestCase):
    """Tests for in-memory PCM to WAV container conversion."""

    def test_pcm_to_wav_headers_and_payload(self) -> None:
        pcm_data = create_mock_pcm(duration_seconds=0.5, sample_rate=16000)
        wav_bytes = pcm_to_wav_bytes(pcm_data, sample_rate=16000, channels=1, sample_width=2)

        # Validate WAV structure using standard wave module
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 16000)
            self.assertEqual(wf.getnframes(), len(pcm_data) // 2)
            read_pcm = wf.readframes(wf.getnframes())
            self.assertEqual(read_pcm, pcm_data)


class TestAudioTranscriberConfigAndInit(unittest.TestCase):
    """Tests for AudioTranscriber configuration, initialization, and credential handling."""

    def test_init_raises_configuration_error_when_key_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GROQ_API_KEY", None)
            with self.assertRaises(ConfigurationError) as ctx:
                AudioTranscriber(api_key=None)
            self.assertIn("GROQ_API_KEY", str(ctx.exception))

    def test_init_with_explicit_string_key(self) -> None:
        transcriber = AudioTranscriber(api_key="gsk_explicit_key_123")
        self.assertEqual(transcriber._api_key.get_secret_value(), "gsk_explicit_key_123")

    def test_init_with_secret_str_key(self) -> None:
        secret_key = SecretStr("gsk_secret_str_456")
        transcriber = AudioTranscriber(api_key=secret_key)
        self.assertEqual(transcriber._api_key.get_secret_value(), "gsk_secret_str_456")

    def test_init_with_environment_key(self) -> None:
        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_env_key_789"}):
            transcriber = AudioTranscriber()
            self.assertEqual(transcriber._api_key.get_secret_value(), "gsk_env_key_789")

    def test_default_config_values(self) -> None:
        transcriber = AudioTranscriber(api_key="gsk_test")
        self.assertEqual(transcriber.config.model, "whisper-large-v3-turbo")
        self.assertEqual(transcriber.config.temperature, 0.0)
        self.assertEqual(transcriber.config.language, "hi")
        self.assertEqual(transcriber.config.chunk_duration_seconds, 10.0)
        self.assertEqual(transcriber.config.max_retries, 3)


class TestAudioTranscriberTranscription(unittest.IsolatedAsyncioTestCase):
    """Tests for PCM transcription, silence gating, retries, and error handling."""

    def setUp(self) -> None:
        self.config = AudioSTTConfig(
            model="whisper-large-v3-turbo",
            temperature=0.0,
            language="hi",
            initial_prompt="Nifty, Buy, Sell",
            timeout_seconds=2.0,
            max_retries=3,
            retry_delay_seconds=0.01,  # Fast for tests
        )
        self.transcriber = AudioTranscriber(
            config=self.config,
            api_key="gsk_test_mock_key_xyz",
        )

    async def asyncTearDown(self) -> None:
        await self.transcriber.close()

    async def test_empty_pcm_returns_empty_string_without_network_call(self) -> None:
        with patch.object(self.transcriber, "_get_session") as mock_get_session:
            result = await self.transcriber.transcribe_pcm_chunk(b"")
            self.assertEqual(result, "")
            mock_get_session.assert_not_called()

    async def test_silence_pcm_returns_empty_string_without_network_call(self) -> None:
        silent_pcm = b"\x00\x00" * 80000  # 5 seconds of pure zeros
        with patch.object(self.transcriber, "_get_session") as mock_get_session:
            result = await self.transcriber.transcribe_pcm_chunk(silent_pcm)
            self.assertEqual(result, "")
            mock_get_session.assert_not_called()

    async def test_transcribe_pcm_chunk_success(self) -> None:
        pcm = create_mock_pcm(duration_seconds=1.0)
        expected_transcript = "NIFTY 24500 CE enter kiya"

        mock_resp = MockClientResponse(status=200, json_data={"text": f"  {expected_transcript}  "})
        mock_post = MagicMock(return_value=mock_resp)

        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.close = AsyncMock()
        mock_session.closed = False

        self.transcriber._session = mock_session

        result = await self.transcriber.transcribe_pcm_chunk(pcm)
        self.assertEqual(result, expected_transcript)
        mock_post.assert_called_once()

        # Check call arguments: url, headers, and form data
        called_url, called_kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
        self.assertEqual(called_url, GROQ_TRANSCRIPTIONS_URL)
        self.assertEqual(called_kwargs["headers"]["Authorization"], "Bearer gsk_test_mock_key_xyz")

        # Inspect FormData fields
        form = called_kwargs["data"]
        field_names = [f[0]["name"] for f in form._fields if isinstance(f, tuple) and "name" in f[0]]
        self.assertIn("file", field_names)
        self.assertIn("model", field_names)
        self.assertIn("temperature", field_names)
        self.assertIn("language", field_names)
        self.assertIn("prompt", field_names)
        self.assertIn("response_format", field_names)

    async def test_transcribe_pcm_chunk_malformed_json_returns_empty(self) -> None:
        pcm = create_mock_pcm()
        mock_resp = MockClientResponse(status=200, raise_json=True)
        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.close = AsyncMock()
        mock_session.closed = False
        self.transcriber._session = mock_session

        result = await self.transcriber.transcribe_pcm_chunk(pcm)
        self.assertEqual(result, "")

    async def test_transcribe_pcm_chunk_missing_text_field_returns_empty(self) -> None:
        pcm = create_mock_pcm()
        mock_resp = MockClientResponse(status=200, json_data={"other_key": "val"})
        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.close = AsyncMock()
        mock_session.closed = False
        self.transcriber._session = mock_session

        result = await self.transcriber.transcribe_pcm_chunk(pcm)
        self.assertEqual(result, "")

    async def test_transcribe_pcm_chunk_non_string_text_returns_empty(self) -> None:
        pcm = create_mock_pcm()
        mock_resp = MockClientResponse(status=200, json_data={"text": 12345})
        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.close = AsyncMock()
        mock_session.closed = False
        self.transcriber._session = mock_session

        result = await self.transcriber.transcribe_pcm_chunk(pcm)
        self.assertEqual(result, "")

    async def test_transcribe_pcm_chunk_auth_failure_does_not_retry(self) -> None:
        pcm = create_mock_pcm()
        mock_resp = MockClientResponse(status=401, json_data={"error": "Invalid API key"})
        mock_post = MagicMock(return_value=mock_resp)
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.close = AsyncMock()
        mock_session.closed = False
        self.transcriber._session = mock_session

        result = await self.transcriber.transcribe_pcm_chunk(pcm)
        self.assertEqual(result, "")
        # Should not retry 401
        self.assertEqual(mock_post.call_count, 1)

    async def test_transcribe_pcm_chunk_429_rate_limit_retries_and_succeeds(self) -> None:
        pcm = create_mock_pcm()
        resp_429 = MockClientResponse(
            status=429,
            json_data={"error": "Rate limit reached"},
            headers={"Retry-After": "0.01"},
        )
        resp_200 = MockClientResponse(status=200, json_data={"text": "Retried and succeeded"})

        mock_post = MagicMock(side_effect=[resp_429, resp_200])
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.close = AsyncMock()
        mock_session.closed = False
        self.transcriber._session = mock_session

        result = await self.transcriber.transcribe_pcm_chunk(pcm)
        self.assertEqual(result, "Retried and succeeded")
        self.assertEqual(mock_post.call_count, 2)

    async def test_transcribe_pcm_chunk_5xx_server_error_retries_and_succeeds(self) -> None:
        pcm = create_mock_pcm()
        resp_503 = MockClientResponse(status=503, json_data={"error": "Service unavailable"})
        resp_200 = MockClientResponse(status=200, json_data={"text": "Recovered from 503"})

        mock_post = MagicMock(side_effect=[resp_503, resp_200])
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.close = AsyncMock()
        mock_session.closed = False
        self.transcriber._session = mock_session

        result = await self.transcriber.transcribe_pcm_chunk(pcm)
        self.assertEqual(result, "Recovered from 503")
        self.assertEqual(mock_post.call_count, 2)

    async def test_transcribe_pcm_chunk_timeout_retries_and_exhausts(self) -> None:
        pcm = create_mock_pcm()
        mock_post = MagicMock(side_effect=asyncio.TimeoutError("Request timed out"))
        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.close = AsyncMock()
        mock_session.closed = False
        self.transcriber._session = mock_session

        result = await self.transcriber.transcribe_pcm_chunk(pcm)
        self.assertEqual(result, "")
        self.assertEqual(mock_post.call_count, self.config.max_retries)


class TestAudioTranscriberPreflightAndLifecycle(unittest.IsolatedAsyncioTestCase):
    """Tests for connection preflight check and session lifecycle."""

    def setUp(self) -> None:
        self.transcriber = AudioTranscriber(api_key="gsk_preflight_test")

    async def asyncTearDown(self) -> None:
        await self.transcriber.close()

    async def test_check_connection_success(self) -> None:
        mock_resp = MockClientResponse(status=200, json_data={"data": []})
        mock_get = MagicMock(return_value=mock_resp)
        mock_session = MagicMock()
        mock_session.get = mock_get
        mock_session.close = AsyncMock()
        mock_session.closed = False
        self.transcriber._session = mock_session

        is_connected = await self.transcriber.check_connection()
        self.assertTrue(is_connected)
        mock_get.assert_called_once()
        called_url, called_kwargs = mock_get.call_args[0][0], mock_get.call_args[1]
        self.assertEqual(called_url, GROQ_MODELS_URL)
        self.assertEqual(called_kwargs["headers"]["Authorization"], "Bearer gsk_preflight_test")

    async def test_check_connection_failure_status(self) -> None:
        mock_resp = MockClientResponse(status=401, json_data={"error": "Invalid API Key"})
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.close = AsyncMock()
        mock_session.closed = False
        self.transcriber._session = mock_session

        is_connected = await self.transcriber.check_connection()
        self.assertFalse(is_connected)

    async def test_check_connection_network_exception(self) -> None:
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=aiohttp.ClientConnectorError(None, OSError("Network down")))
        mock_session.close = AsyncMock()
        mock_session.closed = False
        self.transcriber._session = mock_session

        is_connected = await self.transcriber.check_connection()
        self.assertFalse(is_connected)

    async def test_close_and_context_manager(self) -> None:
        async with AudioTranscriber(api_key="gsk_test") as at:
            session = at._get_session()
            self.assertFalse(session.closed)

        self.assertTrue(session.closed)
        self.assertIsNone(at._session)


class TestAudioTranscriberFileTranscription(unittest.IsolatedAsyncioTestCase):
    """Tests for offline full file transcription via Groq STT."""

    def setUp(self) -> None:
        self.transcriber = AudioTranscriber(api_key="gsk_file_test")

    async def asyncTearDown(self) -> None:
        await self.transcriber.close()

    async def test_transcribe_file_missing_raises_file_not_found(self) -> None:
        with self.assertRaises(FileNotFoundError):
            await self.transcriber.transcribe_file("non_existent_audio_file.wav")

    async def test_transcribe_file_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_wav = Path(tmp_dir) / "test.wav"
            pcm_data = create_mock_pcm(duration_seconds=0.5)
            wav_bytes = pcm_to_wav_bytes(pcm_data)
            tmp_wav.write_bytes(wav_bytes)

            mock_resp = MockClientResponse(status=200, json_data={"text": "Full file transcript content"})
            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=mock_resp)
            mock_session.close = AsyncMock()
            mock_session.closed = False
            self.transcriber._session = mock_session

            segments = await self.transcriber.transcribe_file(tmp_wav)
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0]["text"], "Full file transcript content")


class TestAudioTranscriberKeyRotation(unittest.IsolatedAsyncioTestCase):
    """Tests for multi-key round-robin rotation and HTTP 429 rate limit failover."""

    def setUp(self) -> None:
        self.keys = ["gsk_key_alpha_111", "gsk_key_beta_222"]
        self.transcriber = AudioTranscriber(
            api_key=self.keys,
            config=AudioSTTConfig(max_retries=2, retry_delay_seconds=0.01),
        )

    async def asyncTearDown(self) -> None:
        await self.transcriber.close()

    def test_init_with_multiple_keys_stores_all(self) -> None:
        self.assertEqual(len(self.transcriber.api_keys), 2)
        self.assertEqual(
            [k.get_secret_value() for k in self.transcriber.api_keys],
            self.keys,
        )

    async def test_round_robin_rotation_on_success(self) -> None:
        pcm = create_mock_pcm(duration_seconds=0.5)
        mock_resp1 = MockClientResponse(status=200, json_data={"text": "chunk one"})
        mock_resp2 = MockClientResponse(status=200, json_data={"text": "chunk two"})

        auth_headers_used: list[str] = []

        def mock_post(url, data=None, headers=None):
            auth_headers_used.append(headers.get("Authorization", ""))
            return mock_resp1 if len(auth_headers_used) == 1 else mock_resp2

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=mock_post)
        mock_session.close = AsyncMock()
        mock_session.closed = False
        self.transcriber._session = mock_session

        res1 = await self.transcriber.transcribe_pcm_chunk(pcm)
        res2 = await self.transcriber.transcribe_pcm_chunk(pcm)

        self.assertEqual(res1, "chunk one")
        self.assertEqual(res2, "chunk two")
        self.assertEqual(len(auth_headers_used), 2)
        self.assertEqual(auth_headers_used[0], f"Bearer {self.keys[0]}")
        self.assertEqual(auth_headers_used[1], f"Bearer {self.keys[1]}")

    async def test_immediate_failover_on_429(self) -> None:
        pcm = create_mock_pcm(duration_seconds=0.5)
        mock_resp_429 = MockClientResponse(status=429, headers={"Retry-After": "0.01"})
        mock_resp_200 = MockClientResponse(status=200, json_data={"text": "recovered chunk"})

        auth_headers_used: list[str] = []

        def mock_post(url, data=None, headers=None):
            auth_headers_used.append(headers.get("Authorization", ""))
            return mock_resp_429 if len(auth_headers_used) == 1 else mock_resp_200

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=mock_post)
        mock_session.close = AsyncMock()
        mock_session.closed = False
        self.transcriber._session = mock_session

        res = await self.transcriber.transcribe_pcm_chunk(pcm)

        self.assertEqual(res, "recovered chunk")
        self.assertEqual(len(auth_headers_used), 2)
        # First attempt used key 1, failed with 429, second attempt used key 2 and succeeded!
        self.assertEqual(auth_headers_used[0], f"Bearer {self.keys[0]}")
        self.assertEqual(auth_headers_used[1], f"Bearer {self.keys[1]}")


class TestAudioTranscriberOfflineNetworkGuard(unittest.IsolatedAsyncioTestCase):
    """Guard tests proving all code paths strictly use injected sessions with no live I/O."""

    async def test_guard_paths_never_create_real_session_or_call_network(self) -> None:
        """Prove that success, retry, 429 rate-limit, preflight, and file transcription

        never call aiohttp.ClientSession constructor when an injected session is present.
        """
        keys = ["gsk_guard_key_111", "gsk_guard_key_222"]
        config = AudioSTTConfig(max_retries=2, retry_delay_seconds=0.01)

        with patch("aiohttp.ClientSession", side_effect=AssertionError("Live session created!")):
            mock_session = MagicMock()
            mock_session.post = MagicMock(return_value=MockClientResponse(status=200, json_data={"text": "guard ok"}))
            mock_session.get = MagicMock(return_value=MockClientResponse(status=200, json_data={"data": []}))
            mock_session.close = AsyncMock()
            mock_session.closed = False
            transcriber = AudioTranscriber(
                api_key=keys,
                config=config,
                session=mock_session,
            )

            # 1. Test success path
            pcm = create_mock_pcm(duration_seconds=0.5)
            res = await transcriber.transcribe_pcm_chunk(pcm)
            self.assertEqual(res, "guard ok")
            mock_session.post.assert_called_once()

            # 2. Test 5xx retry path
            mock_session.post.reset_mock()
            resp_500 = MockClientResponse(status=500, json_data={"error": "Internal error"})
            resp_200 = MockClientResponse(status=200, json_data={"text": "5xx recovered"})
            mock_session.post.side_effect = [resp_500, resp_200]

            res_5xx = await transcriber.transcribe_pcm_chunk(pcm)
            self.assertEqual(res_5xx, "5xx recovered")
            self.assertEqual(mock_session.post.call_count, 2)

            # 3. Test 429 failover path
            mock_session.post.reset_mock()
            resp_429 = MockClientResponse(status=429, headers={"Retry-After": "0.01"})
            resp_200_key2 = MockClientResponse(status=200, json_data={"text": "429 recovered"})
            mock_session.post.side_effect = [resp_429, resp_200_key2]

            res_429 = await transcriber.transcribe_pcm_chunk(pcm)
            self.assertEqual(res_429, "429 recovered")
            self.assertEqual(mock_session.post.call_count, 2)

            # 4. Test preflight check_connection
            is_connected = await transcriber.check_connection()
            self.assertTrue(is_connected)
            self.assertEqual(mock_session.get.call_count, len(keys))

            # 5. Test file transcription
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_file = Path(tmp_dir) / "sample.wav"
                tmp_file.write_bytes(pcm_to_wav_bytes(pcm))
                mock_session.post.reset_mock()
                mock_session.post.side_effect = None
                mock_session.post.return_value = MockClientResponse(status=200, json_data={"text": "file ok"})

                file_res = await transcriber.transcribe_file(tmp_file)
                self.assertEqual(len(file_res), 1)
                self.assertEqual(file_res[0]["text"], "file ok")
                mock_session.post.assert_called_once()

            await transcriber.close()

    async def test_open_session_cross_loop_use_fails_without_leaking(self) -> None:
        """An open session must be closed before the transcriber moves to another loop."""
        old_session = MagicMock()
        old_session.closed = False
        old_session.close = AsyncMock()
        transcriber = AudioTranscriber(api_key="gsk_test", session=old_session)
        transcriber._session_loop = MagicMock()

        with patch(
            "aiohttp.ClientSession",
            side_effect=AssertionError("replacement session leaked the old session"),
        ), self.assertRaisesRegex(RuntimeError, "across event loops"):
            transcriber._get_session()

        old_session.close.assert_not_awaited()
        await transcriber.close()
        old_session.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
