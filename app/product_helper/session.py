from __future__ import annotations

import os
import time
from collections import defaultdict
from functools import lru_cache
from threading import Lock

from app.product_helper.models import SessionState


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class ProductHelperSessionStore:
    def __init__(self, ttl_seconds: int = 1800) -> None:
        self._ttl_seconds = max(300, ttl_seconds)
        self._store: dict[str, SessionState] = defaultdict(SessionState)
        self._lock = Lock()

    def get(self, user_id: str) -> SessionState:
        now = time.time()
        uid = str(user_id or "").strip()
        if not uid:
            return SessionState(updated_at=now)
        with self._lock:
            state = self._store.get(uid)
            if state is None or now - state.updated_at > self._ttl_seconds:
                state = SessionState(updated_at=now)
                self._store[uid] = state
            return SessionState(
                language=state.language,
                intake=dict(state.intake),
                current_use_case=state.current_use_case,
                current_intent=state.current_intent,
                pending_action=state.pending_action,
                pending_context=dict(state.pending_context),
                last_user_need=state.last_user_need,
                shortlisted_products=list(state.shortlisted_products),
                shortlisted_ingredients=list(state.shortlisted_ingredients),
                last_constitutions=list(state.last_constitutions),
                last_question=state.last_question,
                updated_at=state.updated_at,
            )

    def upsert(
        self,
        user_id: str,
        *,
        language: str | None = None,
        intake: dict | None = None,
        current_use_case: str | None = None,
        current_intent: str | None = None,
        pending_action: str | None = None,
        pending_context: dict | None = None,
        last_user_need: str | None = None,
        shortlisted_products: list[str] | None = None,
        shortlisted_ingredients: list[str] | None = None,
        last_constitutions: list[str] | None = None,
        last_question: str | None = None,
    ) -> SessionState:
        uid = str(user_id or "").strip()
        if not uid:
            return SessionState(
                language=language or "zh",
                intake=dict(intake or {}),
                current_use_case=current_use_case or "",
                current_intent=current_intent or "",
                pending_action=pending_action or "",
                pending_context=dict(pending_context or {}),
                last_user_need=last_user_need or "",
                shortlisted_products=list(shortlisted_products or []),
                shortlisted_ingredients=list(shortlisted_ingredients or []),
                last_constitutions=list(last_constitutions or []),
                last_question=last_question or "",
                updated_at=time.time(),
            )

        with self._lock:
            now = time.time()
            existing = self._store.get(uid)
            if existing is None or now - existing.updated_at > self._ttl_seconds:
                existing = SessionState(updated_at=now)
            updated = SessionState(
                language=language or existing.language,
                intake=dict(intake or existing.intake),
                current_use_case=current_use_case if current_use_case is not None else existing.current_use_case,
                current_intent=current_intent if current_intent is not None else existing.current_intent,
                pending_action=pending_action if pending_action is not None else existing.pending_action,
                pending_context=dict(pending_context if pending_context is not None else existing.pending_context),
                last_user_need=last_user_need if last_user_need is not None else existing.last_user_need,
                shortlisted_products=list(shortlisted_products if shortlisted_products is not None else existing.shortlisted_products),
                shortlisted_ingredients=list(shortlisted_ingredients if shortlisted_ingredients is not None else existing.shortlisted_ingredients),
                last_constitutions=list(last_constitutions if last_constitutions is not None else existing.last_constitutions),
                last_question=last_question if last_question is not None else existing.last_question,
                updated_at=now,
            )
            self._store[uid] = updated
            return updated

    def clear(self, user_id: str) -> None:
        uid = str(user_id or "").strip()
        if not uid:
            return
        with self._lock:
            self._store.pop(uid, None)

    def clear_all(self) -> None:
        with self._lock:
            self._store.clear()


@lru_cache(maxsize=1)
def get_product_helper_session_store() -> ProductHelperSessionStore:
    return ProductHelperSessionStore(ttl_seconds=_env_int("PRODUCT_HELPER_SESSION_TTL_SECONDS", 1800))


def reset_product_helper_session_store() -> None:
    get_product_helper_session_store.cache_clear()
