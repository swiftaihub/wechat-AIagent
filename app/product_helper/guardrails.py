from __future__ import annotations

import re

from app.product_helper.config import load_commerce_guardrail_config


def _search_any(text: str, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def _contains_safe_boundary_language(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    safe_phrases = (
        "不等同于诊断",
        "不构成诊断",
        "不是诊断",
        "不能代替专业诊疗",
        "不能代替专业医疗",
        "not a diagnosis",
        "rather than a diagnosis",
        "not medical advice",
        "not a substitute for medical",
    )
    return any(phrase in normalized for phrase in safe_phrases)


def _localized_message(message: object, language: str) -> str:
    if isinstance(message, dict):
        selected = str(message.get(language, "") or message.get("zh", "") or message.get("en", "")).strip()
        return selected
    return str(message or "").strip()


def fallback_response(kind: str, language: str, default: str = "") -> str:
    config = load_commerce_guardrail_config()
    message = config.fallback_responses.get(kind, {})
    selected = _localized_message(message, language)
    return selected or default


def detect_high_risk_response(text: str, language: str) -> str | None:
    config = load_commerce_guardrail_config()
    normalized = str(text or "").strip().lower()
    for item in config.high_risk_patterns:
        patterns = tuple(str(pattern).strip() for pattern in item.get("patterns", []) if str(pattern).strip())
        if not patterns:
            continue
        if _search_any(normalized, patterns):
            message = _localized_message(item.get("message", {}), language)
            if message:
                if language == "zh" and "急性风险" not in message and item.get("id") == "chest_pain_breathing":
                    return f"你提到的情况有急性风险，{message}"
                if language == "en" and "acute risk" not in message.lower() and item.get("id") == "chest_pain_breathing":
                    return f"What you described can carry acute risk. {message}"
                return message
            return fallback_response("high_risk", language)
    return None


def collect_caution_notes(text: str, language: str) -> tuple[str, ...]:
    config = load_commerce_guardrail_config()
    normalized = str(text or "").strip().lower()
    notes: list[str] = []
    for item in config.caution_patterns:
        patterns = tuple(str(pattern).strip() for pattern in item.get("patterns", []) if str(pattern).strip())
        if not patterns:
            continue
        if _search_any(normalized, patterns):
            message = _localized_message(item.get("message", {}), language)
            if message:
                notes.append(message)
    return tuple(dict.fromkeys(notes))


def violates_domain_response_policy(text: str) -> bool:
    config = load_commerce_guardrail_config()
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if _contains_safe_boundary_language(normalized):
        return False
    blocked_patterns = (
        config.blocked_medical_diagnosis_patterns
        + config.blocked_treatment_promise_patterns
        + config.blocked_cure_patterns
        + config.blocked_replacement_of_care_patterns
    )
    return _search_any(normalized, blocked_patterns)


def enforce_domain_response_policy(text: str, language: str, *, fallback_text: str = "") -> str:
    if not violates_domain_response_policy(text):
        return str(text or "").strip()
    if str(fallback_text or "").strip():
        return str(fallback_text).strip()
    fallback = fallback_response("medical_boundary", language, default="")
    return str(fallback or "").strip()
