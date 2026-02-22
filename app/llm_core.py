import json
import logging
import os
import re
import time
from functools import lru_cache
from typing import Any

from app.guardrail import GuardrailEngine
from app.memory_store import get_memory_store
from app.ollama_client import ollama_chat
from app.prompt_runtime import PromptRuntime, get_prompt_runtime
from app.tools.executor import execute_tool_call
from app.tools.registry import get_tool_specs_for_prompt

logger = logging.getLogger(__name__)

URL_PATTERN = re.compile(r"https?://[^\s\])>]+")
PHONE_PATTERN = re.compile(r"\+?\d[\d\-\s]{6,}\d")
ADDRESS_PATTERN = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9][A-Za-z0-9\s,.-]{2,80}"
    r"(street|st|road|rd|avenue|ave|boulevard|blvd|lane|ln|drive|dr|way|court|ct)\b",
    re.IGNORECASE,
)
ADDRESS_PATTERN_CN = re.compile(
    r"(?:中国)?[^\n]{0,16}(?:省|市|自治区|自治州|地区)[^\n]{0,24}(?:区|县|镇|街道)[^\n]{0,30}(?:路|街|巷|号)",
)
DISALLOWED_LIFESTYLE_ADVICE_KEYWORDS = (
    "\u8c03\u6574\u4f5c\u606f\u65f6\u95f4",
    "\u996e\u98df\u8c03\u7406",
    "\u9002\u91cf\u8fd0\u52a8",
    "\u4fdd\u6301\u826f\u597d\u60c5\u7eea\u72b6\u6001",
    "\u4f5c\u606f\u5efa\u8bae",
    "\u996e\u98df\u5efa\u8bae",
    "\u8fd0\u52a8\u5efa\u8bae",
    "\u60c5\u7eea\u5efa\u8bae",
)
DAILY_SECTION_TITLE = "\u3010\u65e5\u5e38\u8c03\u517b\u5efa\u8bae\u3011"
HERBAL_SECTION_TITLE = "\u3010\u4e2d\u836f\u517b\u751f\u5efa\u8bae\u3011"
SYMPTOM_SECTION_TITLE = "\u3010\u5bf9\u5e94\u75c7\u72b6\u3011"
OUT_OF_SCOPE_KEYWORDS = (
    "python",
    "java",
    "javascript",
    "typescript",
    "c++",
    "coding",
    "code",
    "sql",
    "docker",
    "kubernetes",
    "linux",
    "git",
    "bug",
    "fastapi",
    "编程",
    "代码",
    "算法",
    "调试",
    "报错",
    "数据库",
    "前端",
    "后端",
)
WELLNESS_HINT_KEYWORDS = (
    "中医",
    "养生",
    "中药",
    "体质",
    "调养",
    "祛痘",
    "便秘",
    "失眠",
    "上火",
    "口苦",
    "出油",
    "疲劳",
    "气虚",
    "阳虚",
    "阴虚",
    "痰湿",
    "湿热",
    "气滞",
    "血瘀",
    "气血两虚",
)
META_CHAT_KEYWORDS = (
    "你是谁",
    "你是",
    "你会",
    "你能做什么",
    "你可以做什么",
    "你是否",
    "专业知识",
    "专业吗",
    "靠谱吗",
    "有资质",
    "你擅长",
)


@lru_cache(maxsize=1)
def _get_guardrail_engine() -> GuardrailEngine:
    runtime = get_prompt_runtime()
    return GuardrailEngine(runtime.guardrail_settings)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _default_profile() -> str:
    return os.getenv("PROMPT_PROFILE", "wechat").strip() or "wechat"


def _planner_profile() -> str:
    return (
        os.getenv("TOOL_CALL_PLANNER_PROFILE", "wechat_tool_planner").strip()
        or "wechat_tool_planner"
    )


def _final_profile() -> str:
    return os.getenv("TOOL_CALL_FINAL_PROFILE", "wechat_tool_final").strip() or "wechat_tool_final"


def _tool_calling_enabled() -> bool:
    return _env_bool("TOOL_CALLING_ENABLED", True)


def _tool_confidence_threshold() -> float:
    return max(0.0, min(1.0, _env_float("TOOL_CALL_CONFIDENCE_THRESHOLD", 0.55)))


def _log_text_preview_chars() -> int:
    return max(24, _env_int("TOOL_LOG_TEXT_PREVIEW_CHARS", 80))


def _log_json_preview_chars() -> int:
    return max(60, _env_int("TOOL_LOG_JSON_PREVIEW_CHARS", 300))


def _preview_text(text: str, max_chars: int) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars]}..."


def _normalize_intent_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower()).strip()


def _contains_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords if keyword)


def _classify_none_tool_intent(user_text: str) -> str:
    normalized = _normalize_intent_text(user_text)
    if not normalized:
        return "meta_chat"

    has_off_topic = _contains_any_keyword(normalized, OUT_OF_SCOPE_KEYWORDS)
    has_wellness_hint = _contains_any_keyword(normalized, WELLNESS_HINT_KEYWORDS)
    has_meta_hint = _contains_any_keyword(normalized, META_CHAT_KEYWORDS)

    if has_off_topic and not has_wellness_hint:
        return "out_of_scope"
    if has_meta_hint:
        return "meta_chat"
    if has_wellness_hint:
        return "wellness_related"
    return "meta_chat"


def _build_out_of_scope_reply() -> str:
    return (
        "我目前仅提供中医养生与中药调养相关信息，暂不提供编程或其他非养生领域的解答。"
        "如果你愿意，我可以根据你的年龄、性别、睡眠、饮食、排便、情绪、运动和近期不适，"
        "给出中药养生建议。"
    )


def _build_meta_chat_reply() -> str:
    return (
        "我具备中医养生与中药调养方面的知识整理能力，可提供一般性养生建议，"
        "但不能替代医生诊疗。你可以告诉我年龄、性别、睡眠、饮食、排便、情绪、运动和近期不适，"
        "我会给你更贴合的中药养生建议。"
    )


def _normalize_reply_whitespace(text: str) -> str:
    lines = [(line or "").rstrip() for line in (text or "").splitlines()]
    result: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        result.append(line)
        previous_blank = is_blank
    return "\n".join(result).strip()


def _contains_disallowed_lifestyle_advice(line: str) -> bool:
    raw = (line or "").strip()
    if not raw:
        return False
    compact = re.sub(r"[\s`*_>#\-\d\.\)\(:：]+", "", raw)
    return any(keyword in compact for keyword in DISALLOWED_LIFESTYLE_ADVICE_KEYWORDS)


def _strip_lifestyle_advice_sections(text: str) -> str:
    lines = (text or "").splitlines()
    if not lines:
        return ""

    filtered: list[str] = []
    for line in lines:
        if _contains_disallowed_lifestyle_advice(line.strip()):
            continue
        filtered.append(line)

    return _normalize_reply_whitespace("\n".join(filtered))


def _enforce_herbal_only_reply(text: str) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return ""

    normalized = normalized.replace(DAILY_SECTION_TITLE, HERBAL_SECTION_TITLE)
    normalized = _strip_lifestyle_advice_sections(normalized)
    return _normalize_reply_whitespace(normalized)


def _collect_symptom_candidates(tool_result: dict[str, Any]) -> list[str]:
    if not isinstance(tool_result, dict):
        return []

    rows = tool_result.get("herbal_recommendations", [])
    if not isinstance(rows, list):
        return []

    symptoms: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_symptoms = row.get("symptoms", [])
        if not isinstance(row_symptoms, list):
            continue
        for symptom in row_symptoms:
            text = str(symptom).strip()
            if text:
                symptoms.append(text)

    return list(dict.fromkeys(symptoms))


def _ensure_symptom_section(output_text: str, tool_result: dict[str, Any]) -> str:
    text = (output_text or "").strip()
    if not text:
        return text
    if not isinstance(tool_result, dict):
        return text
    if str(tool_result.get("tool", "")).strip() == "none":
        return text
    if SYMPTOM_SECTION_TITLE in text:
        return text

    symptoms = _collect_symptom_candidates(tool_result)
    if not symptoms:
        return text

    symptom_line = "、".join(symptoms[:4])
    section_block = f"{SYMPTOM_SECTION_TITLE}\n- {symptom_line}"

    insert_targets = (HERBAL_SECTION_TITLE, DAILY_SECTION_TITLE)
    for marker in insert_targets:
        marker_index = text.find(marker)
        if marker_index >= 0:
            prefix = text[:marker_index].rstrip()
            suffix = text[marker_index:].lstrip()
            merged = f"{prefix}\n\n{section_block}\n\n{suffix}"
            return _normalize_reply_whitespace(merged)

    return _normalize_reply_whitespace(f"{text}\n\n{section_block}")

def _to_phone_key(value: str) -> str:
    return re.sub(r"[^\d+]", "", value or "")


def _normalize_address(value: str) -> str:
    return re.sub(r"[\s,.-]+", " ", (value or "").lower()).strip()


def _collect_allowed_handoff_values(tool_result: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    allowed_urls: set[str] = set()
    allowed_phones: set[str] = set()
    allowed_addresses: set[str] = set()

    matched_items = tool_result.get("matched_items", [])
    if not isinstance(matched_items, list):
        return allowed_urls, allowed_phones, allowed_addresses

    for item in matched_items:
        if not isinstance(item, dict):
            continue
        handoffs = item.get("handoffs", [])
        if not isinstance(handoffs, list):
            continue
        for handoff in handoffs:
            if not isinstance(handoff, dict):
                continue
            url = str(handoff.get("url", "")).strip()
            if url:
                allowed_urls.add(url.rstrip(".,;"))
            phone = str(handoff.get("phone", "")).strip()
            if phone:
                allowed_phones.add(_to_phone_key(phone))
            address = str(handoff.get("address", "")).strip()
            if address:
                allowed_addresses.add(_normalize_address(address))

    return allowed_urls, allowed_phones, allowed_addresses


def _contains_unapproved_handoff(output_text: str, tool_result: dict[str, Any]) -> bool:
    allowed_urls, allowed_phones, allowed_addresses = _collect_allowed_handoff_values(tool_result)
    normalized_output = (output_text or "").strip()

    for url in URL_PATTERN.findall(normalized_output):
        normalized_url = url.rstrip(".,;")
        if normalized_url and normalized_url not in allowed_urls:
            return True

    for phone in PHONE_PATTERN.findall(normalized_output):
        phone_key = _to_phone_key(phone)
        if phone_key and phone_key not in allowed_phones:
            return True

    for pattern in (ADDRESS_PATTERN, ADDRESS_PATTERN_CN):
        for candidate in pattern.finditer(normalized_output):
            candidate_text = candidate.group(0)
            candidate_norm = _normalize_address(candidate_text)
            if not candidate_norm:
                continue
            is_allowed = any(
                candidate_norm in allowed_address or allowed_address in candidate_norm
                for allowed_address in allowed_addresses
            )
            if not is_allowed:
                return True

    return False


def _deduplicate_appendix_occurrences(output_text: str, appendix: str) -> str:
    normalized_output = (output_text or "").strip()
    normalized_appendix = (appendix or "").strip()
    if not normalized_output or not normalized_appendix:
        return normalized_output

    def _compact_line(line: str) -> str:
        return re.sub(r"\s+", "", (line or "").strip()).lower()

    appendix_lines = [
        _compact_line(line)
        for line in normalized_appendix.splitlines()
        if _compact_line(line)
    ]
    appendix_line_set = set(appendix_lines)

    base = normalized_output.replace(normalized_appendix, "").strip()
    if appendix_line_set:
        filtered_lines: list[str] = []
        for line in base.splitlines():
            compact = _compact_line(line)
            if compact and compact in appendix_line_set:
                continue
            filtered_lines.append(line)
        base = _normalize_reply_whitespace("\n".join(filtered_lines))

    if not base:
        return normalized_appendix
    return _normalize_reply_whitespace(f"{base}\n\n{normalized_appendix}")


def _ensure_required_appendix(output_text: str, tool_result: dict[str, Any]) -> str:
    if not isinstance(tool_result, dict):
        return output_text
    if not bool(tool_result.get("requires_company_append", False)):
        return output_text

    appendix = str(tool_result.get("required_append_text", "")).strip()
    if not appendix:
        return output_text

    normalized_output = (output_text or "").strip()
    if not normalized_output:
        return appendix

    if appendix in normalized_output:
        return _deduplicate_appendix_occurrences(normalized_output, appendix)
    return f"{normalized_output}\n\n{appendix}"


def _format_handoff_line(handoff: dict[str, Any]) -> str:
    handoff_type = str(handoff.get("type", "")).strip()
    label = str(handoff.get("label", "")).strip() or "handoff"

    if handoff_type in {"questionnaire", "link"}:
        url = str(handoff.get("url", "")).strip()
        return f"{label}: {url}".strip()
    if handoff_type == "address":
        address = str(handoff.get("address", "")).strip()
        return f"{label}: {address}".strip()
    if handoff_type == "contact":
        phone = str(handoff.get("phone", "")).strip()
        email = str(handoff.get("email", "")).strip()
        segments = []
        if phone:
            segments.append(f"phone {phone}")
        if email:
            segments.append(f"email {email}")
        tail = " / ".join(segments)
        return f"{label}: {tail}".strip()

    return label


def render_tool_result_fallback_reply(tool_result: dict[str, Any]) -> str:
    matched_items = tool_result.get("matched_items", [])
    if not isinstance(matched_items, list) or not matched_items:
        return (
            "I could not match your request to a specific advice item yet. "
            "Please provide more detail, such as your goal, duration, and any prior evaluation."
        )

    lines: list[str] = []
    for item in matched_items:
        if not isinstance(item, dict):
            continue

        title = str(item.get("title", "")).strip() or "Advice"
        advice = str(item.get("advice", "")).strip()
        handoffs = item.get("handoffs", [])
        followups = item.get("followup_questions", [])
        safety = item.get("safety", {})

        lines.append(f"[{title}]")
        if advice:
            lines.append(advice)

        if isinstance(handoffs, list) and handoffs:
            lines.append("Available handoff info:")
            for handoff in handoffs:
                if isinstance(handoff, dict):
                    lines.append(f"- {_format_handoff_line(handoff)}")

        if isinstance(followups, list) and followups:
            lines.append("Suggested follow-up details:")
            for question in followups[:3]:
                question_text = str(question).strip()
                if question_text:
                    lines.append(f"- {question_text}")

        disclaimer = ""
        if isinstance(safety, dict):
            disclaimer = str(safety.get("disclaimer", "")).strip()
        if disclaimer:
            lines.append(f"Note: {disclaimer}")

        lines.append("")

    base = "\n".join(lines).strip()
    return _ensure_required_appendix(base, tool_result)


def parse_tool_call_json(call_json_text: str) -> dict[str, Any]:
    raw_text = (call_json_text or "").strip()
    if not raw_text:
        return {
            "tool": "none",
            "arguments": {},
            "confidence": 0.0,
            "reason": "empty_planner_output",
            "parse_error": "empty_response",
        }

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        payload = None

    if payload is None:
        extracted = _extract_first_json_object(raw_text)
        if extracted:
            try:
                payload = json.loads(extracted)
            except json.JSONDecodeError:
                payload = None

    if payload is None:
        return {
            "tool": "none",
            "arguments": {},
            "confidence": 0.0,
            "reason": "invalid_planner_json",
            "parse_error": "json_decode_error",
        }

    if not isinstance(payload, dict):
        return {
            "tool": "none",
            "arguments": {},
            "confidence": 0.0,
            "reason": "planner_json_not_object",
            "parse_error": "json_not_object",
        }

    tool_name = str(payload.get("tool", "none") or "none").strip() or "none"
    arguments = payload.get("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reason = str(payload.get("reason", "")).strip()
    parsed = {
        "tool": tool_name,
        "arguments": arguments,
        "confidence": confidence,
        "reason": reason,
    }

    if confidence < _tool_confidence_threshold():
        parsed["tool"] = "none"
        parsed["arguments"] = {}
        parsed["forced_none"] = True
        if not parsed["reason"]:
            parsed["reason"] = "confidence_below_threshold"

    if parsed["tool"] == "none":
        parsed["arguments"] = {}

    return parsed


def _extract_first_json_object(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""

    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()

    start_index = raw.find("{")
    if start_index < 0:
        return ""

    in_string = False
    escaped = False
    depth = 0

    for index in range(start_index, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return raw[start_index : index + 1]

    return ""


async def _generate_direct_reply(
    *,
    runtime: PromptRuntime,
    guardrail: GuardrailEngine,
    user_id: str,
    user_text: str,
    context: dict[str, Any],
) -> str:
    none_tool_intent = _classify_none_tool_intent(user_text)
    normalized_intent_text = _normalize_intent_text(user_text)
    if none_tool_intent == "out_of_scope":
        return guardrail.sanitize_output(_build_out_of_scope_reply())
    if none_tool_intent == "meta_chat" and _contains_any_keyword(
        normalized_intent_text,
        META_CHAT_KEYWORDS,
    ):
        return guardrail.sanitize_output(_build_meta_chat_reply())

    profile = _default_profile()
    system_prompt = runtime.system_prompt(profile)
    user_prompt = runtime.render_user_prompt(
        profile=profile,
        user_text=user_text,
        user_id=user_id,
        context=context,
    )
    raw_output = await ollama_chat(system_prompt=system_prompt, user_prompt=user_prompt)
    raw_output = _enforce_herbal_only_reply(raw_output)
    return guardrail.sanitize_output(raw_output)


async def _generate_with_tool_calling(
    *,
    runtime: PromptRuntime,
    guardrail: GuardrailEngine,
    user_id: str,
    user_text: str,
    context: dict[str, Any],
) -> str:
    planner_profile = _planner_profile()
    final_profile = _final_profile()

    planner_started = time.perf_counter()
    planner_system_prompt = runtime.system_prompt(planner_profile)
    planner_user_prompt = runtime.render_user_prompt(
        profile=planner_profile,
        user_text=user_text,
        user_id=user_id,
        context=context,
        extra_variables={
            "tools_json": json.dumps(get_tool_specs_for_prompt(), ensure_ascii=False),
        },
    )
    call_json_text = await ollama_chat(
        system_prompt=planner_system_prompt,
        user_prompt=planner_user_prompt,
        response_format="json",
    )
    planner_elapsed_ms = (time.perf_counter() - planner_started) * 1000.0

    tool_call = parse_tool_call_json(call_json_text)
    logger.info(
        "Tool planner result user=%s tool=%s confidence=%.2f reason=%s planner_ms=%.1f "
        "input_preview=%s raw_preview=%s",
        user_id,
        tool_call.get("tool", "none"),
        float(tool_call.get("confidence", 0.0)),
        _preview_text(str(tool_call.get("reason", "")), 80),
        planner_elapsed_ms,
        _preview_text(user_text, _log_text_preview_chars()),
        _preview_text(call_json_text, _log_json_preview_chars()),
    )

    if "parse_error" in tool_call:
        raise ValueError(f"planner_parse_failed:{tool_call['parse_error']}")

    tool_started = time.perf_counter()
    if tool_call.get("tool") == "none":
        none_tool_intent = _classify_none_tool_intent(user_text)
        if none_tool_intent == "out_of_scope":
            logger.info("Tool planner none-intent=out_of_scope user=%s", user_id)
            return guardrail.sanitize_output(_build_out_of_scope_reply())

        tool_result = {
            "ok": True,
            "tool": "none",
            "matched_items": [],
            "reasons": [{"kind": "intent", "detail": none_tool_intent}],
            "intent": none_tool_intent,
            "interaction_policy": (
                "answer_then_redirect_to_herbal"
                if none_tool_intent == "meta_chat"
                else "wellness_default"
            ),
        }
    else:
        tool_result = execute_tool_call(tool_call, context)
    tool_elapsed_ms = (time.perf_counter() - tool_started) * 1000.0

    matched_ids: list[str] = []
    matched_items = tool_result.get("matched_items", [])
    if isinstance(matched_items, list):
        for item in matched_items:
            if isinstance(item, dict):
                item_id = str(item.get("id", "")).strip()
                if item_id:
                    matched_ids.append(item_id)

    logger.info(
        "Tool execution result tool=%s ok=%s matched_ids=%s tool_ms=%.1f",
        tool_result.get("tool", "none"),
        bool(tool_result.get("ok", False)),
        ",".join(matched_ids) or "-",
        tool_elapsed_ms,
    )

    if not tool_result.get("ok", False):
        raise RuntimeError(f"tool_execution_failed:{tool_result.get('error', 'unknown_error')}")

    final_started = time.perf_counter()
    final_system_prompt = runtime.system_prompt(final_profile)
    final_user_prompt = runtime.render_user_prompt(
        profile=final_profile,
        user_text=user_text,
        user_id=user_id,
        context=context,
        extra_variables={
            "tool_call_json": json.dumps(tool_call, ensure_ascii=False),
            "tool_result_json": json.dumps(tool_result, ensure_ascii=False),
        },
    )
    final_output = await ollama_chat(
        system_prompt=final_system_prompt,
        user_prompt=final_user_prompt,
    )
    final_output = _ensure_required_appendix(final_output, tool_result)
    final_elapsed_ms = (time.perf_counter() - final_started) * 1000.0

    if _contains_unapproved_handoff(final_output, tool_result):
        logger.warning(
            "Final output contains unapproved handoff values user=%s; using deterministic fallback",
            user_id,
        )
        final_output = render_tool_result_fallback_reply(tool_result)

    final_output = _ensure_symptom_section(final_output, tool_result)
    logger.info("Tool final generation user=%s final_ms=%.1f", user_id, final_elapsed_ms)
    final_output = _enforce_herbal_only_reply(final_output)
    return guardrail.sanitize_output(final_output)


async def generate_reply(user_id: str, text: str) -> str:
    runtime = get_prompt_runtime()
    guardrail = _get_guardrail_engine()
    memory_store = get_memory_store()

    input_result = guardrail.check_input(text)
    if input_result.blocked:
        return input_result.text

    clean_user_text = input_result.text
    recent_history = memory_store.render_history_block(user_id=user_id)
    context = {
        "channel": "wechat_mp",
        "user_id": user_id,
        "tool_calling_enabled": str(_tool_calling_enabled()).lower(),
        "user_text_preview": _preview_text(clean_user_text, _log_text_preview_chars()),
        "recent_history": recent_history or "-",
    }

    reply_text = ""
    if not _tool_calling_enabled():
        reply_text = await _generate_direct_reply(
            runtime=runtime,
            guardrail=guardrail,
            user_id=user_id,
            user_text=clean_user_text,
            context=context,
        )
        memory_store.add_exchange(
            user_id=user_id,
            user_text=clean_user_text,
            assistant_text=reply_text,
        )
        return reply_text

    try:
        reply_text = await _generate_with_tool_calling(
            runtime=runtime,
            guardrail=guardrail,
            user_id=user_id,
            user_text=clean_user_text,
            context={**context, "user_text": clean_user_text},
        )
    except Exception as exc:
        logger.warning("Tool-calling pipeline failed, fallback to direct reply: %s", exc)
        reply_text = await _generate_direct_reply(
            runtime=runtime,
            guardrail=guardrail,
            user_id=user_id,
            user_text=clean_user_text,
            context=context,
        )

    memory_store.add_exchange(
        user_id=user_id,
        user_text=clean_user_text,
        assistant_text=reply_text,
    )
    return reply_text
