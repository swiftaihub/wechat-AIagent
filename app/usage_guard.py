from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from threading import Lock

from app.i18n import normalize_language
from app.metrics import get_runtime_metrics
from app.runtime_config import UsageProtectionConfig, get_runtime_config

try:
    from redis import asyncio as redis_async
except Exception:  # pragma: no cover - optional dependency fallback
    redis_async = None


logger = logging.getLogger(__name__)

_USAGE_LIMIT_OVERRIDE_CODES = {
    "RATE_LIMITED",
    "ABUSE_BLOCKED",
    "SESSION_LIMIT_REACHED",
    "HOURLY_QUOTA_EXCEEDED",
    "DAILY_QUOTA_EXCEEDED",
}


@dataclass(frozen=True)
class GuardRejection:
    code: str
    internal_reason: str
    user_message: str
    retry_after_seconds: int | None = None
    unblock_at: str | None = None


@dataclass(frozen=True)
class RequestLease:
    user_id: str
    token: str
    admitted_at: float


@dataclass(frozen=True)
class GuardAdmission:
    allowed: bool
    lease: RequestLease | None = None
    rejection: GuardRejection | None = None
    normalized_text: str = ""
    user_id: str = ""


@dataclass
class _BlockState:
    code: str
    internal_reason: str
    until_ts: float


@dataclass
class _MemoryUserState:
    recent_requests: deque[float]
    hour_key: str
    hour_count: int
    day_key: str
    day_count: int
    session_count: int
    session_last_ts: float
    prompt_counts: dict[str, tuple[int, float]]
    block: _BlockState | None
    inflight_token: str | None
    inflight_until_ts: float


def _hash_user_id(user_id: str) -> str:
    return hashlib.sha256(str(user_id or "").encode("utf-8")).hexdigest()[:12]


def _hash_prompt(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _normalize_prompt_text(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _has_disallowed_control_chars(text: str) -> bool:
    for char in str(text or ""):
        if ord(char) < 32 and char not in "\t\n\r":
            return True
    return False


def _normalize_user_id(user_id: str) -> str:
    normalized = " ".join(str(user_id or "").strip().split())
    if not normalized:
        return "anonymous"
    return normalized[:128]


def _looks_ascii_text(value: str) -> bool:
    return bool(value) and value.isascii()


def _load_localized_env_message(env_key: str, language: str) -> str:
    direct_value = os.getenv(env_key, "").strip()
    zh_value = os.getenv(f"{env_key}_ZH", "").strip()
    en_value = os.getenv(f"{env_key}_EN", "").strip()

    if not zh_value and direct_value and not _looks_ascii_text(direct_value):
        zh_value = direct_value
    if not en_value and direct_value and _looks_ascii_text(direct_value):
        en_value = direct_value

    return en_value if language == "en" else zh_value


def _utc_now_iso_from_ts(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _seconds_until_next_hour(now_ts: float) -> int:
    now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    next_hour = (now_dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    return max(1, int((next_hour - now_dt).total_seconds()))


def _seconds_until_next_day(now_ts: float) -> int:
    now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
    next_day = (now_dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    return max(1, int((next_day - now_dt).total_seconds()))


def _window_key(now_ts: float, fmt: str) -> str:
    return datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime(fmt)


def _format_retry_delay(seconds: int | None, language: str) -> str:
    if seconds is None:
        return ""
    total = max(1, int(seconds))
    if total < 60:
        return f"{total} seconds" if language == "en" else f"{total} 秒"
    minutes = math.ceil(total / 60)
    if minutes < 60:
        return f"about {minutes} minutes" if language == "en" else f"约 {minutes} 分钟"
    hours = math.ceil(minutes / 60)
    return f"about {hours} hours" if language == "en" else f"约 {hours} 小时"


def _rejection_message(code: str, language: str, retry_after_seconds: int | None) -> str:
    if code in _USAGE_LIMIT_OVERRIDE_CODES:
        override_message = _load_localized_env_message("USAGE_LIMIT_MESSAGE", language)
        if override_message:
            return override_message

    delay_text = _format_retry_delay(retry_after_seconds, language)
    if language == "en":
        messages = {
            "INVALID_REQUEST": "I could not read that request clearly. Please send a short, plain message and try again.",
            "INPUT_TOO_LONG": "That message is a bit too long for one request. Please shorten it and send the key part first.",
            "RATE_LIMITED": f"You've been sending messages a little too quickly. Please pause and try again in {delay_text or 'a little while'}.",
            "ABUSE_BLOCKED": f"I'm pausing this chat for a bit because the same request was repeated too frequently. Please try again in {delay_text or 'a little while'}.",
            "SESSION_LIMIT_REACHED": f"This conversation has reached its current session limit. Please come back in {delay_text or 'a little while'} to continue.",
            "HOURLY_QUOTA_EXCEEDED": f"You've reached the current hourly limit for this chat. Please try again in {delay_text or 'about an hour'}.",
            "DAILY_QUOTA_EXCEEDED": f"You've reached the current daily limit for this chat. Please try again in {delay_text or 'about a day'}.",
            "CONCURRENT_REQUEST_BLOCKED": "I'm still working on your previous message. Please wait for that reply before sending another one.",
        }
    else:
        messages = {
            "INVALID_REQUEST": "这条消息我没有读取清楚。请尽量用简短明确的文字再发一次。",
            "INPUT_TOO_LONG": "这条消息有点长，先发最关键的部分会更稳妥一些。",
            "RATE_LIMITED": f"你发送得有点快了，我先帮你暂停一下。请在{delay_text or '稍后'}再试。",
            "ABUSE_BLOCKED": f"同一类消息短时间内重复过多，我先帮你暂停一下。请在{delay_text or '稍后'}再试。",
            "SESSION_LIMIT_REACHED": f"这段会话已经达到当前上限，我先帮你冷却一下。请在{delay_text or '稍后'}再继续。",
            "HOURLY_QUOTA_EXCEEDED": f"你当前这一小时的使用次数已经到上限。请在{delay_text or '稍后'}再试。",
            "DAILY_QUOTA_EXCEEDED": f"你今天的使用次数已经到上限。请在{delay_text or '明天'}再试。",
            "CONCURRENT_REQUEST_BLOCKED": "我还在处理你上一条消息，等这条回复回来后再发下一条会更稳。",
        }
    return messages.get(code, messages["INVALID_REQUEST"])


def _build_rejection(code: str, internal_reason: str, language: str, until_ts: float | None = None) -> GuardRejection:
    retry_after = None
    unblock_at = None
    if until_ts is not None:
        retry_after = max(1, int(until_ts - time.time()))
        unblock_at = _utc_now_iso_from_ts(until_ts)
    return GuardRejection(
        code=code,
        internal_reason=internal_reason,
        user_message=_rejection_message(code, language, retry_after),
        retry_after_seconds=retry_after,
        unblock_at=unblock_at,
    )


class _BaseBackend:
    async def admit(self, *, user_id: str, prompt_hash: str, now_ts: float) -> tuple[bool, str | None, str | None, float | None, str | None]:
        raise NotImplementedError

    async def release(self, *, user_id: str, token: str) -> None:
        raise NotImplementedError


class _MemoryUsageBackend(_BaseBackend):
    def __init__(self, config: UsageProtectionConfig) -> None:
        self._config = config
        self._lock = Lock()
        self._users: dict[str, _MemoryUserState] = {}

    async def admit(self, *, user_id: str, prompt_hash: str, now_ts: float) -> tuple[bool, str | None, str | None, float | None, str | None]:
        with self._lock:
            state = self._users.get(user_id)
            if state is None:
                state = _MemoryUserState(
                    recent_requests=deque(),
                    hour_key=_window_key(now_ts, "%Y%m%d%H"),
                    hour_count=0,
                    day_key=_window_key(now_ts, "%Y%m%d"),
                    day_count=0,
                    session_count=0,
                    session_last_ts=0.0,
                    prompt_counts={},
                    block=None,
                    inflight_token=None,
                    inflight_until_ts=0.0,
                )
                self._users[user_id] = state

            self._prune_locked(state, now_ts)

            if state.block and state.block.until_ts > now_ts:
                return False, state.block.code, state.block.internal_reason, state.block.until_ts, None
            state.block = None

            if state.inflight_token and state.inflight_until_ts > now_ts:
                return False, "CONCURRENT_REQUEST_BLOCKED", "concurrent_request", state.inflight_until_ts, None

            if state.session_last_ts and (now_ts - state.session_last_ts) > float(self._config.session_ttl_seconds):
                state.session_count = 0
                state.prompt_counts.clear()

            if len(state.recent_requests) >= self._config.rate_limit_max_requests:
                until_ts = now_ts + float(self._config.rapid_abuse_block_minutes * 60)
                state.block = _BlockState(
                    code="RATE_LIMITED",
                    internal_reason="rapid_requests",
                    until_ts=until_ts,
                )
                return False, state.block.code, state.block.internal_reason, until_ts, None

            prompt_count, expires_at = state.prompt_counts.get(prompt_hash, (0, 0.0))
            if expires_at <= now_ts:
                prompt_count = 0
            if prompt_count + 1 >= self._config.repeated_prompt_max_duplicates:
                until_ts = now_ts + float(self._config.rapid_abuse_block_minutes * 60)
                state.block = _BlockState(
                    code="ABUSE_BLOCKED",
                    internal_reason="repeated_identical_prompts",
                    until_ts=until_ts,
                )
                return False, state.block.code, state.block.internal_reason, until_ts, None

            hour_key = _window_key(now_ts, "%Y%m%d%H")
            if state.hour_key != hour_key:
                state.hour_key = hour_key
                state.hour_count = 0
            if state.hour_count >= self._config.max_requests_per_hour:
                until_ts = now_ts + float(_seconds_until_next_hour(now_ts))
                state.block = _BlockState(
                    code="HOURLY_QUOTA_EXCEEDED",
                    internal_reason="hourly_quota",
                    until_ts=until_ts,
                )
                return False, state.block.code, state.block.internal_reason, until_ts, None

            day_key = _window_key(now_ts, "%Y%m%d")
            if state.day_key != day_key:
                state.day_key = day_key
                state.day_count = 0
            if state.day_count >= self._config.max_requests_per_day:
                until_ts = now_ts + float(_seconds_until_next_day(now_ts))
                state.block = _BlockState(
                    code="DAILY_QUOTA_EXCEEDED",
                    internal_reason="daily_quota",
                    until_ts=until_ts,
                )
                return False, state.block.code, state.block.internal_reason, until_ts, None

            if state.session_count >= self._config.max_messages_per_user_session:
                until_ts = now_ts + float(self._config.user_session_cooldown_minutes * 60)
                state.block = _BlockState(
                    code="SESSION_LIMIT_REACHED",
                    internal_reason="session_message_ceiling",
                    until_ts=until_ts,
                )
                return False, state.block.code, state.block.internal_reason, until_ts, None

            token = uuid.uuid4().hex
            state.recent_requests.append(now_ts)
            state.prompt_counts[prompt_hash] = (
                prompt_count + 1,
                now_ts + float(self._config.repeated_prompt_window_seconds),
            )
            state.hour_count += 1
            state.day_count += 1
            state.session_count += 1
            state.session_last_ts = now_ts
            state.inflight_token = token
            state.inflight_until_ts = now_ts + float(self._config.inflight_ttl_seconds)
            return True, None, None, None, token

    async def release(self, *, user_id: str, token: str) -> None:
        with self._lock:
            state = self._users.get(user_id)
            if not state:
                return
            if state.inflight_token == token:
                state.inflight_token = None
                state.inflight_until_ts = 0.0

    def _prune_locked(self, state: _MemoryUserState, now_ts: float) -> None:
        rate_threshold = now_ts - float(self._config.rate_limit_window_seconds)
        while state.recent_requests and state.recent_requests[0] < rate_threshold:
            state.recent_requests.popleft()

        expired_hashes = [
            prompt_hash
            for prompt_hash, (_, expires_at) in state.prompt_counts.items()
            if expires_at <= now_ts
        ]
        for prompt_hash in expired_hashes:
            state.prompt_counts.pop(prompt_hash, None)

        if state.inflight_token and state.inflight_until_ts <= now_ts:
            state.inflight_token = None
            state.inflight_until_ts = 0.0

        if state.block and state.block.until_ts <= now_ts:
            state.block = None


class _RedisUsageBackend(_BaseBackend):
    def __init__(self, config: UsageProtectionConfig) -> None:
        self._config = config
        self._redis = redis_async.from_url(config.redis_url, encoding="utf-8", decode_responses=True)

    async def admit(self, *, user_id: str, prompt_hash: str, now_ts: float) -> tuple[bool, str | None, str | None, float | None, str | None]:
        block_key = self._key("block", user_id)
        block_raw = await self._redis.get(block_key)
        if block_raw:
            block = json.loads(block_raw)
            return False, block["code"], block["reason"], float(block["until_ts"]), None

        token = uuid.uuid4().hex
        inflight_key = self._key("inflight", user_id)
        acquired = await self._redis.set(
            inflight_key,
            token,
            ex=self._config.inflight_ttl_seconds,
            nx=True,
        )
        if not acquired:
            ttl = await self._redis.ttl(inflight_key)
            until_ts = now_ts + float(max(1, ttl))
            return False, "CONCURRENT_REQUEST_BLOCKED", "concurrent_request", until_ts, None

        try:
            rate_key = self._key("rate", user_id)
            await self._redis.zremrangebyscore(rate_key, "-inf", now_ts - float(self._config.rate_limit_window_seconds))
            await self._redis.zadd(rate_key, {f"{now_ts}:{token}": now_ts})
            await self._redis.expire(rate_key, self._config.rate_limit_window_seconds + 5)
            request_count = await self._redis.zcard(rate_key)
            if int(request_count) > self._config.rate_limit_max_requests:
                until_ts = now_ts + float(self._config.rapid_abuse_block_minutes * 60)
                await self._set_block(
                    user_id=user_id,
                    code="RATE_LIMITED",
                    reason="rapid_requests",
                    until_ts=until_ts,
                )
                await self.release(user_id=user_id, token=token)
                return False, "RATE_LIMITED", "rapid_requests", until_ts, token

            prompt_key = self._key("prompt", user_id, prompt_hash)
            prompt_count = await self._redis.incr(prompt_key)
            await self._redis.expire(prompt_key, self._config.repeated_prompt_window_seconds)
            if int(prompt_count) >= self._config.repeated_prompt_max_duplicates:
                until_ts = now_ts + float(self._config.rapid_abuse_block_minutes * 60)
                await self._set_block(
                    user_id=user_id,
                    code="ABUSE_BLOCKED",
                    reason="repeated_identical_prompts",
                    until_ts=until_ts,
                )
                await self.release(user_id=user_id, token=token)
                return False, "ABUSE_BLOCKED", "repeated_identical_prompts", until_ts, token

            hour_key = self._key("hour", user_id, _window_key(now_ts, "%Y%m%d%H"))
            hour_count = await self._redis.incr(hour_key)
            await self._redis.expire(hour_key, _seconds_until_next_hour(now_ts) + 5)
            if int(hour_count) > self._config.max_requests_per_hour:
                until_ts = now_ts + float(_seconds_until_next_hour(now_ts))
                await self._set_block(
                    user_id=user_id,
                    code="HOURLY_QUOTA_EXCEEDED",
                    reason="hourly_quota",
                    until_ts=until_ts,
                )
                await self.release(user_id=user_id, token=token)
                return False, "HOURLY_QUOTA_EXCEEDED", "hourly_quota", until_ts, token

            day_key = self._key("day", user_id, _window_key(now_ts, "%Y%m%d"))
            day_count = await self._redis.incr(day_key)
            await self._redis.expire(day_key, _seconds_until_next_day(now_ts) + 5)
            if int(day_count) > self._config.max_requests_per_day:
                until_ts = now_ts + float(_seconds_until_next_day(now_ts))
                await self._set_block(
                    user_id=user_id,
                    code="DAILY_QUOTA_EXCEEDED",
                    reason="daily_quota",
                    until_ts=until_ts,
                )
                await self.release(user_id=user_id, token=token)
                return False, "DAILY_QUOTA_EXCEEDED", "daily_quota", until_ts, token

            session_key = self._key("session", user_id)
            session_count = await self._redis.incr(session_key)
            await self._redis.expire(session_key, self._config.session_ttl_seconds)
            if int(session_count) > self._config.max_messages_per_user_session:
                until_ts = now_ts + float(self._config.user_session_cooldown_minutes * 60)
                await self._set_block(
                    user_id=user_id,
                    code="SESSION_LIMIT_REACHED",
                    reason="session_message_ceiling",
                    until_ts=until_ts,
                )
                await self.release(user_id=user_id, token=token)
                return False, "SESSION_LIMIT_REACHED", "session_message_ceiling", until_ts, token

            return True, None, None, None, token
        except Exception:
            await self.release(user_id=user_id, token=token)
            raise

    async def release(self, *, user_id: str, token: str) -> None:
        inflight_key = self._key("inflight", user_id)
        current = await self._redis.get(inflight_key)
        if current == token:
            await self._redis.delete(inflight_key)

    async def _set_block(self, *, user_id: str, code: str, reason: str, until_ts: float) -> None:
        block_key = self._key("block", user_id)
        payload = json.dumps(
            {
                "code": code,
                "reason": reason,
                "until_ts": until_ts,
            }
        )
        ttl_seconds = max(1, int(until_ts - time.time()))
        await self._redis.set(block_key, payload, ex=ttl_seconds)

    @staticmethod
    def _key(kind: str, user_id: str, extra: str | None = None) -> str:
        base = f"openclaw:{kind}:{user_id}"
        if extra:
            return f"{base}:{extra}"
        return base


class UsageGuard:
    def __init__(self) -> None:
        self._config = get_runtime_config().protection
        self._metrics = get_runtime_metrics()
        self._memory_backend = _MemoryUsageBackend(self._config)
        if self._config.redis_url and redis_async is not None:
            self._backend: _BaseBackend = _RedisUsageBackend(self._config)
        else:
            self._backend = self._memory_backend

    async def admit_request(
        self,
        *,
        user_id: str,
        text: str,
        preferred_language: str | None = None,
    ) -> GuardAdmission:
        language = normalize_language(preferred_language)
        normalized_text = _normalize_prompt_text(text)
        stable_user_id = _normalize_user_id(user_id)
        user_hash = _hash_user_id(stable_user_id)

        if not normalized_text:
            self._metrics.increment("request_blocked_invalid")
            rejection = _build_rejection("INVALID_REQUEST", "empty_or_whitespace_request", language)
            logger.info("Usage guard blocked user=%s code=%s reason=%s", user_hash, rejection.code, rejection.internal_reason)
            return GuardAdmission(
                allowed=False,
                rejection=rejection,
                normalized_text="",
                user_id=stable_user_id,
            )

        if _has_disallowed_control_chars(normalized_text):
            self._metrics.increment("request_blocked_invalid")
            rejection = _build_rejection("INVALID_REQUEST", "control_character_payload", language)
            logger.info("Usage guard blocked user=%s code=%s reason=%s", user_hash, rejection.code, rejection.internal_reason)
            return GuardAdmission(
                allowed=False,
                rejection=rejection,
                normalized_text="",
                user_id=stable_user_id,
            )

        if len(normalized_text) > self._config.max_input_chars:
            self._metrics.increment("request_blocked_input_too_long")
            rejection = _build_rejection("INPUT_TOO_LONG", "input_length_limit", language)
            logger.info(
                "Usage guard blocked user=%s code=%s length=%d",
                user_hash,
                rejection.code,
                len(normalized_text),
            )
            return GuardAdmission(
                allowed=False,
                rejection=rejection,
                normalized_text=normalized_text,
                user_id=stable_user_id,
            )

        now_ts = time.time()
        prompt_hash = _hash_prompt(normalized_text)
        try:
            allowed, code, reason, until_ts, token = await self._backend.admit(
                user_id=stable_user_id,
                prompt_hash=prompt_hash,
                now_ts=now_ts,
            )
        except Exception as exc:
            if isinstance(self._backend, _RedisUsageBackend):
                logger.warning("Redis usage guard backend unavailable; falling back to in-memory protection: %s", exc)
                self._backend = self._memory_backend
                allowed, code, reason, until_ts, token = await self._backend.admit(
                    user_id=stable_user_id,
                    prompt_hash=prompt_hash,
                    now_ts=now_ts,
                )
            else:
                raise
        if not allowed or token is None:
            rejection = _build_rejection(code or "INVALID_REQUEST", reason or "guard_rejected", language, until_ts)
            self._increment_block_metric(rejection.code)
            logger.info(
                "Usage guard blocked user=%s code=%s reason=%s retry_after=%s",
                user_hash,
                rejection.code,
                rejection.internal_reason,
                rejection.retry_after_seconds,
            )
            return GuardAdmission(
                allowed=False,
                rejection=rejection,
                normalized_text=normalized_text,
                user_id=stable_user_id,
            )

        self._metrics.increment("request_accepted")
        logger.info("Usage guard accepted user=%s input_chars=%d", user_hash, len(normalized_text))
        return GuardAdmission(
            allowed=True,
            lease=RequestLease(user_id=stable_user_id, token=token, admitted_at=now_ts),
            normalized_text=normalized_text,
            user_id=stable_user_id,
        )

    async def release(self, lease: RequestLease | None) -> None:
        if lease is None:
            return
        try:
            await self._backend.release(user_id=lease.user_id, token=lease.token)
        except Exception as exc:
            if isinstance(self._backend, _RedisUsageBackend):
                logger.warning("Redis usage guard release failed; continuing with in-memory fallback: %s", exc)
                self._backend = self._memory_backend
                await self._backend.release(user_id=lease.user_id, token=lease.token)
            else:
                raise

    def _increment_block_metric(self, code: str) -> None:
        mapping = {
            "RATE_LIMITED": "request_blocked_rate_limit",
            "ABUSE_BLOCKED": "request_blocked_abuse",
            "SESSION_LIMIT_REACHED": "request_blocked_session_limit",
            "HOURLY_QUOTA_EXCEEDED": "request_blocked_hourly_quota",
            "DAILY_QUOTA_EXCEEDED": "request_blocked_daily_quota",
            "CONCURRENT_REQUEST_BLOCKED": "request_blocked_concurrent",
            "INPUT_TOO_LONG": "request_blocked_input_too_long",
        }
        self._metrics.increment(mapping.get(code, "request_blocked_other"))


@lru_cache(maxsize=1)
def get_usage_guard() -> UsageGuard:
    return UsageGuard()


def reset_usage_guard_cache() -> None:
    get_usage_guard.cache_clear()
