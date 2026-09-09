"""Fixed provider endpoints and secret-safe failure/cooldown handling."""

import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import aiohttp

ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "sambanova": "https://api.sambanova.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


class ProviderFailure(Exception):
    def __init__(self, status, body="", headers=None):
        super().__init__(f"Provider HTTP {status}")
        self.status_code = status
        self.body = body
        self.headers = headers or {}


def duration(value):
    """Parse Retry-After seconds/date and provider duration strings."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        try:
            result = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)", str(value))
            result = sum(float(n) * {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}[u] for n, u in parts)
    return max(0, result) if math.isfinite(result) else 0


def failure_policy(exc, config):
    """Return cooldown, category and whether this affects the whole provider."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    headers = {str(k).lower(): v for k, v in getattr(exc, "headers", {}).items()}
    # Only inspect error bodies internally; never log them or credentials.
    body = str(getattr(exc, "body", "") or exc).lower()
    if status in (401, 402, 403):
        return config.daily_quota_cooldown_seconds, "access", True
    if status == 404:
        return config.daily_quota_cooldown_seconds, "model_unavailable", False
    if status == 429 or "resource_exhausted" in body:
        daily = any(s in body for s in ("perday", "per day", "daily", "requests per day", "tokens per day"))
        daily = daily or str(headers.get("x-ratelimit-remaining-requests-day", "")) == "0"
        delay = config.daily_quota_cooldown_seconds if daily else config.rate_limit_cooldown_seconds
        retry = duration(headers.get("retry-after"))
        for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
            retry = max(retry, duration(headers.get(key)))
        match = re.search(r"retry(?: in|delay['\"]?\s*:\s*['\"]?)\s*(\d+(?:\.\d+)?s)", body)
        if match:
            retry = max(retry, duration(match.group(1)))
        return max(delay, retry), "daily_quota" if daily else "rate_limit", False
    return config.failure_cooldown_seconds, "invalid_response" if isinstance(exc, ValueError) else "transient", False


async def chat_completion(provider, model, key, prompt, timeout):
    """Text-only fallbacks use native async HTTP with no hidden SDK retries."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.post(
            ENDPOINTS[provider] + "/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.1, "max_tokens": 2048,
                  "response_format": {"type": "json_object"}},
        ) as response:
            body = await response.text()
            if response.status >= 400:
                raise ProviderFailure(response.status, body, response.headers)
            payload = await response.json()
            if payload.get("error"):
                error = payload["error"]
                raise ProviderFailure(error.get("code", 500), str(error), response.headers)
            try:
                content = payload["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError("Missing completion") from exc
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty completion")
            return content


async def model_accessible(provider, model, key, timeout):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.get(ENDPOINTS[provider] + "/models", headers={"Authorization": f"Bearer {key}"}) as response:
            if response.status != 200:
                return False
            payload = await response.json()
            return any(item.get("id") == model for item in payload.get("data", []))
