import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock


@dataclass(frozen=True)
class MemoryMessage:
    role: str
    content: str
    timestamp: float


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _normalize_memory_text(text: str, max_chars: int) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if max_chars <= 0:
        return normalized
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars].rstrip()}..."


class ConversationMemoryStore:
    def __init__(
        self,
        *,
        enabled: bool,
        max_turns: int,
        ttl_seconds: int,
        max_message_chars: int,
        max_history_chars: int,
    ) -> None:
        self._enabled = enabled
        self._max_turns = max(1, max_turns)
        self._ttl_seconds = max(30, ttl_seconds)
        self._max_message_chars = max(32, max_message_chars)
        self._max_history_chars = max(64, max_history_chars)
        self._store: dict[str, deque[MemoryMessage]] = defaultdict(deque)
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def add_exchange(
        self,
        *,
        user_id: str,
        user_text: str,
        assistant_text: str,
        now_ts: float | None = None,
    ) -> None:
        if not self._enabled:
            return

        uid = str(user_id or "").strip()
        if not uid:
            return

        now = time.time() if now_ts is None else float(now_ts)
        user_clean = _normalize_memory_text(user_text, self._max_message_chars)
        assistant_clean = _normalize_memory_text(assistant_text, self._max_message_chars)
        if not user_clean and not assistant_clean:
            return

        with self._lock:
            bucket = self._store[uid]
            self._prune_locked(bucket, now)

            if user_clean:
                bucket.append(MemoryMessage(role="user", content=user_clean, timestamp=now))
            if assistant_clean:
                bucket.append(MemoryMessage(role="assistant", content=assistant_clean, timestamp=now))

            max_messages = self._max_turns * 2
            while len(bucket) > max_messages:
                bucket.popleft()

    def render_history_block(self, *, user_id: str, now_ts: float | None = None) -> str:
        if not self._enabled:
            return ""

        uid = str(user_id or "").strip()
        if not uid:
            return ""

        now = time.time() if now_ts is None else float(now_ts)
        with self._lock:
            bucket = self._store.get(uid)
            if not bucket:
                return ""

            self._prune_locked(bucket, now)
            if not bucket:
                return ""

            lines: list[str] = []
            for message in bucket:
                role_label = "User" if message.role == "user" else "Assistant"
                lines.append(f"[{role_label}] {message.content}")

            rendered = "\n".join(lines).strip()
            if len(rendered) <= self._max_history_chars:
                return rendered
            return f"{rendered[:self._max_history_chars].rstrip()}..."

    def clear_user(self, user_id: str) -> None:
        uid = str(user_id or "").strip()
        if not uid:
            return
        with self._lock:
            self._store.pop(uid, None)

    def clear_all(self) -> None:
        with self._lock:
            self._store.clear()

    def _prune_locked(self, bucket: deque[MemoryMessage], now_ts: float) -> None:
        if not bucket:
            return
        threshold = now_ts - float(self._ttl_seconds)
        while bucket and bucket[0].timestamp < threshold:
            bucket.popleft()


@lru_cache(maxsize=1)
def get_memory_store() -> ConversationMemoryStore:
    return ConversationMemoryStore(
        enabled=_env_bool("OPENCLAW_MEMORY_ENABLED", True),
        max_turns=_env_int("OPENCLAW_MEMORY_MAX_TURNS", 4),
        ttl_seconds=_env_int("OPENCLAW_MEMORY_TTL_SECONDS", 1800),
        max_message_chars=_env_int("OPENCLAW_MEMORY_MAX_MESSAGE_CHARS", 240),
        max_history_chars=_env_int("OPENCLAW_MEMORY_MAX_HISTORY_CHARS", 1200),
    )


def reset_memory_store_cache() -> None:
    get_memory_store.cache_clear()

