from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Any

from app.guardrail import GuardrailEngine
from app.i18n import normalize_language
from app.memory_store import get_memory_store
from app.product_helper.service import get_product_helper_service
from app.prompt_runtime import get_prompt_runtime

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _detect_reply_language(user_text: str) -> str:
    text = str(user_text or "")
    english_words = re.findall(r"[A-Za-z]{2,}", text)
    has_cjk = bool(re.search(r"[\u3400-\u9fff]", text))
    if has_cjk and len(english_words) <= 3:
        return "zh"
    if english_words and not has_cjk:
        return "en"
    if len(english_words) >= 4:
        return "en"
    return "zh"


def _resolve_reply_language(user_text: str, preferred_language: str | None = None) -> str:
    explicit = normalize_language(preferred_language, default="")
    if explicit:
        return explicit
    return _detect_reply_language(user_text)


@lru_cache(maxsize=2)
def _get_guardrail_engine(reply_language: str = "zh") -> GuardrailEngine:
    runtime = get_prompt_runtime()
    settings = runtime.guardrail_settings_for_language(reply_language)
    return GuardrailEngine(settings)


def _default_tool_confidence_threshold() -> float:
    return max(0.0, min(1.0, _env_float("TOOL_CALL_CONFIDENCE_THRESHOLD", 0.55)))


def _extract_first_json_object(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw.startswith("{") and raw.endswith("}"):
        return raw

    depth = 0
    start_index = -1
    for index, char in enumerate(raw):
        if char == "{":
            if depth == 0:
                start_index = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and start_index >= 0:
                return raw[start_index : index + 1]
    return ""


def parse_tool_call_json(call_json_text: str) -> dict[str, Any]:
    raw = _extract_first_json_object(call_json_text)
    if not raw:
        return {"tool": "none", "arguments": {}, "confidence": 0.0, "reason": "", "parse_error": "missing_json_object"}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"tool": "none", "arguments": {}, "confidence": 0.0, "reason": "", "parse_error": str(exc)}

    if not isinstance(parsed, dict):
        return {"tool": "none", "arguments": {}, "confidence": 0.0, "reason": "", "parse_error": "json_root_must_be_object"}

    tool = str(parsed.get("tool", "none") or "none").strip()
    arguments = parsed.get("arguments", {})
    confidence = parsed.get("confidence", 0.0)
    reason = str(parsed.get("reason", "")).strip()

    if not isinstance(arguments, dict):
        arguments = {}
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0

    normalized = {
        "tool": tool or "none",
        "arguments": arguments,
        "confidence": max(0.0, min(1.0, confidence_value)),
        "reason": reason,
    }
    if normalized["tool"] != "none" and normalized["confidence"] < _default_tool_confidence_threshold():
        normalized["tool"] = "none"
        normalized["arguments"] = {}
        normalized["forced_none"] = True
    return normalized


async def generate_reply(user_id: str, text: str, preferred_language: str | None = None) -> str:
    runtime = get_prompt_runtime()
    reply_language = _resolve_reply_language(text, preferred_language)
    guardrail = _get_guardrail_engine(reply_language)
    input_check = guardrail.check_input(text)
    if input_check.blocked:
        return input_check.text

    memory = get_memory_store()
    history = memory.render_history_block(user_id=user_id)

    service = get_product_helper_service()
    result = service.handle(
        user_id=user_id,
        text=text,
        preferred_language=reply_language,
        channel="web",
        history_text=history,
    )

    reply = guardrail.sanitize_output(result.reply)
    if not reply:
        reply = runtime.guardrail_settings_for_language(reply_language).fallback_response

    memory.add_exchange(user_id=user_id, user_text=text, assistant_text=reply)
    return reply
