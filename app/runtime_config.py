from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


@dataclass(frozen=True)
class LLMProviderConfig:
    api_key: str
    base_url: str
    model: str
    timeout_ms: int
    max_output_tokens: int
    temperature: float
    top_p: float
    max_retries: int
    warmup_on_startup: bool
    warmup_timeout_ms: int
    global_disable: bool
    circuit_breaker_enabled: bool
    circuit_breaker_window_seconds: int
    circuit_breaker_failure_threshold: int
    circuit_breaker_cooldown_seconds: int


@dataclass(frozen=True)
class UsageProtectionConfig:
    redis_url: str
    rate_limit_window_seconds: int
    rate_limit_max_requests: int
    rapid_abuse_block_minutes: int
    max_messages_per_user_session: int
    user_session_cooldown_minutes: int
    max_requests_per_hour: int
    max_requests_per_day: int
    max_input_chars: int
    max_context_messages: int
    max_output_tokens: int
    session_ttl_seconds: int
    inflight_ttl_seconds: int
    repeated_prompt_window_seconds: int
    repeated_prompt_max_duplicates: int


@dataclass(frozen=True)
class RuntimeConfig:
    llm: LLMProviderConfig
    protection: UsageProtectionConfig


def _normalize_base_url(raw: str, default: str) -> str:
    value = (raw or default).strip()
    if not value:
        return default
    return value.rstrip("/")


@lru_cache(maxsize=1)
def get_runtime_config() -> RuntimeConfig:
    timeout_ms = _env_int("LLM_REQUEST_TIMEOUT_MS", 30000, minimum=1000)
    max_output_tokens = _env_int("MAX_OUTPUT_TOKENS", 800, minimum=1)
    session_ttl_seconds = _env_int(
        "PRODUCT_HELPER_SESSION_TTL_SECONDS",
        1800,
        minimum=300,
    )

    llm = LLMProviderConfig(
        api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
        base_url=_normalize_base_url(
            os.getenv("DASHSCOPE_BASE_URL", ""),
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        model=os.getenv("DASHSCOPE_MODEL", "qwen-flash").strip() or "qwen-flash",
        timeout_ms=timeout_ms,
        max_output_tokens=max_output_tokens,
        temperature=_env_float("LLM_TEMPERATURE", 0.2, minimum=0.0),
        top_p=_env_float("LLM_TOP_P", 0.9, minimum=0.0),
        max_retries=_env_int("LLM_MAX_RETRIES", 1, minimum=0),
        warmup_on_startup=_env_bool("LLM_WARMUP_ON_STARTUP", False),
        warmup_timeout_ms=_env_int("LLM_WARMUP_TIMEOUT_MS", 8000, minimum=1000),
        global_disable=_env_bool("LLM_GLOBAL_DISABLE", False),
        circuit_breaker_enabled=_env_bool("LLM_CIRCUIT_BREAKER_ENABLED", True),
        circuit_breaker_window_seconds=_env_int("LLM_CIRCUIT_BREAKER_WINDOW_SECONDS", 60, minimum=10),
        circuit_breaker_failure_threshold=_env_int("LLM_CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5, minimum=1),
        circuit_breaker_cooldown_seconds=_env_int("LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS", 120, minimum=10),
    )

    protection = UsageProtectionConfig(
        redis_url=os.getenv("REDIS_URL", "").strip(),
        rate_limit_window_seconds=_env_int("RATE_LIMIT_WINDOW_SECONDS", 60, minimum=1),
        rate_limit_max_requests=_env_int("RATE_LIMIT_MAX_REQUESTS", 8, minimum=1),
        rapid_abuse_block_minutes=_env_int("RAPID_ABUSE_BLOCK_MINUTES", 30, minimum=1),
        max_messages_per_user_session=_env_int("MAX_MESSAGES_PER_USER_SESSION", 20, minimum=1),
        user_session_cooldown_minutes=_env_int("USER_SESSION_COOLDOWN_MINUTES", 120, minimum=1),
        max_requests_per_hour=_env_int("MAX_REQUESTS_PER_HOUR", 40, minimum=1),
        max_requests_per_day=_env_int("MAX_REQUESTS_PER_DAY", 200, minimum=1),
        max_input_chars=_env_int("MAX_INPUT_CHARS", 4000, minimum=1),
        max_context_messages=_env_int("MAX_CONTEXT_MESSAGES", 12, minimum=1),
        max_output_tokens=max_output_tokens,
        session_ttl_seconds=session_ttl_seconds,
        inflight_ttl_seconds=max(30, (timeout_ms // 1000) * 2),
        repeated_prompt_window_seconds=_env_int("REPEAT_PROMPT_WINDOW_SECONDS", 120, minimum=10),
        repeated_prompt_max_duplicates=_env_int("REPEAT_PROMPT_MAX_DUPLICATES", 3, minimum=2),
    )
    return RuntimeConfig(llm=llm, protection=protection)


def reset_runtime_config_cache() -> None:
    get_runtime_config.cache_clear()
