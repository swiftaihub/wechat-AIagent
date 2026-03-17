from __future__ import annotations

from difflib import SequenceMatcher
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
from app.memory_store import MemoryMessage, get_memory_store
from app.product_helper.config import load_runtime_limits_config
from app.product_helper.guardrails import enforce_domain_response_policy
from app.product_helper.service import get_product_helper_service
from app.product_helper.validation import sanitize_helper_result, sanitize_reply_links, validate_final_reply
from app.prompt_runtime import get_prompt_runtime
from app.runtime_config import get_runtime_config
from app.text_trimming import looks_cut_mid_sentence, smart_trim_to_limit
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


def _repeat_sequence_threshold() -> float:
    return max(0.5, min(0.99, _env_float("OPENCLAW_REPEAT_SEQUENCE_THRESHOLD", 0.92)))


def _repeat_token_threshold() -> float:
    return max(0.5, min(0.99, _env_float("OPENCLAW_REPEAT_TOKEN_THRESHOLD", 0.88)))


def _naturalization_enabled() -> bool:
    return _env_bool("OPENCLAW_NATURALIZE_ENABLED", False)


def _force_naturalization_enabled() -> bool:
    return _env_bool("OPENCLAW_FORCE_NATURALIZATION", False)


def _naturalization_requested() -> bool:
    return _naturalization_enabled() or _force_naturalization_enabled()


def _should_force_naturalization(result) -> bool:
    if not _force_naturalization_enabled():
        return False
    if bool(result.metadata.get("grounding_required", False)):
        return False
    if result.intent in {"high_risk_medical", "out_of_scope"}:
        return False
    if result.mode in {"fallback_safe", "intake_followup"}:
        return False
    return bool(str(result.reply or "").strip())


def _trim_to_channel_limit(text: str, channel: str) -> str:
    limits = load_runtime_limits_config()
    channel_name = "wechat" if channel == "wechat" else "web"
    channel_limits = limits.channels.get(channel_name, {})
    max_chars = int(channel_limits.get("max_output_chars", 480) or 480)
    trim_suffix = str(limits.shared.get("trim_suffix", "…")).strip() or "…"
    normalized = str(text or "").strip()
    if channel_name == "web":
        max_chars = max(max_chars, 1400)
        if normalized.count("\n- ") >= 2:
            max_chars = max(max_chars, 4500)
        elif "](" in normalized:
            max_chars = max(max_chars, 1600)
    if len(normalized) <= max_chars:
        return normalized
    return smart_trim_to_limit(normalized, max_chars=max_chars, trim_suffix=trim_suffix)


def _trim_history_context(history_text: str) -> str:
    normalized = str(history_text or "").strip()
    if not normalized:
        return ""
    max_messages = get_runtime_config().protection.max_context_messages
    lines = [line for line in normalized.splitlines() if line.strip()]
    if len(lines) <= max_messages:
        return normalized
    return "\n".join(lines[-max_messages:])


def _normalize_reply_text(text: str) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    raw = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1", raw)
    raw = re.sub(r"https?://\S+", "", raw)
    raw = re.sub(r"[^\w\u3400-\u9fff]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _semantic_like_tokens(text: str) -> set[str]:
    normalized = _normalize_reply_text(text)
    if not normalized:
        return set()
    return set(re.findall(r"[\u3400-\u9fff]|[a-z0-9]+", normalized))


def _reply_similarity_metrics(left: str, right: str) -> tuple[float, float]:
    left_normalized = _normalize_reply_text(left)
    right_normalized = _normalize_reply_text(right)
    if not left_normalized or not right_normalized:
        return 0.0, 0.0

    sequence_ratio = SequenceMatcher(a=left_normalized, b=right_normalized).ratio()
    left_tokens = _semantic_like_tokens(left_normalized)
    right_tokens = _semantic_like_tokens(right_normalized)
    if not left_tokens or not right_tokens:
        return sequence_ratio, 0.0
    token_overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    return sequence_ratio, token_overlap


def _is_repetitive_reply(candidate: str, previous: str) -> bool:
    candidate_normalized = _normalize_reply_text(candidate)
    previous_normalized = _normalize_reply_text(previous)
    if not candidate_normalized or not previous_normalized:
        return False
    if candidate_normalized == previous_normalized:
        return True

    sequence_ratio, token_overlap = _reply_similarity_metrics(candidate_normalized, previous_normalized)
    return sequence_ratio >= _repeat_sequence_threshold() and token_overlap >= _repeat_token_threshold()


def _render_history_lines(messages: tuple[MemoryMessage, ...]) -> str:
    lines = []
    for message in messages:
        role_label = "User" if message.role == "user" else "Assistant"
        lines.append(f"[{role_label}] {message.content}")
    return "\n".join(lines).strip()


def _normalize_active_messages(
    history_messages: tuple[MemoryMessage, ...],
    *,
    latest_user_text: str,
) -> tuple[MemoryMessage, ...]:
    active: list[MemoryMessage] = []
    for message in history_messages + ((MemoryMessage(role="user", content=str(latest_user_text or "").strip(), timestamp=0.0),) if str(latest_user_text or "").strip() else ()):
        if message.role not in {"user", "assistant"}:
            continue
        content = str(message.content or "").strip()
        if not content:
            continue
        normalized_message = MemoryMessage(role=message.role, content=content, timestamp=message.timestamp)
        if active and active[-1].role == normalized_message.role:
            if active[-1].content == normalized_message.content:
                continue
            active[-1] = MemoryMessage(
                role=normalized_message.role,
                content=f"{active[-1].content}\n{normalized_message.content}",
                timestamp=normalized_message.timestamp,
            )
            continue
        active.append(normalized_message)

    max_messages = max(1, get_runtime_config().protection.max_context_messages)
    if len(active) > max_messages:
        active = active[-max_messages:]
    return tuple(active)


def _tool_final_personality_addendum(language: str) -> str:
    if language == "en":
        return (
            "[Conversation Style]\n"
            "- Keep the voice warm, natural, and human rather than templated.\n"
            "- Handle greetings, thanks, and harmless casual chat naturally.\n"
            "- Use recent user context when answering follow-ups.\n"
            "- Vary openings and sentence rhythm so consecutive replies do not sound cloned.\n"
            "- Explain first, recommend second when product suggestions are relevant."
        )
    return (
        "[Conversation Style]\n"
        "- 语气保持自然、温和、像真实对话，不要像固定客服模板。\n"
        "- 对问候、感谢、轻松闲聊先自然回应，再决定是否引回主题。\n"
        "- 回答追问时要承接最近上下文，不要像重新开新话题。\n"
        "- 尽量变化开头和句式，避免连续几轮像复读。\n"
        "- 涉及产品时先解释，再在合适时给建议。"
    )


def _tool_final_domain_addendum(language: str) -> str:
    if language == "en":
        return (
            "[Domain Logic]\n"
            "- Ingredient questions about a product should explain each ingredient with available fields before the product link.\n"
            "- Product links should stay selective and appear as clickable markdown links at the end when shown.\n"
            "- Summary memory can supplement the answer, but never override the latest user turn."
        )
    return (
        "[Domain Logic]\n"
        "- 用户问产品原料时，要先逐个解释原料，再放产品链接。\n"
        "- 展示链接时保持克制，并统一输出为可点击的 Markdown 链接，放在回复结尾。\n"
        "- 历史总结只能补充信息，不能覆盖最新一轮用户问题。"
    )


def _compose_tool_final_system_prompt(base_prompt: str, language: str) -> str:
    sections = [str(base_prompt or "").strip(), _tool_final_personality_addendum(language), _tool_final_domain_addendum(language)]
    return "\n\n".join(section for section in sections if section)


async def _maybe_naturalize_reply(
    *,
    user_id: str,
    user_text: str,
    result,
    channel: str,
    history_messages: tuple[MemoryMessage, ...],
) -> str:
    if bool(result.metadata.get("grounding_required", False)):
        return result.reply
    if not _naturalization_requested():
        return result.reply
    if not bool(result.metadata.get("allow_naturalization", False)) and not _should_force_naturalization(result):
        return result.reply

    runtime = get_prompt_runtime()
    try:
        system_prompt = _compose_tool_final_system_prompt(
            runtime.system_prompt("tool_final", language=result.language),
            result.language,
        )
        tool_result_json = json.dumps(
            {
                "intent": result.intent,
                "mode": result.mode,
                "language": result.language,
                "channel": channel,
                "direct_answer_first": True,
                "ask_at_most_one_followup": True,
                "draft_reply": result.reply,
                "recent_history": [
                    {
                        "role": message.role,
                        "content": message.content,
                    }
                    for message in history_messages[-6:]
                ],
                "summary_memory": str(result.metadata.get("summary_memory", "")).strip(),
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
        if looks_cut_mid_sentence(naturalized):
            naturalized_retry = await llm_chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                user_id=user_id,
                max_output_tokens=max(1200, get_runtime_config().llm.max_output_tokens),
            )
            if str(naturalized_retry or "").strip():
                naturalized = naturalized_retry
    except Exception as exc:
        logger.warning("Naturalization fallback triggered for intent=%s error=%s", result.intent, exc)
        return result.reply

    naturalized = enforce_domain_response_policy(naturalized, result.language, fallback_text=result.reply)
    return naturalized or result.reply


async def _finalize_reply(
    *,
    user_id: str,
    user_text: str,
    result,
    channel: str,
    reply_language: str,
    history_messages: tuple[MemoryMessage, ...],
    guardrail: GuardrailEngine,
) -> str:
    runtime = get_prompt_runtime()
    raw_reply = await _maybe_naturalize_reply(
        user_id=user_id,
        user_text=user_text,
        result=result,
        channel=channel,
        history_messages=history_messages,
    )
    is_valid, validation_reason = validate_final_reply(raw_reply, result)
    if not is_valid:
        logger.warning(
            "Reply validation fallback triggered for intent=%s reason=%s",
            result.intent,
            validation_reason,
        )
        if validation_reason == "unknown_url":
            raw_reply = _prefer_link_safe_reply(candidate=raw_reply, fallback=result.reply, result=result)
        else:
            raw_reply = result.reply
    raw_reply = enforce_domain_response_policy(raw_reply, reply_language, fallback_text=result.reply)
    reply = guardrail.sanitize_output(raw_reply)
    reply = _trim_to_channel_limit(reply, channel)
    if not reply:
        reply = runtime.guardrail_settings_for_language(reply_language).fallback_response
    return reply


def _prefer_link_safe_reply(*, candidate: str, fallback: str, result) -> str:
    sanitized_candidate = sanitize_reply_links(candidate, result)
    sanitized_fallback = sanitize_reply_links(fallback, result)
    if sanitized_candidate:
        if sanitized_fallback and len(sanitized_candidate) < max(24, len(sanitized_fallback) // 3):
            return sanitized_fallback
        return sanitized_candidate
    if sanitized_fallback:
        return sanitized_fallback
    return fallback


def _loop_recovery_reply(*, language: str, fallback_text: str) -> str:
    if language == "en":
        return (
            "Let me reset and answer from your latest message instead of repeating myself.\n\n"
            f"{fallback_text}"
        )
    return (
        "我换个角度，直接接你刚刚这句来答，避免重复上一条。\n\n"
        f"{fallback_text}"
    )


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
        previous_assistant = memory.last_message(user_id=admission.user_id, role="assistant")
        stored_messages = memory.recent_messages(user_id=admission.user_id)
        active_messages = _normalize_active_messages(stored_messages, latest_user_text=admission.normalized_text)
        history = _trim_history_context(_render_history_lines(active_messages))

        service = get_product_helper_service()
        result = sanitize_helper_result(
            service.handle(
                user_id=admission.user_id,
                text=admission.normalized_text,
                preferred_language=reply_language,
                channel=channel,
                history_text=history,
                history_messages=active_messages,
            )
        )

        reply = await _finalize_reply(
            user_id=admission.user_id,
            user_text=admission.normalized_text,
            result=result,
            channel=channel,
            reply_language=reply_language,
            history_messages=active_messages,
            guardrail=guardrail,
        )

        if previous_assistant and _is_repetitive_reply(reply, previous_assistant.content):
            logger.warning(
                "Repeat reply detected for user=%s seq_token=%s; rebuilding active memory window",
                admission.user_id,
                _reply_similarity_metrics(reply, previous_assistant.content),
            )
            repaired_messages = memory.rebuild_window(
                user_id=admission.user_id,
                keep_turns=2,
                drop_last_assistant=True,
            )
            retry_active_messages = _normalize_active_messages(repaired_messages, latest_user_text=admission.normalized_text)
            retry_history = _trim_history_context(_render_history_lines(retry_active_messages))
            retry_result = sanitize_helper_result(
                service.handle(
                    user_id=admission.user_id,
                    text=admission.normalized_text,
                    preferred_language=reply_language,
                    channel=channel,
                    history_text=retry_history,
                    history_messages=retry_active_messages,
                    loop_detected=True,
                )
            )
            reply = await _finalize_reply(
                user_id=admission.user_id,
                user_text=admission.normalized_text,
                result=retry_result,
                channel=channel,
                reply_language=reply_language,
                history_messages=retry_active_messages,
                guardrail=guardrail,
            )
            if _is_repetitive_reply(reply, previous_assistant.content):
                reply = _trim_to_channel_limit(
                    _loop_recovery_reply(language=reply_language, fallback_text=retry_result.reply),
                    channel,
                )

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
