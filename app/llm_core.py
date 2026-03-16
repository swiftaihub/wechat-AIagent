from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.guardrail import GuardrailEngine
from app.i18n import normalize_language
from app.llm_provider import llm_chat
from app.memory_store import get_memory_store
from app.product_helper.config import load_runtime_limits_config
from app.product_helper.guardrails import enforce_domain_response_policy
from app.product_helper.service import get_product_helper_service
from app.prompt_runtime import get_prompt_runtime
from app.runtime_config import get_runtime_config
from app.usage_guard import get_usage_guard

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplyOutcome:
    ok: bool
    reply: str
    blocked: bool = False
    error_code: str | None = None
    internal_reason: str | None = None
    retry_after_seconds: int | None = None
    unblock_at: str | None = None


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


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


def _naturalization_enabled() -> bool:
    return _env_bool("OPENCLAW_NATURALIZE_ENABLED", False)


def _trim_to_channel_limit(text: str, channel: str) -> str:
    limits = load_runtime_limits_config()
    channel_name = "wechat" if channel == "wechat" else "web"
    channel_limits = limits.channels.get(channel_name, {})
    max_chars = int(channel_limits.get("max_output_chars", 480) or 480)
    trim_suffix = str(limits.shared.get("trim_suffix", "…")).strip() or "…"
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max(0, max_chars - len(trim_suffix))].rstrip()}{trim_suffix}"


def _trim_history_context(history_text: str) -> str:
    normalized = str(history_text or "").strip()
    if not normalized:
        return ""
    max_messages = get_runtime_config().protection.max_context_messages
    lines = [line for line in normalized.splitlines() if line.strip()]
    if len(lines) <= max_messages:
        return normalized
    return "\n".join(lines[-max_messages:])


async def _maybe_naturalize_reply(*, user_id: str, user_text: str, result, channel: str) -> str:
    if not _naturalization_enabled():
        return result.reply
    if not bool(result.metadata.get("allow_naturalization", False)):
        return result.reply

    runtime = get_prompt_runtime()
    try:
        system_prompt = runtime.system_prompt("tool_final", language=result.language)
        tool_result_json = json.dumps(
            {
                "intent": result.intent,
                "mode": result.mode,
                "language": result.language,
                "channel": channel,
                "direct_answer_first": True,
                "ask_at_most_one_followup": True,
                "draft_reply": result.reply,
                "safety_notes": list(result.safety_notes),
                "products": [
                    {
                        "name": recommendation.product.name,
                        "tagline": recommendation.product.tagline,
                        "why": list(recommendation.why),
                        "taste": recommendation.taste,
                    }
                    for recommendation in result.product_recommendations[:3]
                ],
                "links": [
                    {
                        "zh_title": link.zh_title,
                        "en_title": link.en_title,
                        "url": link.url,
                    }
                    for link in result.support_links[:2]
                ],
            },
            ensure_ascii=False,
        )
        user_prompt = runtime.render_user_prompt(
            profile="tool_final",
            user_text=user_text,
            language=result.language,
            extra_variables={"tool_result_json": tool_result_json},
        )
    except KeyError:
        return result.reply

    try:
        naturalized = await llm_chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Naturalization fallback triggered for intent=%s error=%s", result.intent, exc)
        return result.reply

    naturalized = enforce_domain_response_policy(naturalized, result.language, fallback_text=result.reply)
    return naturalized or result.reply


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


async def generate_reply_result(
    user_id: str,
    text: str,
    preferred_language: str | None = None,
    channel: str = "web",
) -> ReplyOutcome:
    runtime = get_prompt_runtime()
    reply_language = _resolve_reply_language(text, preferred_language)
    usage_guard = get_usage_guard()
    admission = await usage_guard.admit_request(
        user_id=user_id,
        text=text,
        preferred_language=reply_language,
    )
    if not admission.allowed:
        rejection = admission.rejection
        fallback_text = runtime.guardrail_settings_for_language(reply_language).fallback_response
        return ReplyOutcome(
            ok=False,
            reply=rejection.user_message if rejection else fallback_text,
            blocked=True,
            error_code=rejection.code if rejection else "REQUEST_BLOCKED",
            internal_reason=rejection.internal_reason if rejection else "request_blocked",
            retry_after_seconds=rejection.retry_after_seconds if rejection else None,
            unblock_at=rejection.unblock_at if rejection else None,
        )

    lease = admission.lease
    guardrail = _get_guardrail_engine(reply_language)
    try:
        input_check = guardrail.check_input(admission.normalized_text)
        if input_check.blocked:
            return ReplyOutcome(
                ok=False,
                reply=input_check.text,
                blocked=True,
                error_code="INPUT_GUARDRAIL_BLOCKED",
                internal_reason="prompt_guardrail_blocked",
            )

        memory = get_memory_store()
        history = _trim_history_context(memory.render_history_block(user_id=admission.user_id))

        service = get_product_helper_service()
        result = service.handle(
            user_id=admission.user_id,
            text=admission.normalized_text,
            preferred_language=reply_language,
            channel=channel,
            history_text=history,
        )

        raw_reply = await _maybe_naturalize_reply(
            user_id=admission.user_id,
            user_text=admission.normalized_text,
            result=result,
            channel=channel,
        )
        raw_reply = enforce_domain_response_policy(raw_reply, reply_language, fallback_text=result.reply)
        reply = guardrail.sanitize_output(raw_reply)
        reply = _trim_to_channel_limit(reply, channel)
        if not reply:
            reply = runtime.guardrail_settings_for_language(reply_language).fallback_response

        memory.add_exchange(user_id=admission.user_id, user_text=admission.normalized_text, assistant_text=reply)
        return ReplyOutcome(ok=True, reply=reply)
    finally:
        await usage_guard.release(lease)


async def generate_reply(user_id: str, text: str, preferred_language: str | None = None, channel: str = "web") -> str:
    outcome = await generate_reply_result(
        user_id=user_id,
        text=text,
        preferred_language=preferred_language,
        channel=channel,
    )
    return outcome.reply
