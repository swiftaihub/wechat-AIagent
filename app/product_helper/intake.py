from __future__ import annotations

import json
import re
from typing import Any

from app.i18n import normalize_language, resolve_localized_text
from app.product_helper.config import QuestionnaireConfig


JSON_BLOCK_PATTERN = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def merge_intake(base: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(base or {})
    for key, value in (extra or {}).items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            existing = merged.get(key, [])
            if not isinstance(existing, list):
                existing = []
            combined = existing + [item for item in value if item not in existing]
            merged[key] = combined
        elif isinstance(value, tuple):
            existing = merged.get(key, [])
            if not isinstance(existing, list):
                existing = []
            combined = existing + [item for item in value if item not in existing]
            merged[key] = combined
        else:
            merged[key] = value
    return merged


def _normalize_match_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def intake_field_is_visible(field: dict[str, Any], intake_state: dict[str, Any] | None) -> bool:
    field_name = str(field.get("name", "")).strip()
    if not field_name:
        return False
    if field_name == "use_case":
        return True

    state = intake_state or {}
    show_if = field.get("show_if", {})
    if not isinstance(show_if, dict) or not show_if:
        return bool(str(state.get("use_case", "")).strip())

    for dependency_name, expected_value in show_if.items():
        expected_values = set(_normalize_match_values(expected_value))
        current_value = state.get(dependency_name)
        current_values = set(_normalize_match_values(current_value))
        if not expected_values or not current_values or not (current_values & expected_values):
            return False
    return True


def visible_intake_fields(
    intake_fields: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    intake_state: dict[str, Any] | None,
) -> tuple[dict[str, Any], ...]:
    return tuple(
        field
        for field in intake_fields
        if isinstance(field, dict) and intake_field_is_visible(field, intake_state)
    )


def _append_recent_discomfort_combined(payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload)
    recent_parts: list[str] = []
    for key in ("recent_discomfort_multi", "free_text_recent_discomfort"):
        value = merged.get(key)
        if isinstance(value, list):
            for item in value:
                trimmed = str(item or "").strip()
                if trimmed and trimmed not in recent_parts:
                    recent_parts.append(trimmed)
        elif isinstance(value, str):
            trimmed = value.strip()
            if trimmed and trimmed not in recent_parts:
                recent_parts.append(trimmed)
    if recent_parts:
        merged["recent_discomfort_combined"] = recent_parts
    else:
        merged.pop("recent_discomfort_combined", None)
    return merged


def build_visible_intake_payload(
    intake_state: dict[str, Any],
    intake_fields: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in visible_intake_fields(intake_fields, intake_state):
        name = str(field.get("name", "")).strip()
        if not name:
            continue
        value = intake_state.get(name)
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            if items:
                payload[name] = items
        elif isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                payload[name] = trimmed
        elif value not in (None, "", []):
            payload[name] = value
    return _append_recent_discomfort_combined(payload)


def prune_intake_to_visible(intake_state: dict[str, Any], questionnaire: QuestionnaireConfig) -> dict[str, Any]:
    pruned: dict[str, Any] = {}
    for field in visible_intake_fields(questionnaire.fields, intake_state):
        name = str(field.get("name", "")).strip()
        if not name or name not in intake_state:
            continue
        pruned[name] = intake_state[name]
    return _append_recent_discomfort_combined(pruned)


def parse_intake_json_from_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}

    matched = JSON_BLOCK_PATTERN.search(raw)
    if matched:
        try:
            parsed = json.loads(matched.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}

    lowered = raw.lower()
    if any(hint in lowered for hint in ("{\"age_", "{\"use_case", "\"recent_discomfort_multi\"")):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def extract_intake_from_text(text: str, questionnaire: QuestionnaireConfig) -> dict[str, Any]:
    parsed = parse_intake_json_from_text(text)
    if parsed:
        return parsed

    normalized_text = str(text or "").strip().lower()
    intake: dict[str, Any] = {}
    for field in questionnaire.fields:
        field_name = field["name"]
        field_type = field["type"]
        matched_values: list[str] = []
        for option in field.get("options", []):
            label_values = [
                resolve_localized_text(option.get("label", {}), "zh").lower(),
                resolve_localized_text(option.get("label", {}), "en").lower(),
                str(option.get("value", "")).lower(),
            ]
            if any(label and label in normalized_text for label in label_values):
                matched_values.append(str(option.get("value", "")).strip())

        if field_type == "multi" and matched_values:
            intake[field_name] = tuple(dict.fromkeys(matched_values))
        elif field_type == "single" and matched_values:
            intake[field_name] = matched_values[0]

    if any(term in normalized_text for term in ("gift", "送礼", "礼物", "送给", "送妈妈", "送女生", "送女朋友", "送闺蜜", "for her", "girlfriend", "mom")):
        intake.setdefault("use_case", "gifting")
    if "article" in normalized_text or "read" in normalized_text or "文章" in normalized_text:
        intake.setdefault("use_case", "article_recommendation")
    return intake


def normalize_intake(intake: dict[str, Any], questionnaire: QuestionnaireConfig) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    field_names = {field["name"]: field for field in questionnaire.fields}

    for key, value in (intake or {}).items():
        field = field_names.get(key)
        if field is None:
            continue
        if field["type"] == "multi":
            if isinstance(value, (list, tuple, set)):
                normalized[key] = [str(item).strip() for item in value if str(item).strip()]
            elif value:
                normalized[key] = [str(value).strip()]
            else:
                normalized[key] = []
        else:
            normalized[key] = str(value or "").strip()
    return prune_intake_to_visible(normalized, questionnaire)


def next_followup_question(
    *,
    questionnaire: QuestionnaireConfig,
    intake: dict[str, Any],
    intent: str,
    language: str,
) -> str:
    lang = normalize_language(language)
    candidates: list[tuple[int, str]] = []
    for field in visible_intake_fields(questionnaire.fields, intake):
        field_name = field["name"]
        value = intake.get(field_name)
        if value not in ("", [], None):
            continue
        importance = field.get("importance", {})
        weight = int(importance.get(intent, importance.get("default", 0)) or 0)
        if weight <= 0:
            continue

        options = field.get("options", [])
        if options:
            option_labels = [
                resolve_localized_text(option["label"], lang, fallback=str(option["value"]))
                for option in options[:3]
            ]
            if lang == "en":
                prompt = f"To narrow it down a bit more, which feels closer: {', '.join(option_labels)}?"
            else:
                prompt = f"如果只补一个信息，你会更接近：{'、'.join(option_labels)}？"
        else:
            label = resolve_localized_text(field["label"], lang, fallback=field_name)
            if lang == "en":
                prompt = f"One quick detail that would help: {label}."
            else:
                prompt = f"我先补一个关键信息会更准一些：{label}。"
        candidates.append((weight, prompt))

    if not candidates:
        if lang == "en":
            return "Would you like a quick recommendation, or a slightly more tailored suggestion?"
        return "你更想要快速推荐，还是希望我按你的情况再细一点来选？"

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]
