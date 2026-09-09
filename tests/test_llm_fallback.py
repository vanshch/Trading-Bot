import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import AIConfig, LLMRoute
from src.llm_fallback import ProviderFailure, duration, failure_policy
from src.multimodal_verifier import MultimodalVerifier


def verifier(routes=None):
    return MultimodalVerifier("gemini-secret", AIConfig(fallback_models=routes or [
        LLMRoute(provider="groq", model="large"), LLMRoute(provider="groq", model="small")
    ]), client=MagicMock(), provider_keys={"groq": "groq-secret"})


@pytest.mark.asyncio
async def test_rate_limit_advances_and_cooldown_recovers():
    v = verifier()
    clock = [100.0]
    with patch("src.multimodal_verifier.time.monotonic", side_effect=lambda: clock[0]), patch.object(
        v, "_call_gemini_multimodal_sync", side_effect=ProviderFailure(429, headers={"Retry-After": "120"})
    ) as primary, patch("src.multimodal_verifier.chat_completion", new_callable=AsyncMock,
                       return_value='{"signal_type":"NONE"}') as fallback:
        await v.verify_multimodal_trade(None, "No entry")
        clock[0] = 219
        await v.verify_multimodal_trade(None, "No entry")
        assert primary.call_count == 1
        clock[0] = 221
        await v.verify_multimodal_trade(None, "No entry")
        assert primary.call_count == 2
        assert fallback.await_count == 3
        assert all(call.args[1] == "large" for call in fallback.await_args_list)


@pytest.mark.asyncio
async def test_large_then_small_and_text_only_prompt():
    v = verifier()
    with patch.object(v, "_call_gemini_multimodal_sync", side_effect=ProviderFailure(429)), patch(
        "src.multimodal_verifier.chat_completion", new_callable=AsyncMock,
        side_effect=[ProviderFailure(429), '{"signal_type":"NONE"}']
    ) as fallback:
        result = await v.verify_multimodal_trade(b"jpeg", "No entry")
        assert [c.args[1] for c in fallback.await_args_list] == ["large", "small"]
        assert "NO screenshot" in fallback.await_args.args[3]
        assert not result.verification_unavailable


@pytest.mark.asyncio
async def test_all_unavailable_and_missing_keys_fail_closed():
    v = verifier([LLMRoute(provider="cerebras", model="missing-key")])
    with patch.object(v, "_call_gemini_multimodal_sync", side_effect=ProviderFailure(429)), patch(
        "src.multimodal_verifier.chat_completion", new_callable=AsyncMock
    ) as fallback:
        result = await v.verify_multimodal_trade(None, "Entry")
        assert result.verification_unavailable
        assert result.signal_type == "NONE"
        fallback.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_json_fails_over_but_valid_none_stops():
    v = verifier()
    with patch.object(v, "_call_gemini_multimodal_sync", return_value="not json"), patch(
        "src.multimodal_verifier.chat_completion", new_callable=AsyncMock,
        return_value='{"signal_type":"NONE", "verification_unavailable":true}'
    ) as fallback:
        result = await v.verify_multimodal_trade(None, "Entry")
        assert fallback.await_count == 1
        assert not result.verification_unavailable


@pytest.mark.asyncio
async def test_concurrent_triggers_share_rate_limit_cooldown():
    v = verifier()
    with patch.object(v, "_call_gemini_multimodal_sync", side_effect=ProviderFailure(429)) as primary, patch(
        "src.multimodal_verifier.chat_completion", new_callable=AsyncMock,
        return_value='{"signal_type":"NONE"}'
    ):
        await asyncio.gather(*(v.verify_multimodal_trade(None, "No entry") for _ in range(3)))
        assert primary.call_count == 1


def test_daily_quota_overrides_short_retry_and_access_scope():
    cfg = AIConfig()
    assert failure_policy(ProviderFailure(429, "GenerateRequestsPerDayPerProjectPerModel", {"Retry-After": "3"}), cfg)[:2] == (86400, "daily_quota")
    assert failure_policy(ProviderFailure(401), cfg)[2] is True
    assert duration("1m30s") == 90
    assert duration("nan") == 0
    assert duration("invalid") == 0


def test_paid_openrouter_rejected():
    with pytest.raises(ValueError):
        LLMRoute(provider="openrouter", model="paid/model")
