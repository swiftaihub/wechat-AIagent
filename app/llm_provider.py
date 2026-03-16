from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any

import httpx

from app.logging_utils import hash_identifier
from app.metrics import get_runtime_metrics
from app.runtime_config import get_runtime_config

logger = logging.getLogger(__name__)


class LLMProviderError(RuntimeError):
    code = "LLM_UPSTREAM_ERROR"


class LLMProviderConfigError(LLMProviderError):
    code = "LLM_CONFIG_ERROR"


class LLMAuthenticationError(LLMProviderError):
    code = "LLM_AUTH_ERROR"


class LLMRateLimitError(LLMProviderError):
    code = "LLM_RATE_LIMITED"


class LLMServiceUnavailableError(LLMProviderError):
    code = "LLM_UNAVAILABLE"


class LLMTransientError(LLMProviderError):
    code = "LLM_TRANSIENT_ERROR"


def estimate_text_tokens(text: str) -> int:
    normalized = str(text or "").strip()
    if not normalized:
        return 0
    # A lightweight heuristic for monitoring only.
    return max(1, len(normalized) // 4)

@dataclass(frozen=True)
class _CircuitFailure:
    timestamp: float


class _CircuitBreaker:
    def __init__(self) -> None:
        self._lock = Lock()
        self._failures: deque[_CircuitFailure] = deque()
        self._open_until_ts = 0.0

    def before_request(self) -> None:
        config = get_runtime_config().llm
        if config.global_disable:
            raise LLMServiceUnavailableError("LLM provider disabled by configuration.")
        if not config.circuit_breaker_enabled:
            return

        now = time.time()
        with self._lock:
            self._prune_locked(now, config.circuit_breaker_window_seconds)
            if self._open_until_ts > now:
                retry_after = int(self._open_until_ts - now)
                raise LLMServiceUnavailableError(
                    f"LLM circuit breaker open for another {retry_after} seconds."
                )
            if self._open_until_ts and self._open_until_ts <= now:
                self._open_until_ts = 0.0

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
            self._open_until_ts = 0.0

    def record_failure(self) -> None:
        config = get_runtime_config().llm
        if not config.circuit_breaker_enabled:
            return

        now = time.time()
        with self._lock:
            self._prune_locked(now, config.circuit_breaker_window_seconds)
            self._failures.append(_CircuitFailure(timestamp=now))
            if len(self._failures) >= config.circuit_breaker_failure_threshold:
                self._open_until_ts = now + float(config.circuit_breaker_cooldown_seconds)

    def _prune_locked(self, now_ts: float, window_seconds: int) -> None:
        threshold = now_ts - float(window_seconds)
        while self._failures and self._failures[0].timestamp < threshold:
            self._failures.popleft()


_CIRCUIT_BREAKER = _CircuitBreaker()


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _response_format_payload(response_format: str | None) -> dict[str, Any] | None:
    value = str(response_format or "").strip().lower()
    if not value:
        return None
    if value in {"json", "json_object", "json-object"}:
        return {"type": "json_object"}
    return None


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 409, 429, 500, 502, 503, 504}


def _build_payload(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response_format: str | None,
    max_output_tokens: int | None,
) -> dict[str, Any]:
    config = get_runtime_config().llm
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()},
        ],
        "temperature": config.temperature,
        "top_p": config.top_p,
        "max_tokens": max_output_tokens or config.max_output_tokens,
        "stream": False,
    }
    format_payload = _response_format_payload(response_format)
    if format_payload:
        payload["response_format"] = format_payload
    return payload


def _parse_response_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list):
        return ""
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = str(message.get("content", "")).strip()
        if content:
            return content
    return ""


async def llm_chat(
    *,
    system_prompt: str,
    user_prompt: str,
    response_format: str | None = None,
    user_id: str | None = None,
    max_output_tokens: int | None = None,
) -> str:
    config = get_runtime_config().llm
    if not config.api_key:
        raise LLMProviderConfigError("DASHSCOPE_API_KEY is not configured.")

    _CIRCUIT_BREAKER.before_request()
    metrics = get_runtime_metrics()
    payload = _build_payload(
        model=config.model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_format=response_format,
        max_output_tokens=max_output_tokens,
    )
    url = f"{config.base_url}/chat/completions"
    prompt_token_estimate = estimate_text_tokens(system_prompt) + estimate_text_tokens(user_prompt)
    started = time.perf_counter()
    last_error: Exception | None = None

    for attempt in range(config.max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=config.timeout_ms / 1000.0) as client:
                response = await client.post(url, json=payload, headers=_headers(config.api_key))

            if response.status_code in {401, 403}:
                raise LLMAuthenticationError(f"DashScope authentication failed with status {response.status_code}.")
            if response.status_code == 429:
                raise LLMRateLimitError("DashScope rate limit reached.")
            if _is_retryable_status(response.status_code):
                raise LLMTransientError(f"DashScope returned retryable status {response.status_code}.")
            response.raise_for_status()

            data = response.json()
            text = _parse_response_text(data)
            if not text:
                raise LLMProviderError("DashScope response did not contain message content.")

            elapsed_ms = (time.perf_counter() - started) * 1000
            metrics.increment("llm_request_success")
            metrics.observe_latency("llm_request_latency_ms", elapsed_ms)
            logger.info(
                "LLM request succeeded model=%s latency_ms=%.1f input_tokens_est=%d output_tokens_est=%d user_hash=%s",
                config.model,
                elapsed_ms,
                prompt_token_estimate,
                estimate_text_tokens(text),
                hash_identifier(user_id),
            )
            _CIRCUIT_BREAKER.record_success()
            return text
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if attempt >= config.max_retries:
                break
            await asyncio.sleep(0.35 * (attempt + 1))
        except LLMTransientError as exc:
            last_error = exc
            if attempt >= config.max_retries:
                break
            await asyncio.sleep(0.35 * (attempt + 1))
        except httpx.InvalidURL as exc:
            raise LLMProviderConfigError(f"Invalid DASHSCOPE_BASE_URL: {config.base_url}") from exc
        except LLMProviderError:
            raise
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else 0
            if status_code in {401, 403}:
                raise LLMAuthenticationError(f"DashScope authentication failed with status {status_code}.") from exc
            if status_code == 429:
                raise LLMRateLimitError("DashScope rate limit reached.") from exc
            if _is_retryable_status(status_code) and attempt < config.max_retries:
                last_error = exc
                await asyncio.sleep(0.35 * (attempt + 1))
                continue
            raise LLMProviderError(f"DashScope request failed with status {status_code}.") from exc

    _CIRCUIT_BREAKER.record_failure()
    metrics.increment("llm_request_failure")
    elapsed_ms = (time.perf_counter() - started) * 1000
    metrics.observe_latency("llm_request_failure_latency_ms", elapsed_ms)
    raise LLMTransientError("DashScope request failed after retries.") from last_error


async def warmup_llm_provider() -> None:
    config = get_runtime_config().llm
    if not config.warmup_on_startup:
        return
    if not config.api_key:
        logger.warning("LLM warmup skipped because DASHSCOPE_API_KEY is not configured.")
        return

    payload = _build_payload(
        model=config.model,
        system_prompt="You are a helpful assistant.",
        user_prompt="Reply with ok.",
        response_format=None,
        max_output_tokens=min(8, config.max_output_tokens),
    )
    url = f"{config.base_url}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=config.warmup_timeout_ms / 1000.0) as client:
            response = await client.post(url, json=payload, headers=_headers(config.api_key))
            response.raise_for_status()
        logger.info("DashScope warmup succeeded for model: %s", config.model)
    except Exception as exc:
        logger.warning("DashScope warmup failed for model %s: %s", config.model, exc)


def reset_llm_provider_state() -> None:
    _CIRCUIT_BREAKER.record_success()
