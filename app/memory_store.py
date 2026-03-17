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
                self._append_message_locked(bucket, role="user", content=user_clean, timestamp=now)
            if assistant_clean:
                self._append_message_locked(bucket, role="assistant", content=assistant_clean, timestamp=now)

            self._trim_bucket_locked(bucket, max_messages=self._max_turns * 2)

    def recent_messages(self, *, user_id: str, now_ts: float | None = None) -> tuple[MemoryMessage, ...]:
        if not self._enabled:
            return ()

        uid = str(user_id or "").strip()
        if not uid:
            return ()

        now = time.time() if now_ts is None else float(now_ts)
        with self._lock:
            bucket = self._store.get(uid)
            if not bucket:
                return ()

            self._prune_locked(bucket, now)
            if not bucket:
                return ()
            return tuple(bucket)

    def last_message(self, *, user_id: str, role: str | None = None, now_ts: float | None = None) -> MemoryMessage | None:
        normalized_role = str(role or "").strip()
        for message in reversed(self.recent_messages(user_id=user_id, now_ts=now_ts)):
            if normalized_role and message.role != normalized_role:
                continue
            return message
        return None

    def rebuild_window(
        self,
        *,
        user_id: str,
        keep_turns: int | None = None,
        drop_last_assistant: bool = False,
        now_ts: float | None = None,
    ) -> tuple[MemoryMessage, ...]:
        if not self._enabled:
            return ()

        uid = str(user_id or "").strip()
        if not uid:
            return ()

        now = time.time() if now_ts is None else float(now_ts)
        with self._lock:
            bucket = self._store.get(uid)
            if not bucket:
                return ()

            self._prune_locked(bucket, now)
            if not bucket:
                return ()

            rebuilt = list(bucket)
            if drop_last_assistant and rebuilt and rebuilt[-1].role == "assistant":
                rebuilt.pop()

            max_messages = max(1, keep_turns or self._max_turns) * 2
            rebuilt = rebuilt[-max_messages:]

            normalized: deque[MemoryMessage] = deque()
            for message in rebuilt:
                self._append_message_locked(normalized, role=message.role, content=message.content, timestamp=message.timestamp)

            self._trim_bucket_locked(normalized, max_messages=max_messages)
            self._store[uid] = normalized
            return tuple(normalized)

    def render_history_block(self, *, user_id: str, now_ts: float | None = None) -> str:
        messages = self.recent_messages(user_id=user_id, now_ts=now_ts)
        if not messages:
            return ""

        lines = [
            f"[{'User' if message.role == 'user' else 'Assistant'}] {message.content}"
            for message in messages
        ]
        rendered = self._render_recent_lines(lines)
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

    @staticmethod
    def _append_message_locked(
        bucket: deque[MemoryMessage],
        *,
        role: str,
        content: str,
        timestamp: float,
    ) -> None:
        normalized_role = str(role or "").strip().lower()
        normalized_content = str(content or "").strip()
        if normalized_role not in {"user", "assistant"} or not normalized_content:
            return

        if bucket and bucket[-1].role == normalized_role and bucket[-1].content == normalized_content:
            bucket[-1] = MemoryMessage(role=normalized_role, content=normalized_content, timestamp=timestamp)
            return
        bucket.append(MemoryMessage(role=normalized_role, content=normalized_content, timestamp=timestamp))

    @staticmethod
    def _trim_bucket_locked(bucket: deque[MemoryMessage], *, max_messages: int) -> None:
        while len(bucket) > max(1, max_messages):
            bucket.popleft()

    def _render_recent_lines(self, lines: list[str]) -> str:
        if not lines:
            return ""

        selected: list[str] = []
        total_chars = 0
        for line in reversed(lines):
            line_text = str(line or "").strip()
            if not line_text:
                continue

            added_chars = len(line_text) + (1 if selected else 0)
            if selected and (total_chars + added_chars) > self._max_history_chars:
                break

            if not selected and len(line_text) > self._max_history_chars:
                return line_text[:self._max_history_chars].rstrip()

            selected.append(line_text)
            total_chars += added_chars

        selected.reverse()
        return "\n".join(selected).strip()


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
