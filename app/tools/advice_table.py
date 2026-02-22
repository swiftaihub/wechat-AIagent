import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ALLOWED_HANDOFF_TYPES = {"questionnaire", "address", "contact", "link"}


@dataclass(frozen=True)
class AdviceTable:
    source_path: Path
    version: int
    items: tuple[dict[str, Any], ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _resolve_path(path_value: str) -> Path:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (_repo_root() / candidate).resolve()


def _resolve_advice_table_path() -> Path:
    from_env = os.getenv("ADVICE_TABLE_PATH", "").strip()
    if from_env:
        env_path = _resolve_path(from_env)
        if not env_path.exists():
            raise FileNotFoundError(f"ADVICE_TABLE_PATH file not found: {env_path}")
        return env_path

    private_path = _resolve_path("config/advice_table.private.yaml")
    if private_path.exists():
        return private_path

    example_path = _resolve_path(
        os.getenv("ADVICE_TABLE_EXAMPLE_PATH", "config/advice_table.example.yaml")
    )
    if example_path.exists():
        return example_path

    raise FileNotFoundError(
        "No advice table found. Set ADVICE_TABLE_PATH or create config/advice_table.private.yaml."
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"Advice table root must be a mapping: {path}")
    return raw


def _as_non_empty_string(value: Any, field_name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"Advice item field '{field_name}' cannot be empty.")
    return result


def _normalize_list_of_strings(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"Advice item field '{field_name}' must be a list.")
    normalized = [str(item).strip() for item in value if str(item).strip()]
    if not normalized:
        raise ValueError(f"Advice item field '{field_name}' must include at least one string.")
    return normalized


def _normalize_handoffs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("Advice item field 'handoffs' must be a list.")

    handoffs: list[dict[str, str]] = []
    for idx, handoff in enumerate(value):
        if not isinstance(handoff, dict):
            raise ValueError(f"handoffs[{idx}] must be a mapping.")

        handoff_type = _as_non_empty_string(handoff.get("type"), f"handoffs[{idx}].type")
        if handoff_type not in ALLOWED_HANDOFF_TYPES:
            raise ValueError(
                f"handoffs[{idx}].type must be one of {sorted(ALLOWED_HANDOFF_TYPES)}."
            )

        label = _as_non_empty_string(handoff.get("label"), f"handoffs[{idx}].label")
        normalized: dict[str, str] = {"type": handoff_type, "label": label}

        if handoff_type in {"questionnaire", "link"}:
            normalized["url"] = _as_non_empty_string(handoff.get("url"), f"handoffs[{idx}].url")
        elif handoff_type == "address":
            normalized["address"] = _as_non_empty_string(
                handoff.get("address"),
                f"handoffs[{idx}].address",
            )
        elif handoff_type == "contact":
            phone = str(handoff.get("phone", "")).strip()
            email = str(handoff.get("email", "")).strip()
            if not phone and not email:
                raise ValueError(
                    f"handoffs[{idx}] contact must include at least one of phone/email."
                )
            if phone:
                normalized["phone"] = phone
            if email:
                normalized["email"] = email

        handoffs.append(normalized)

    return handoffs


def _normalize_triggers(value: Any) -> list[dict[str, list[str]]]:
    if not isinstance(value, list):
        raise ValueError("Advice item field 'triggers' must be a list.")
    if not value:
        raise ValueError("Advice item field 'triggers' must not be empty.")

    normalized_triggers: list[dict[str, list[str]]] = []
    for idx, trigger in enumerate(value):
        if not isinstance(trigger, dict):
            raise ValueError(f"triggers[{idx}] must be a mapping.")

        normalized_trigger: dict[str, list[str]] = {}
        if "any" in trigger:
            normalized_trigger["any"] = _normalize_list_of_strings(
                trigger["any"],
                f"triggers[{idx}].any",
            )
        if "all" in trigger:
            normalized_trigger["all"] = _normalize_list_of_strings(
                trigger["all"],
                f"triggers[{idx}].all",
            )

        if not normalized_trigger:
            raise ValueError(f"triggers[{idx}] must include at least one of 'any'/'all'.")

        normalized_triggers.append(normalized_trigger)

    return normalized_triggers


def _normalize_item(raw_item: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw_item, dict):
        raise ValueError(f"items[{index}] must be a mapping.")

    normalized = {
        "id": _as_non_empty_string(raw_item.get("id"), f"items[{index}].id"),
        "title": _as_non_empty_string(raw_item.get("title"), f"items[{index}].title"),
        "keywords": _normalize_list_of_strings(raw_item.get("keywords"), f"items[{index}].keywords"),
        "triggers": _normalize_triggers(raw_item.get("triggers")),
        "advice": _as_non_empty_string(raw_item.get("advice"), f"items[{index}].advice"),
        "handoffs": _normalize_handoffs(raw_item.get("handoffs")),
    }

    followup_questions = raw_item.get("followup_questions", [])
    if followup_questions:
        normalized["followup_questions"] = _normalize_list_of_strings(
            followup_questions,
            f"items[{index}].followup_questions",
        )
    else:
        normalized["followup_questions"] = []

    safety = raw_item.get("safety", {})
    if safety:
        if not isinstance(safety, dict):
            raise ValueError(f"items[{index}].safety must be a mapping.")
        disclaimer = str(safety.get("disclaimer", "")).strip()
        if disclaimer:
            normalized["safety"] = {"disclaimer": disclaimer}
        else:
            normalized["safety"] = {}
    else:
        normalized["safety"] = {}

    return normalized


@lru_cache(maxsize=1)
def load_advice_table() -> AdviceTable:
    source_path = _resolve_advice_table_path()
    raw = _read_yaml(source_path)

    version = int(raw.get("version", 1))
    if version <= 0:
        raise ValueError("advice_table.version must be a positive integer.")

    raw_items = raw.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("Advice table must include 'items' as a list.")

    normalized_items = tuple(_normalize_item(item, idx) for idx, item in enumerate(raw_items))

    seen_ids: set[str] = set()
    for item in normalized_items:
        item_id = item["id"]
        if item_id in seen_ids:
            raise ValueError(f"Duplicate advice item id detected: {item_id}")
        seen_ids.add(item_id)

    return AdviceTable(source_path=source_path, version=version, items=normalized_items)


def reload_advice_table() -> AdviceTable:
    load_advice_table.cache_clear()
    return load_advice_table()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _score_item(item: dict[str, Any], normalized_query: str) -> tuple[float, list[str], list[str]]:
    score = 0.0
    matched_keywords: list[str] = []
    matched_triggers: list[str] = []

    for keyword in item["keywords"]:
        normalized_keyword = _normalize_text(keyword)
        if not normalized_keyword:
            continue
        if normalized_keyword in normalized_query:
            score += 10.0 + min(len(normalized_keyword), 10) * 0.1
            matched_keywords.append(keyword)
        elif normalized_query and normalized_query in normalized_keyword:
            score += 2.0

    for trigger in item["triggers"]:
        any_terms = trigger.get("any", [])
        all_terms = trigger.get("all", [])

        hit_any = [term for term in any_terms if _normalize_text(term) in normalized_query]
        if hit_any:
            score += 8.0
            matched_triggers.append(f"any:{','.join(hit_any)}")

        if all_terms and all(_normalize_text(term) in normalized_query for term in all_terms):
            score += 12.0
            matched_triggers.append(f"all:{','.join(all_terms)}")

    return score, matched_keywords, matched_triggers


def match_advice_from_table(query: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    table = load_advice_table()
    try:
        max_matches = int(os.getenv("ADVICE_TABLE_MAX_MATCHES", "3"))
    except ValueError:
        max_matches = 3
    max_matches = max(1, max_matches)
    normalized_query = _normalize_text(query)

    candidates: list[tuple[float, dict[str, Any], list[str], list[str]]] = []
    for item in table.items:
        score, matched_keywords, matched_triggers = _score_item(item, normalized_query)
        if score > 0:
            candidates.append((score, item, matched_keywords, matched_triggers))

    candidates.sort(key=lambda row: (-row[0], row[1]["id"]))
    selected = candidates[:max_matches]

    matched_items = []
    reasons = []
    for score, item, matched_keywords, matched_triggers in selected:
        matched_items.append(
            {
                "id": item["id"],
                "title": item["title"],
                "advice": item["advice"],
                "handoffs": item["handoffs"],
                "followup_questions": item["followup_questions"],
                "safety": item["safety"],
            }
        )
        reasons.append(
            {
                "id": item["id"],
                "score": round(score, 3),
                "matched_keywords": matched_keywords,
                "matched_triggers": matched_triggers,
            }
        )

    return {
        "ok": True,
        "tool": "match_advice_from_table",
        "query": query,
        "matched_items": matched_items,
        "reasons": reasons,
        "source_path": str(table.source_path),
        "context_hint": {
            "channel": (context or {}).get("channel", ""),
        },
    }
