import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.i18n import (
    localized_terms,
    normalize_language,
    normalize_localized_list,
    normalize_localized_text,
    resolve_localized_text,
)


PROFILE_FIELDS = ("age", "gender", "sleep", "diet", "bowel", "emotion", "exercise", "recent_discomfort")
CONSTITUTION_ENGLISH_FALLBACKS = {
    "气虚": "Qi deficiency",
    "阳虚": "Yang deficiency",
    "阴虚": "Yin deficiency",
    "痰湿": "Phlegm-dampness",
    "湿热": "Damp-heat",
    "气滞": "Qi stagnation",
    "血瘀": "Blood stasis",
    "气血两虚": "Qi and blood deficiency",
}
FIELD_LABELS = {
    "age": {"zh": "年龄", "en": "Age"},
    "gender": {"zh": "性别", "en": "Gender"},
    "sleep": {"zh": "睡眠", "en": "Sleep"},
    "diet": {"zh": "饮食", "en": "Diet"},
    "bowel": {"zh": "排便", "en": "Bowel"},
    "emotion": {"zh": "情绪", "en": "Emotion"},
    "exercise": {"zh": "运动", "en": "Exercise"},
    "recent_discomfort": {"zh": "最近不适", "en": "Recent discomfort"},
}
FOLLOWUP_QUESTIONS = {
    "age": {"zh": "请补充年龄（例如：28岁）。", "en": "Please share your age, for example 28."},
    "gender": {"zh": "请补充性别信息。", "en": "Please share your gender."},
    "sleep": {"zh": "请补充最近的睡眠情况。", "en": "Please describe your recent sleep pattern."},
    "diet": {
        "zh": "请补充饮食习惯（如辛辣、冷饮、甜食频率）。",
        "en": "Please describe your diet, such as spicy food, cold drinks, or sweets.",
    },
    "bowel": {
        "zh": "请补充排便情况（如便秘、黏腻、便溏等）。",
        "en": "Please describe your bowel habits, such as constipation, sticky stool, or loose stool.",
    },
    "emotion": {
        "zh": "请补充近期情绪状态（如焦虑、烦躁、低落）。",
        "en": "Please describe your recent emotional state, such as anxiety, irritability, or low mood.",
    },
    "exercise": {
        "zh": "请补充每周运动频率和强度。",
        "en": "Please describe your weekly exercise frequency and intensity.",
    },
    "recent_discomfort": {
        "zh": "请补充最近最明显的不适表现。",
        "en": "Please describe your most noticeable recent discomfort.",
    },
}
TITLE_HINT_PATTERN = re.compile(r"[（(]([^()（）]{2,64})[）)]")
TITLE_HINT_SPLIT_PATTERN = re.compile(r"[、，,；;\\/|\s]+")


@dataclass(frozen=True)
class ConstitutionScoringConfig:
    source_path: Path
    constitutions: tuple[str, ...]
    constitution_labels: dict[str, dict[str, str]]
    tie_breaker_priority: tuple[str, ...]
    fields: tuple[str, ...]
    rules: dict[str, Any]
    output_policy: dict[str, Any]


@dataclass(frozen=True)
class HerbalAdviceConfig:
    source_path: Path
    recommendations: tuple[dict[str, Any], ...]
    safety_disclaimer: dict[str, str]
    required_append_text: dict[str, str]
    company_handoffs: tuple[dict[str, Any], ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _resolve_path(path_value: str) -> Path:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (_repo_root() / candidate).resolve()


def _resolve_config_path(*, env_path_key: str, private_path: str, example_env_key: str, example_path: str) -> Path:
    from_env = os.getenv(env_path_key, "").strip()
    if from_env:
        env_path = _resolve_path(from_env)
        if not env_path.exists():
            raise FileNotFoundError(f"{env_path_key} file not found: {env_path}")
        return env_path

    private = _resolve_path(private_path)
    if private.exists():
        return private

    fallback = _resolve_path(os.getenv(example_env_key, example_path))
    if fallback.exists():
        return fallback

    raise FileNotFoundError(
        f"No config found for {env_path_key}. Tried {private_path} and {example_path}."
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return raw


def _to_non_empty_string(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Field '{field_name}' cannot be empty.")
    return text


def _to_list_of_strings(value: Any, field_name: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"Field '{field_name}' must be a list.")
    normalized = [str(item).strip() for item in value if str(item).strip()]
    if not allow_empty and not normalized:
        raise ValueError(f"Field '{field_name}' cannot be empty.")
    return normalized


def _localize_constitution_label(value: str) -> dict[str, str]:
    text = str(value or "").strip()
    return normalize_localized_text(
        {"zh": text, "en": CONSTITUTION_ENGLISH_FALLBACKS.get(text, text)},
        fallback=text,
    )


def _with_english_fallback(value: Any, fallback_map: dict[str, str], *, fallback: str = "") -> dict[str, str]:
    localized = normalize_localized_text(value, fallback=fallback)
    zh = localized["zh"].strip()
    if zh and (not localized["en"].strip() or localized["en"].strip() == zh):
        localized["en"] = fallback_map.get(zh, localized["en"] or zh)
    return localized


def _merge_unique_strings(*groups: Any) -> tuple[str, ...]:
    merged: list[str] = []
    for group in groups:
        if isinstance(group, (list, tuple, set)):
            candidates = group
        else:
            candidates = (group,)
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text and text not in merged:
                merged.append(text)
    return tuple(merged)


def _normalize_match_keywords(value: Any, field_name: str, *fallback_terms: str) -> tuple[str, ...]:
    if value in (None, ""):
        return _merge_unique_strings(fallback_terms)

    if isinstance(value, dict):
        collected: list[str] = []
        for language in ("zh", "en"):
            lang_values = value.get(language, [])
            if lang_values in (None, ""):
                continue
            if not isinstance(lang_values, list):
                raise ValueError(f"Field '{field_name}.{language}' must be a list.")
            collected.extend(str(item).strip() for item in lang_values if str(item).strip())
        return _merge_unique_strings(fallback_terms, collected)

    if not isinstance(value, list):
        raise ValueError(f"Field '{field_name}' must be a list or mapping.")
    return _merge_unique_strings(fallback_terms, value)


def _localized_search_terms(items: list[dict[str, str]] | tuple[dict[str, str], ...]) -> tuple[str, ...]:
    collected: list[str] = []
    for item in items:
        for term in localized_terms(item):
            if term and term not in collected:
                collected.append(term)
    return tuple(collected)


def _localized_list_to_text(
    items: list[dict[str, str]] | tuple[dict[str, str], ...],
    reply_language: str,
    *,
    max_items: int | None = None,
    separator: str | None = None,
) -> str:
    selected = items if max_items is None else items[:max_items]
    texts = [
        resolve_localized_text(item, reply_language)
        for item in selected
        if resolve_localized_text(item, reply_language)
    ]
    if not texts:
        return ""
    joiner = separator if separator is not None else (", " if reply_language == "en" else "、")
    return joiner.join(texts)


def _normalize_add_map(value: Any, field_name: str, constitutions: tuple[str, ...]) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Field '{field_name}' must be a mapping.")
    result: dict[str, float] = {}
    for key, score in value.items():
        constitution = str(key).strip()
        if constitution not in constitutions:
            raise ValueError(f"Unknown constitution '{constitution}' in {field_name}.")
        try:
            result[constitution] = float(score)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid score in {field_name}.{constitution}") from exc
    return result


def _normalize_rule_options(
    value: Any, field_name: str, constitutions: tuple[str, ...]
) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise ValueError(f"Field '{field_name}' must be a mapping.")

    options = value.get("options")
    if not isinstance(options, dict) or not options:
        raise ValueError(f"Field '{field_name}.options' must be a non-empty mapping.")

    normalized: list[dict[str, Any]] = []
    for option_name, option_cfg in options.items():
        name = _to_non_empty_string(option_name, f"{field_name}.option_name")
        if not isinstance(option_cfg, dict):
            raise ValueError(f"Field '{field_name}.options.{name}' must be a mapping.")

        label = _with_english_fallback(
            option_cfg.get("label", name),
            OPTION_LABEL_ENGLISH_FALLBACKS,
            fallback=name,
        )
        add_map = _normalize_add_map(option_cfg.get("add"), f"{field_name}.options.{name}.add", constitutions)
        keywords = _normalize_match_keywords(
            option_cfg.get("match_keywords", []),
            f"{field_name}.options.{name}.match_keywords",
            name,
            *localized_terms(label),
            *(OPTION_MATCH_KEYWORD_ENGLISH_FALLBACKS.get(name, [])),
        )

        normalized.append(
            {
                "option": name,
                "label": label,
                "add": add_map,
                "keywords": keywords,
            }
        )

    return normalized


def _normalize_company_handoffs(value: Any) -> tuple[dict[str, Any], ...]:
    if not value:
        return ()
    if not isinstance(value, list):
        raise ValueError("company_handoffs must be a list.")

    handoffs: list[dict[str, Any]] = []
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"company_handoffs[{idx}] must be a mapping.")
        handoff_type = _to_non_empty_string(item.get("type"), f"company_handoffs[{idx}].type")
        label = normalize_localized_text(item.get("label"), fallback=f"handoff_{idx + 1}")

        normalized: dict[str, Any] = {"type": handoff_type, "label": label}
        if handoff_type in {"questionnaire", "link"}:
            normalized["url"] = _to_non_empty_string(item.get("url"), f"company_handoffs[{idx}].url")
        elif handoff_type == "address":
            normalized["address"] = normalize_localized_text(
                item.get("address"),
                fallback="",
            )
        elif handoff_type == "contact":
            phone = item.get("phone", "")
            email = item.get("email", "")
            phone_text = str(phone or "").strip()
            email_text = str(email or "").strip()
            if not phone_text and not email_text:
                raise ValueError(f"company_handoffs[{idx}] contact must include phone or email.")
            if phone_text:
                normalized["phone"] = phone_text
            if email_text:
                normalized["email"] = email_text
        else:
            raise ValueError(f"Unsupported company_handoffs type: {handoff_type}")
        handoffs.append(normalized)
    return tuple(handoffs)


def _normalize_recommendation(raw_item: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw_item, dict):
        raise ValueError(f"constitution_recommendations[{index}] must be a mapping.")

    constitution = str(raw_item.get("constitution") or raw_item.get("体质") or "").strip()
    if not constitution:
        raise ValueError(f"constitution_recommendations[{index}] missing constitution.")

    symptoms_raw = raw_item.get("symptoms")
    if symptoms_raw is None:
        symptoms_raw = raw_item.get("症状", [])
    symptoms = _to_list_of_strings(symptoms_raw, f"constitution_recommendations[{index}].symptoms")

    herbs_raw = raw_item.get("herbs")
    if herbs_raw is None:
        herbs_raw = raw_item.get("推荐中药搭配", [])
    herbs = _to_list_of_strings(herbs_raw, f"constitution_recommendations[{index}].herbs")

    usage = str(raw_item.get("usage") or raw_item.get("用法用量疗程") or "").strip()
    if not usage:
        raise ValueError(f"constitution_recommendations[{index}] missing usage.")

    cautions = str(raw_item.get("cautions", "")).strip()
    title = str(raw_item.get("title", "")).strip() or f"{constitution}调养建议"
    item_id = str(raw_item.get("id", "")).strip()
    if not item_id:
        item_id = f"{constitution}_{index + 1}"

    return {
        "id": item_id,
        "constitution": constitution,
        "title": title,
        "symptoms": symptoms,
        "herbs": herbs,
        "usage": usage,
        "cautions": cautions,
    }


def extract_recent_discomfort_option_values(
    herb_cfg: HerbalAdviceConfig | None = None,
) -> tuple[str, ...]:
    config = herb_cfg or load_herbal_advice_config()
    values: list[str] = []
    seen: set[str] = set()
    for item in config.recommendations:
        symptoms = item.get("symptoms", [])
        if not isinstance(symptoms, list) or not symptoms:
            continue
        first_value = str(symptoms[0]).strip()
        if not first_value or first_value in seen:
            continue
        seen.add(first_value)
        values.append(first_value)
    return tuple(values)


def _normalize_recommendation(raw_item: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw_item, dict):
        raise ValueError(f"constitution_recommendations[{index}] must be a mapping.")

    constitution = str(raw_item.get("constitution") or raw_item.get("体质") or "").strip()
    if not constitution:
        raise ValueError(f"constitution_recommendations[{index}] missing constitution.")

    symptoms_raw = raw_item.get("symptoms")
    if symptoms_raw is None:
        symptoms_raw = raw_item.get("症状", [])
    symptoms = normalize_localized_list(
        symptoms_raw,
        field_name=f"constitution_recommendations[{index}].symptoms",
    )

    herbs_raw = raw_item.get("herbs")
    if herbs_raw is None:
        herbs_raw = raw_item.get("推荐中药搭配", [])
    herbs = normalize_localized_list(
        herbs_raw,
        field_name=f"constitution_recommendations[{index}].herbs",
    )

    usage = normalize_localized_text(raw_item.get("usage") or raw_item.get("用法用量疗程") or "")
    if not (usage["zh"] or usage["en"]):
        raise ValueError(f"constitution_recommendations[{index}] missing usage.")

    cautions = normalize_localized_text(raw_item.get("cautions", ""))
    title = normalize_localized_text(
        raw_item.get("title", ""),
        fallback=f"{constitution}调养建议",
    )
    item_id = str(raw_item.get("id", "")).strip()
    if not item_id:
        item_id = f"{constitution}_{index + 1}"

    return {
        "id": item_id,
        "constitution": constitution,
        "constitution_label": normalize_localized_text(
            raw_item.get("constitution_label", _localize_constitution_label(constitution)),
            fallback=constitution,
        ),
        "title": title,
        "symptoms": symptoms,
        "symptom_terms": _localized_search_terms(symptoms),
        "herbs": herbs,
        "usage": usage,
        "cautions": cautions,
    }


def extract_recent_discomfort_option_values(
    herb_cfg: HerbalAdviceConfig | None = None,
) -> tuple[str, ...]:
    return tuple(item["value"] for item in extract_recent_discomfort_options(herb_cfg))


def extract_recent_discomfort_options(
    herb_cfg: HerbalAdviceConfig | None = None,
) -> tuple[dict[str, Any], ...]:
    config = herb_cfg or load_herbal_advice_config()
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in config.recommendations:
        symptoms = item.get("symptoms", [])
        if not isinstance(symptoms, (list, tuple)) or not symptoms:
            continue
        first_symptom = _with_english_fallback(symptoms[0], SYMPTOM_ENGLISH_FALLBACKS)
        first_value = resolve_localized_text(first_symptom, "zh") or resolve_localized_text(first_symptom, "en")
        if not first_value or first_value in seen:
            continue
        seen.add(first_value)
        options.append({"value": first_value, "label": first_symptom})
    return tuple(options)


@lru_cache(maxsize=1)
def load_constitution_scoring_config() -> ConstitutionScoringConfig:
    path = _resolve_config_path(
        env_path_key="CONSTITUTION_SCORING_PATH",
        private_path="config/constitution_scoring.private.yaml",
        example_env_key="CONSTITUTION_SCORING_EXAMPLE_PATH",
        example_path="config/constitution_scoring.example.yaml",
    )
    raw = _read_yaml(path)

    schema = raw.get("schema", {})
    if not isinstance(schema, dict):
        raise ValueError("schema must be a mapping.")

    fields = tuple(_to_list_of_strings(schema.get("fields", []), "schema.fields"))
    constitutions = tuple(_to_list_of_strings(schema.get("constitutions", []), "schema.constitutions"))
    constitution_labels = {
        constitution: _localize_constitution_label(constitution) for constitution in constitutions
    }
    raw_constitution_labels = schema.get("constitution_labels", {})
    if raw_constitution_labels:
        if not isinstance(raw_constitution_labels, dict):
            raise ValueError("schema.constitution_labels must be a mapping.")
        for constitution, label in raw_constitution_labels.items():
            constitution_key = str(constitution).strip()
            if constitution_key not in constitution_labels:
                raise ValueError(f"Unknown constitution '{constitution_key}' in schema.constitution_labels.")
            constitution_labels[constitution_key] = normalize_localized_text(
                label,
                fallback=constitution_key,
            )

    rules = raw.get("rules")
    if not isinstance(rules, dict):
        raise ValueError("rules must be a mapping.")

    output_policy = raw.get("output_policy")
    if not isinstance(output_policy, dict):
        raise ValueError("output_policy must be a mapping.")

    tie_breaker_priority = output_policy.get("tie_breaker_priority", [])
    tie_breaker = tuple(_to_list_of_strings(tie_breaker_priority, "output_policy.tie_breaker_priority", allow_empty=True))
    if not tie_breaker:
        tie_breaker = constitutions

    normalized_rules: dict[str, Any] = {}

    age_bucket = rules.get("age_bucket", {})
    if age_bucket:
        if not isinstance(age_bucket, dict):
            raise ValueError("rules.age_bucket must be a mapping.")
        normalized_age_bucket: dict[str, dict[str, float]] = {}
        for bucket_name, bucket_cfg in age_bucket.items():
            name = _to_non_empty_string(bucket_name, "rules.age_bucket.bucket")
            if not isinstance(bucket_cfg, dict):
                raise ValueError(f"rules.age_bucket.{name} must be a mapping.")
            normalized_age_bucket[name] = _normalize_add_map(
                bucket_cfg.get("add"),
                f"rules.age_bucket.{name}.add",
                constitutions,
            )
        normalized_rules["age_bucket"] = normalized_age_bucket

    gender_rule = rules.get("gender", {})
    if gender_rule:
        if not isinstance(gender_rule, dict):
            raise ValueError("rules.gender must be a mapping.")
        normalized_gender: dict[str, dict[str, float]] = {}
        for gender_name, gender_cfg in gender_rule.items():
            name = _to_non_empty_string(gender_name, "rules.gender.name")
            if not isinstance(gender_cfg, dict):
                raise ValueError(f"rules.gender.{name} must be a mapping.")
            normalized_gender[name] = _normalize_add_map(
                gender_cfg.get("add"),
                f"rules.gender.{name}.add",
                constitutions,
            )
        normalized_rules["gender"] = normalized_gender

    for field in ("sleep", "diet", "bowel", "emotion", "exercise"):
        if field in rules:
            normalized_rules[field] = _normalize_rule_options(rules[field], f"rules.{field}", constitutions)

    return ConstitutionScoringConfig(
        source_path=path,
        constitutions=constitutions,
        constitution_labels=constitution_labels,
        tie_breaker_priority=tie_breaker,
        fields=fields,
        rules=normalized_rules,
        output_policy=output_policy,
    )


@lru_cache(maxsize=1)
def load_herbal_advice_config() -> HerbalAdviceConfig:
    path = _resolve_config_path(
        env_path_key="HERBAL_ADVICE_PATH",
        private_path="config/herbal_advice.private.yaml",
        example_env_key="HERBAL_ADVICE_EXAMPLE_PATH",
        example_path="config/herbal_advice.example.yaml",
    )
    raw = _read_yaml(path)

    safety_disclaimer = normalize_localized_text(raw.get("safety_disclaimer", ""))
    required_append_text = normalize_localized_text(raw.get("required_append_text", ""))
    company_handoffs = _normalize_company_handoffs(raw.get("company_handoffs", []))

    recs = raw.get("constitution_recommendations", [])
    if not isinstance(recs, list) or not recs:
        raise ValueError("constitution_recommendations must be a non-empty list.")

    normalized = tuple(_normalize_recommendation(item, idx) for idx, item in enumerate(recs))
    unique_keys: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    deduped: list[dict[str, Any]] = []
    for item in normalized:
        dedupe_key = (
            item["constitution"],
            tuple(sorted(resolve_localized_text(symptom, "zh") for symptom in item["symptoms"])),
            tuple(sorted(resolve_localized_text(herb, "zh") for herb in item["herbs"])),
        )
        if dedupe_key in unique_keys:
            continue
        unique_keys.add(dedupe_key)
        deduped.append(item)

    return HerbalAdviceConfig(
        source_path=path,
        recommendations=tuple(deduped),
        safety_disclaimer=safety_disclaimer,
        required_append_text=required_append_text,
        company_handoffs=company_handoffs,
    )


def reload_constitution_advice_configs() -> None:
    load_constitution_scoring_config.cache_clear()
    load_herbal_advice_config.cache_clear()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _merge_recent_discomfort_values(*values: Any) -> str:
    merged: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in merged:
            continue
        merged.append(text)
    return "\n".join(merged)


def _extract_structured_fields(query: str) -> dict[str, str]:
    text = (query or "").strip()
    result: dict[str, str] = {}
    for field in PROFILE_FIELDS:
        pattern = re.compile(rf"{field}\s*[:：]\s*(.+?)(?:\n|$)", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            result[field] = match.group(1).strip()

    cn_field_map = {
        "age": "年龄",
        "gender": "性别",
        "sleep": "睡眠",
        "diet": "饮食",
        "bowel": "排便",
        "emotion": "情绪",
        "exercise": "运动",
        "recent_discomfort": "最近不适",
    }
    for key, cn_label in cn_field_map.items():
        if key in result:
            continue
        pattern = re.compile(rf"{cn_label}\s*[:：]\s*(.+?)(?:\n|$)")
        match = pattern.search(text)
        if match:
            result[key] = match.group(1).strip()

    age_number = re.search(r"(\d{1,3})\s*岁", text)
    if age_number and "age" not in result:
        result["age"] = age_number.group(1)

    gender_guess = re.search(r"(男|女)", text)
    if gender_guess and "gender" not in result:
        result["gender"] = gender_guess.group(1)

    return result


def _parse_age_bucket(age_text: str, age_bucket_rule: dict[str, dict[str, float]]) -> str | None:
    age_match = re.search(r"\d{1,3}", str(age_text or ""))
    if not age_match:
        return None
    age = int(age_match.group(0))

    for bucket in age_bucket_rule:
        normalized = bucket.strip()
        if re.match(r"^\d+\+$", normalized):
            min_age = int(normalized[:-1])
            if age >= min_age:
                return bucket
            continue
        range_match = re.match(r"^(\d+)\s*-\s*(\d+)$", normalized)
        if range_match:
            min_age = int(range_match.group(1))
            max_age = int(range_match.group(2))
            if min_age <= age <= max_age:
                return bucket
    return None


def _normalize_gender(gender_text: str) -> str | None:
    text = str(gender_text or "").strip()
    if not text:
        return None
    if "女" in text or text.lower() in {"f", "female"}:
        return "女"
    if "男" in text or text.lower() in {"m", "male"}:
        return "男"
    return None


def _score_from_add_map(scores: dict[str, float], add_map: dict[str, float], evidence: list[str], reason: str) -> None:
    for constitution, value in add_map.items():
        scores[constitution] += value
    if add_map:
        evidence.append(reason)


def _match_option_hits(field_text: str, options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = _normalize_text(field_text)
    if not normalized:
        return []
    hits: list[dict[str, Any]] = []
    for option in options:
        keywords = option.get("keywords", ())
        if any(_normalize_text(keyword) in normalized for keyword in keywords):
            hits.append(option)
    return hits


def _score_constitution(
    profile: dict[str, str],
    query: str,
    cfg: ConstitutionScoringConfig,
) -> tuple[dict[str, float], list[str], dict[str, list[str]]]:
    scores = {constitution: 0.0 for constitution in cfg.constitutions}
    evidence: list[str] = []
    hit_options: dict[str, list[str]] = {}

    age_bucket_rule = cfg.rules.get("age_bucket", {})
    if age_bucket_rule and profile.get("age"):
        bucket = _parse_age_bucket(profile["age"], age_bucket_rule)
        if bucket:
            _score_from_add_map(scores, age_bucket_rule[bucket], evidence, f"年龄分段命中: {bucket}")

    gender_rule = cfg.rules.get("gender", {})
    if gender_rule and profile.get("gender"):
        gender = _normalize_gender(profile["gender"])
        if gender and gender in gender_rule:
            _score_from_add_map(scores, gender_rule[gender], evidence, f"性别命中: {gender}")

    for field in ("sleep", "diet", "bowel", "emotion", "exercise"):
        options = cfg.rules.get(field, [])
        if not options:
            continue

        text_value = profile.get(field, "")
        if not text_value:
            text_value = query
        hits = _match_option_hits(text_value, options)
        if not hits:
            continue
        hit_options[field] = [str(item["option"]) for item in hits]
        for hit in hits:
            _score_from_add_map(
                scores,
                hit["add"],
                evidence,
                f"{field}命中: {hit['option']}",
            )

    return scores, evidence, hit_options


def _to_sorted_constitutions(scores: dict[str, float], tie_breaker: tuple[str, ...]) -> list[tuple[str, float]]:
    tie_index = {name: idx for idx, name in enumerate(tie_breaker)}
    return sorted(
        scores.items(),
        key=lambda item: (-item[1], tie_index.get(item[0], 10_000), item[0]),
    )


def _apply_output_policy(
    sorted_scores: list[tuple[str, float]],
    output_policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    top_k = int(output_policy.get("top_k", 2))
    min_gap = float(output_policy.get("min_gap_for_single", 3))
    min_score = float(output_policy.get("min_score_to_output", 3))

    if not sorted_scores:
        return [], False
    if sorted_scores[0][1] < min_score:
        return [], False

    selected = sorted_scores[: max(1, top_k)]
    if len(selected) >= 2 and (selected[0][1] - selected[1][1]) >= min_gap:
        selected = selected[:1]

    total = sum(max(score, 0.0) for _, score in selected) or 1.0
    rows = [
        {
            "constitution": name,
            "score": round(score, 3),
            "confidence": round(max(score, 0.0) / total, 3),
        }
        for name, score in selected
    ]
    return rows, True


def _symptom_score(text: str, symptom_keywords: list[str]) -> float:
    normalized = _normalize_text(text)
    if not normalized:
        return 0.0
    score = 0.0
    for keyword in symptom_keywords:
        key = _normalize_text(keyword)
        if key and key in normalized:
            score += 1.0
    return score


def _merge_recommendation_text(query: str, profile: dict[str, str]) -> str:
    return " ".join(
        [
            query or "",
            profile.get("recent_discomfort", ""),
            profile.get("sleep", ""),
            profile.get("diet", ""),
            profile.get("bowel", ""),
            profile.get("emotion", ""),
            profile.get("exercise", ""),
        ]
    )


def _extract_title_hint_keywords(title: str) -> tuple[str, ...]:
    title_text = str(title or "").strip()
    if not title_text:
        return ()

    hints: list[str] = []
    for match in TITLE_HINT_PATTERN.finditer(title_text):
        raw_hint = (match.group(1) or "").strip()
        if len(raw_hint) < 2:
            continue
        hints.append(raw_hint)
        for token in TITLE_HINT_SPLIT_PATTERN.split(raw_hint):
            cleaned = token.strip()
            if len(cleaned) >= 2:
                hints.append(cleaned)

    return tuple(dict.fromkeys(hints))


def _score_title_hint_match(merged_text: str, title: str) -> tuple[float, tuple[str, ...]]:
    normalized = _normalize_text(merged_text)
    if not normalized:
        return 0.0, ()

    hint_keywords = _extract_title_hint_keywords(title)
    if not hint_keywords:
        return 0.0, ()

    hit_keywords: list[str] = []
    score = 0.0
    for index, keyword in enumerate(hint_keywords):
        token = _normalize_text(keyword)
        if not token or token not in normalized:
            continue
        hit_keywords.append(keyword)
        if index == 0 and len(token) >= 4:
            score += 2.0
        else:
            score += 1.0

    return score, tuple(dict.fromkeys(hit_keywords))


def _select_recommendations_by_title_hint(
    profile: dict[str, str],
    query: str,
    herb_cfg: HerbalAdviceConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged_text = _merge_recommendation_text(query, profile)
    candidates: list[tuple[float, dict[str, Any], tuple[str, ...]]] = []

    for item in herb_cfg.recommendations:
        title_text = str(item.get("title", ""))
        hint_keywords = _extract_title_hint_keywords(title_text)
        title_score, hit_keywords = _score_title_hint_match(merged_text, title_text)
        symptom_score = _symptom_score(merged_text, item["symptoms"])
        # If bracket-hint text exists but title keywords were not an exact hit,
        # allow symptom overlap to trigger direct-path recommendation.
        if title_score <= 0 and not (hint_keywords and symptom_score > 0):
            continue
        if title_score <= 0:
            title_score = 1.0

        total_score = title_score * 5.0 + symptom_score
        candidates.append((total_score, item, hit_keywords))

    candidates.sort(key=lambda row: (-row[0], row[1]["id"]))
    selected: list[dict[str, Any]] = []
    reasons: list[dict[str, Any]] = []
    for score, item, hit_keywords in candidates:
        selected.append(item)
        reasons.append(
            {
                "id": item["id"],
                "title": item["title"],
                "title_hits": list(hit_keywords),
                "score": round(score, 3),
            }
        )
        if len(selected) >= 2:
            break

    return selected, reasons


def _select_recommendations(
    constitutions: list[str],
    profile: dict[str, str],
    query: str,
    herb_cfg: HerbalAdviceConfig,
) -> list[dict[str, Any]]:
    merged_text = _merge_recommendation_text(query, profile)

    candidates: list[tuple[float, dict[str, Any]]] = []
    target_constitutions = set(constitutions)
    for item in herb_cfg.recommendations:
        if target_constitutions and item["constitution"] not in target_constitutions:
            continue
        base = 10.0 if item["constitution"] in target_constitutions else 0.0
        symptom = _symptom_score(merged_text, item["symptoms"])
        total = base + symptom
        if total <= 0:
            continue
        candidates.append((total, item))

    candidates.sort(key=lambda row: (-row[0], row[1]["id"]))
    selected: list[dict[str, Any]] = []
    picked: set[str] = set()
    for _, item in candidates:
        if item["constitution"] in picked:
            continue
        selected.append(item)
        picked.add(item["constitution"])
        if len(selected) >= 2:
            break

    return selected


def _build_followup_questions(profile: dict[str, str]) -> list[str]:
    prompts = {
        "age": "请补充年龄（例如：28岁）。",
        "gender": "请补充性别信息。",
        "sleep": "请补充最近的睡眠情况。",
        "diet": "请补充饮食习惯（如辛辣、冷饮、甜食频率）。",
        "bowel": "请补充排便情况（如便秘、黏腻、便溏等）。",
        "emotion": "请补充近期情绪状态（如焦虑、烦躁、低落）。",
        "exercise": "请补充每周运动频率和强度。",
        "recent_discomfort": "请补充最近最明显的不适表现。",
    }
    return [prompts[field] for field in PROFILE_FIELDS if not str(profile.get(field, "")).strip()]


def _build_matched_items(
    recommendations: list[dict[str, Any]],
    constitutions: list[dict[str, Any]],
    followups: list[str],
    herb_cfg: HerbalAdviceConfig,
    direct_match_mode: bool = False,
    direct_match_reasons: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if direct_match_mode:
        summary_tokens: list[str] = []
        for reason in direct_match_reasons or []:
            if not isinstance(reason, dict):
                continue
            item_id = str(reason.get("id", "")).strip()
            hits = reason.get("title_hits", [])
            if isinstance(hits, list) and hits:
                joined_hits = "、".join(str(hit).strip() for hit in hits if str(hit).strip())
                if joined_hits:
                    summary_tokens.append(f"{item_id}:{joined_hits}")
                    continue
            if item_id:
                summary_tokens.append(item_id)

        assessment_summary = (
            f"按症状直达匹配（命中：{'；'.join(summary_tokens)}）"
            if summary_tokens
            else "按症状直达匹配（命中标题括号症状）"
        )
    else:
        assessment_summary = "、".join(
            f"{row['constitution']}({row['score']})" for row in constitutions
        ) or "暂无法判断，建议补充信息"

    items: list[dict[str, Any]] = []
    for rec in recommendations:
        herbs_text = "、".join(rec["herbs"])
        symptoms_text = "、".join(
            str(symptom).strip() for symptom in rec["symptoms"][:4] if str(symptom).strip()
        )
        if not symptoms_text:
            symptoms_text = "未提供"

        if direct_match_mode:
            advice_lines = [
                f"匹配方式：{assessment_summary}",
                f"建议方向：{rec['constitution']}调养。",
                f"对应症状：{symptoms_text}",
                f"可参考中药：{herbs_text}",
                f"用法建议：{rec['usage']}",
            ]
        else:
            advice_lines = [
                f"体质评估结果：{assessment_summary}",
                f"建议方向：{rec['constitution']}调养。",
                f"对应症状：{symptoms_text}",
                f"可参考中药：{herbs_text}",
                f"用法建议：{rec['usage']}",
            ]
        if rec["cautions"]:
            advice_lines.append(f"注意事项：{rec['cautions']}")

        items.append(
            {
                "id": rec["id"],
                "title": rec["title"],
                "advice": "\n".join(advice_lines),
                "handoffs": list(herb_cfg.company_handoffs),
                "followup_questions": followups[:3],
                "safety": {"disclaimer": herb_cfg.safety_disclaimer} if herb_cfg.safety_disclaimer else {},
            }
        )

    return items


def assess_constitution_and_recommend_herbs(
    query: str,
    profile: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scoring_cfg = load_constitution_scoring_config()
    herb_cfg = load_herbal_advice_config()

    extracted = _extract_structured_fields(query)
    normalized_profile: dict[str, str] = {}
    profile_recent_discomfort_choice = ""
    profile_recent_discomfort_text = ""
    if isinstance(profile, dict):
        profile_recent_discomfort_choice = str(profile.get("recent_discomfort_choice", "")).strip()
        profile_recent_discomfort_text = str(profile.get("recent_discomfort_text", "")).strip()
    for field in PROFILE_FIELDS:
        from_profile = ""
        if isinstance(profile, dict):
            from_profile = str(profile.get(field, "")).strip()
        if field == "recent_discomfort":
            normalized_profile[field] = _merge_recent_discomfort_values(
                from_profile,
                profile_recent_discomfort_choice,
                profile_recent_discomfort_text,
                extracted.get(field, ""),
            )
            continue
        normalized_profile[field] = from_profile or extracted.get(field, "")

    direct_recommendations, direct_match_reasons = _select_recommendations_by_title_hint(
        normalized_profile,
        query,
        herb_cfg,
    )
    bypass_constitution = bool(direct_recommendations)

    if bypass_constitution:
        scores = {constitution: 0.0 for constitution in scoring_cfg.constitutions}
        sorted_scores: list[tuple[str, float]] = []
        selected_constitutions: list[dict[str, Any]] = []
        is_confident = True
        hit_options: dict[str, list[str]] = {}
        evidence = [
            f"symptom_direct_match:{str(reason.get('id', '')).strip()}"
            for reason in direct_match_reasons
            if isinstance(reason, dict)
        ]
        recommendations = direct_recommendations
        followup_questions: list[str] = []
    else:
        scores, evidence, hit_options = _score_constitution(normalized_profile, query, scoring_cfg)
        sorted_scores = _to_sorted_constitutions(scores, scoring_cfg.tie_breaker_priority)
        selected_constitutions, is_confident = _apply_output_policy(sorted_scores, scoring_cfg.output_policy)
        constitution_names = [row["constitution"] for row in selected_constitutions]
        recommendations = _select_recommendations(constitution_names, normalized_profile, query, herb_cfg)
        followup_questions = _build_followup_questions(normalized_profile)

    matched_items = _build_matched_items(
        recommendations=recommendations,
        constitutions=selected_constitutions,
        followups=followup_questions,
        herb_cfg=herb_cfg,
        direct_match_mode=bypass_constitution,
        direct_match_reasons=direct_match_reasons,
    )

    recommendation_rows: list[dict[str, Any]] = []
    for item in recommendations:
        recommendation_rows.append(
            {
                "id": item["id"],
                "constitution": item["constitution"],
                "title": item["title"],
                "symptoms": item["symptoms"],
                "herbs": item["herbs"],
                "usage": item["usage"],
                "cautions": item["cautions"],
            }
        )

    return {
        "ok": True,
        "tool": "assess_constitution_and_recommend_herbs",
        "query": query,
        "input_profile": normalized_profile,
        "constitution_assessment": {
            "selected": selected_constitutions,
            "scores": {k: round(v, 3) for k, v in sorted_scores},
            "evidence": evidence,
            "hit_options": hit_options,
            "is_confident": is_confident,
            "bypassed": bypass_constitution,
            "bypass_reason": "title_parenthetical_symptom_match" if bypass_constitution else "",
        },
        "herbal_recommendations": recommendation_rows,
        "matched_items": matched_items,
        "followup_questions": followup_questions,
        "direct_symptom_match": bypass_constitution,
        "direct_match_reasons": direct_match_reasons,
        "required_append_text": herb_cfg.required_append_text if recommendation_rows else "",
        "requires_company_append": bool(recommendation_rows and herb_cfg.required_append_text),
        "safety_disclaimer": herb_cfg.safety_disclaimer,
        "reasons": (
            [{"kind": "direct_symptom_match", "detail": item} for item in direct_match_reasons]
            if bypass_constitution
            else [{"kind": "constitution", "detail": item} for item in evidence]
        ),
        "source_path": {
            "scoring": str(scoring_cfg.source_path),
            "advice": str(herb_cfg.source_path),
        },
        "context_hint": {
            "channel": (context or {}).get("channel", ""),
            "user_id": (context or {}).get("user_id", ""),
        },
    }


def _localized_field_label(field: str, reply_language: str) -> str:
    return resolve_localized_text(FIELD_LABELS.get(field, field), reply_language, fallback=field)


def _localized_constitution_label(
    constitution: str,
    scoring_cfg: ConstitutionScoringConfig,
    reply_language: str,
) -> str:
    label = scoring_cfg.constitution_labels.get(constitution, _localize_constitution_label(constitution))
    return resolve_localized_text(label, reply_language, fallback=constitution)


def _localized_age_gender_evidence(
    profile: dict[str, str],
    scoring_cfg: ConstitutionScoringConfig,
    reply_language: str,
) -> list[str]:
    evidence: list[str] = []
    age_bucket_rule = scoring_cfg.rules.get("age_bucket", {})
    if age_bucket_rule and profile.get("age"):
        bucket = _parse_age_bucket(profile["age"], age_bucket_rule)
        if bucket:
            if reply_language == "en":
                evidence.append(f"Age bucket match: {bucket}")
            else:
                evidence.append(f"年龄分段命中: {bucket}")

    gender_rule = scoring_cfg.rules.get("gender", {})
    if gender_rule and profile.get("gender"):
        gender = _normalize_gender(profile["gender"])
        if gender and gender in gender_rule:
            gender_display = "Female" if reply_language == "en" and gender == "女" else gender
            if reply_language == "en" and gender == "男":
                gender_display = "Male"
            if reply_language == "en":
                evidence.append(f"Gender match: {gender_display}")
            else:
                evidence.append(f"性别命中: {gender}")
    return evidence


def _localized_option_evidence(
    hit_options: dict[str, list[str]],
    scoring_cfg: ConstitutionScoringConfig,
    reply_language: str,
) -> list[str]:
    evidence: list[str] = []
    for field, option_keys in hit_options.items():
        options = scoring_cfg.rules.get(field, [])
        if not isinstance(options, list) or not option_keys:
            continue
        label_map = {
            str(option.get("option", "")).strip(): resolve_localized_text(
                option.get("label", option.get("option", "")),
                reply_language,
                fallback=str(option.get("option", "")).strip(),
            )
            for option in options
        }
        localized_hits = [label_map.get(option_key, option_key) for option_key in option_keys]
        joiner = ", " if reply_language == "en" else "、"
        if reply_language == "en":
            evidence.append(f"{_localized_field_label(field, reply_language)} match: {joiner.join(localized_hits)}")
        else:
            evidence.append(f"{_localized_field_label(field, reply_language)}命中: {joiner.join(localized_hits)}")
    return evidence


def _extract_title_hint_keywords_localized(title: Any) -> tuple[str, ...]:
    hints: list[str] = []
    for title_text in localized_terms(title):
        for hint in _extract_title_hint_keywords(title_text):
            if hint.lower() in ENGLISH_TITLE_HINT_STOPWORDS:
                continue
            if hint not in hints:
                hints.append(hint)
    return tuple(hints)


def _score_title_hint_match_localized(merged_text: str, title: Any) -> tuple[float, tuple[str, ...]]:
    normalized = _normalize_text(merged_text)
    if not normalized:
        return 0.0, ()

    hint_keywords = _extract_title_hint_keywords_localized(title)
    if not hint_keywords:
        return 0.0, ()

    hit_keywords: list[str] = []
    score = 0.0
    for index, keyword in enumerate(hint_keywords):
        token = _normalize_text(keyword)
        if not token or token not in normalized:
            continue
        hit_keywords.append(keyword)
        if index == 0 and len(token) >= 4:
            score += 2.0
        else:
            score += 1.0
    return score, tuple(dict.fromkeys(hit_keywords))


def _select_recommendations_by_title_hint_localized(
    profile: dict[str, str],
    query: str,
    herb_cfg: HerbalAdviceConfig,
    reply_language: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    merged_text = _merge_recommendation_text(query, profile)
    candidates: list[tuple[float, dict[str, Any], tuple[str, ...]]] = []

    for item in herb_cfg.recommendations:
        hint_keywords = _extract_title_hint_keywords_localized(item.get("title", ""))
        title_score, hit_keywords = _score_title_hint_match_localized(merged_text, item.get("title", ""))
        symptom_score = _symptom_score(merged_text, list(item.get("symptom_terms", ())))
        if title_score <= 0 and not (hint_keywords and symptom_score > 0):
            continue
        if title_score <= 0:
            title_score = 1.0

        total_score = title_score * 5.0 + symptom_score
        candidates.append((total_score, item, hit_keywords))

    candidates.sort(key=lambda row: (-row[0], row[1]["id"]))
    selected: list[dict[str, Any]] = []
    reasons: list[dict[str, Any]] = []
    for score, item, hit_keywords in candidates:
        selected.append(item)
        reasons.append(
            {
                "id": item["id"],
                "title": resolve_localized_text(item.get("title", ""), reply_language),
                "title_hits": list(hit_keywords),
                "score": round(score, 3),
            }
        )
        if len(selected) >= 2:
            break

    return selected, reasons


def _select_recommendations_localized(
    constitutions: list[str],
    profile: dict[str, str],
    query: str,
    herb_cfg: HerbalAdviceConfig,
) -> list[dict[str, Any]]:
    merged_text = _merge_recommendation_text(query, profile)

    candidates: list[tuple[float, dict[str, Any]]] = []
    target_constitutions = set(constitutions)
    for item in herb_cfg.recommendations:
        if target_constitutions and item["constitution"] not in target_constitutions:
            continue
        base = 10.0 if item["constitution"] in target_constitutions else 0.0
        symptom = _symptom_score(merged_text, list(item.get("symptom_terms", ())))
        total = base + symptom
        if total <= 0:
            continue
        candidates.append((total, item))

    candidates.sort(key=lambda row: (-row[0], row[1]["id"]))
    selected: list[dict[str, Any]] = []
    picked: set[str] = set()
    for _, item in candidates:
        if item["constitution"] in picked:
            continue
        selected.append(item)
        picked.add(item["constitution"])
        if len(selected) >= 2:
            break
    return selected


def _build_followup_questions_localized(profile: dict[str, str], reply_language: str) -> list[str]:
    return [
        resolve_localized_text(FOLLOWUP_QUESTIONS[field], reply_language)
        for field in PROFILE_FIELDS
        if not str(profile.get(field, "")).strip()
    ]


def _localize_handoff(handoff: dict[str, Any], reply_language: str) -> dict[str, Any]:
    localized = {"type": str(handoff.get("type", "")).strip()}
    localized["label"] = resolve_localized_text(
        _with_english_fallback(handoff.get("label", ""), HANDOFF_LABEL_ENGLISH_FALLBACKS),
        reply_language,
    )
    if "url" in handoff:
        localized["url"] = str(handoff.get("url", "")).strip()
    if "phone" in handoff:
        localized["phone"] = str(handoff.get("phone", "")).strip()
    if "email" in handoff:
        localized["email"] = str(handoff.get("email", "")).strip()
    if "address" in handoff:
        localized["address"] = resolve_localized_text(handoff.get("address", ""), reply_language)
    return localized


def _localize_recommendation_row(
    recommendation: dict[str, Any],
    scoring_cfg: ConstitutionScoringConfig,
    reply_language: str,
) -> dict[str, Any]:
    constitution_key = str(recommendation.get("constitution", "")).strip()
    constitution_label = resolve_localized_text(
        recommendation.get("constitution_label", scoring_cfg.constitution_labels.get(constitution_key, constitution_key)),
        reply_language,
        fallback=constitution_key,
    )
    title_value = normalize_localized_text(recommendation.get("title", ""))
    title_zh = title_value["zh"].strip()
    title_en = title_value["en"].strip()
    if title_zh and (not title_en or title_en == title_zh):
        title_match = TITLE_HINT_PATTERN.search(title_zh)
        if title_match:
            hint_zh = (title_match.group(1) or "").strip()
            hint_en = SYMPTOM_ENGLISH_FALLBACKS.get(hint_zh, hint_zh)
            title_value["en"] = f"{constitution_label} wellness guidance ({hint_en})"
        else:
            title_value["en"] = f"{constitution_label} wellness guidance"
    return {
        "id": str(recommendation.get("id", "")).strip(),
        "constitution": constitution_label,
        "constitution_key": constitution_key,
        "title": resolve_localized_text(title_value, reply_language),
        "symptoms": [
            resolve_localized_text(_with_english_fallback(symptom, SYMPTOM_ENGLISH_FALLBACKS), reply_language)
            for symptom in recommendation.get("symptoms", [])
            if resolve_localized_text(_with_english_fallback(symptom, SYMPTOM_ENGLISH_FALLBACKS), reply_language)
        ],
        "herbs": [
            resolve_localized_text(_with_english_fallback(herb, HERB_ENGLISH_FALLBACKS), reply_language)
            for herb in recommendation.get("herbs", [])
            if resolve_localized_text(_with_english_fallback(herb, HERB_ENGLISH_FALLBACKS), reply_language)
        ],
        "usage": resolve_localized_text(recommendation.get("usage", ""), reply_language),
        "cautions": resolve_localized_text(recommendation.get("cautions", ""), reply_language),
    }


def _build_matched_items_localized(
    recommendations: list[dict[str, Any]],
    constitutions: list[dict[str, Any]],
    followups: list[str],
    herb_cfg: HerbalAdviceConfig,
    scoring_cfg: ConstitutionScoringConfig,
    reply_language: str,
    *,
    direct_match_mode: bool = False,
    direct_match_reasons: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    joiner = ", " if reply_language == "en" else "、"

    if direct_match_mode:
        summary_tokens: list[str] = []
        for reason in direct_match_reasons or []:
            if not isinstance(reason, dict):
                continue
            item_id = str(reason.get("id", "")).strip()
            hits = reason.get("title_hits", [])
            if isinstance(hits, list) and hits:
                hit_joiner = "; " if reply_language == "en" else "；"
                joined_hits = hit_joiner.join(str(hit).strip() for hit in hits if str(hit).strip())
                if joined_hits:
                    summary_tokens.append(f"{item_id}: {joined_hits}")
                    continue
            if item_id:
                summary_tokens.append(item_id)
        if reply_language == "en":
            assessment_summary = (
                f"Direct symptom match (hits: {'; '.join(summary_tokens)})"
                if summary_tokens
                else "Direct symptom match (matched title-hint symptoms)"
            )
        else:
            assessment_summary = (
                f"按症状直达匹配（命中：{'；'.join(summary_tokens)}）"
                if summary_tokens
                else "按症状直达匹配（命中标题括号症状）"
            )
    else:
        assessment_summary = joiner.join(
            f"{row['constitution']}({row['score']})" for row in constitutions
        )
        if not assessment_summary:
            assessment_summary = (
                "Unable to determine yet. More details are needed."
                if reply_language == "en"
                else "暂时无法判断，建议补充信息"
            )

    items: list[dict[str, Any]] = []
    for recommendation in recommendations:
        row = _localize_recommendation_row(recommendation, scoring_cfg, reply_language)
        herbs_text = joiner.join(row["herbs"]) or ("Not provided" if reply_language == "en" else "未提供")
        symptoms_text = joiner.join(row["symptoms"][:4]) or ("Not provided" if reply_language == "en" else "未提供")

        if direct_match_mode:
            advice_lines = [
                f"Match route: {assessment_summary}" if reply_language == "en" else f"匹配方式：{assessment_summary}",
                (
                    f"Suggested direction: {row['constitution']} wellness support."
                    if reply_language == "en"
                    else f"建议方向：{row['constitution']}调养。"
                ),
                f"Relevant symptoms: {symptoms_text}" if reply_language == "en" else f"对应症状：{symptoms_text}",
                f"Suggested herbs: {herbs_text}" if reply_language == "en" else f"可参考中药：{herbs_text}",
                f"Usage: {row['usage']}" if reply_language == "en" else f"用法建议：{row['usage']}",
            ]
        else:
            advice_lines = [
                (
                    f"Constitution assessment: {assessment_summary}"
                    if reply_language == "en"
                    else f"体质评估结果：{assessment_summary}"
                ),
                (
                    f"Suggested direction: {row['constitution']} wellness support."
                    if reply_language == "en"
                    else f"建议方向：{row['constitution']}调养。"
                ),
                f"Relevant symptoms: {symptoms_text}" if reply_language == "en" else f"对应症状：{symptoms_text}",
                f"Suggested herbs: {herbs_text}" if reply_language == "en" else f"可参考中药：{herbs_text}",
                f"Usage: {row['usage']}" if reply_language == "en" else f"用法建议：{row['usage']}",
            ]
        if row["cautions"]:
            advice_lines.append(
                f"Cautions: {row['cautions']}" if reply_language == "en" else f"注意事项：{row['cautions']}"
            )

        items.append(
            {
                "id": row["id"],
                "title": row["title"],
                "advice": "\n".join(advice_lines),
                "handoffs": [_localize_handoff(handoff, reply_language) for handoff in herb_cfg.company_handoffs],
                "followup_questions": followups[:3],
                "safety": {
                    "disclaimer": resolve_localized_text(herb_cfg.safety_disclaimer, reply_language)
                }
                if resolve_localized_text(herb_cfg.safety_disclaimer, reply_language)
                else {},
            }
        )
    return items


def assess_constitution_and_recommend_herbs(
    query: str,
    profile: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scoring_cfg = load_constitution_scoring_config()
    herb_cfg = load_herbal_advice_config()
    reply_language = normalize_language((context or {}).get("reply_language"), default="zh")

    extracted = _extract_structured_fields(query)
    normalized_profile: dict[str, str] = {}
    profile_recent_discomfort_choice = ""
    profile_recent_discomfort_text = ""
    if isinstance(profile, dict):
        profile_recent_discomfort_choice = str(profile.get("recent_discomfort_choice", "")).strip()
        profile_recent_discomfort_text = str(profile.get("recent_discomfort_text", "")).strip()
    for field in PROFILE_FIELDS:
        from_profile = ""
        if isinstance(profile, dict):
            from_profile = str(profile.get(field, "")).strip()
        if field == "recent_discomfort":
            normalized_profile[field] = _merge_recent_discomfort_values(
                from_profile,
                profile_recent_discomfort_choice,
                profile_recent_discomfort_text,
                extracted.get(field, ""),
            )
            continue
        normalized_profile[field] = from_profile or extracted.get(field, "")

    direct_recommendations, direct_match_reasons = _select_recommendations_by_title_hint_localized(
        normalized_profile,
        query,
        herb_cfg,
        reply_language,
    )
    bypass_constitution = bool(direct_recommendations)

    if bypass_constitution:
        sorted_scores: list[tuple[str, float]] = []
        selected_constitutions: list[dict[str, Any]] = []
        is_confident = True
        hit_options: dict[str, list[str]] = {}
        evidence = [
            (
                f"Direct symptom match: {str(reason.get('id', '')).strip()}"
                if reply_language == "en"
                else f"症状直达匹配: {str(reason.get('id', '')).strip()}"
            )
            for reason in direct_match_reasons
            if isinstance(reason, dict)
        ]
        recommendations = direct_recommendations
        followup_questions: list[str] = []
    else:
        scores, _, hit_options = _score_constitution(normalized_profile, query, scoring_cfg)
        sorted_scores = _to_sorted_constitutions(scores, scoring_cfg.tie_breaker_priority)
        selected_constitutions, is_confident = _apply_output_policy(sorted_scores, scoring_cfg.output_policy)
        constitution_names = [row["constitution"] for row in selected_constitutions]
        recommendations = _select_recommendations_localized(
            constitution_names,
            normalized_profile,
            query,
            herb_cfg,
        )
        followup_questions = _build_followup_questions_localized(normalized_profile, reply_language)
        evidence = _localized_age_gender_evidence(normalized_profile, scoring_cfg, reply_language)
        evidence.extend(_localized_option_evidence(hit_options, scoring_cfg, reply_language))

    localized_constitutions = [
        {
            "constitution": _localized_constitution_label(row["constitution"], scoring_cfg, reply_language),
            "constitution_key": row["constitution"],
            "score": row["score"],
            "confidence": row["confidence"],
        }
        for row in selected_constitutions
    ]
    localized_scores = {
        _localized_constitution_label(name, scoring_cfg, reply_language): round(score, 3)
        for name, score in sorted_scores
    }
    recommendation_rows = [
        _localize_recommendation_row(item, scoring_cfg, reply_language) for item in recommendations
    ]
    matched_items = _build_matched_items_localized(
        recommendations=recommendations,
        constitutions=localized_constitutions,
        followups=followup_questions,
        herb_cfg=herb_cfg,
        scoring_cfg=scoring_cfg,
        reply_language=reply_language,
        direct_match_mode=bypass_constitution,
        direct_match_reasons=direct_match_reasons,
    )

    return {
        "ok": True,
        "tool": "assess_constitution_and_recommend_herbs",
        "query": query,
        "input_profile": normalized_profile,
        "constitution_assessment": {
            "selected": localized_constitutions,
            "scores": localized_scores,
            "scores_by_key": {k: round(v, 3) for k, v in sorted_scores},
            "evidence": evidence,
            "hit_options": {
                field: [
                    resolve_localized_text(
                        next(
                            (
                                option.get("label", option_key)
                                for option in scoring_cfg.rules.get(field, [])
                                if str(option.get("option", "")).strip() == option_key
                            ),
                            option_key,
                        ),
                        reply_language,
                        fallback=option_key,
                    )
                    for option_key in option_keys
                ]
                for field, option_keys in hit_options.items()
            },
            "is_confident": is_confident,
            "bypassed": bypass_constitution,
            "bypass_reason": "title_parenthetical_symptom_match" if bypass_constitution else "",
        },
        "herbal_recommendations": recommendation_rows,
        "matched_items": matched_items,
        "followup_questions": followup_questions,
        "direct_symptom_match": bypass_constitution,
        "direct_match_reasons": direct_match_reasons,
        "required_append_text": (
            resolve_localized_text(herb_cfg.required_append_text, reply_language) if recommendation_rows else ""
        ),
        "requires_company_append": bool(
            recommendation_rows and resolve_localized_text(herb_cfg.required_append_text, reply_language)
        ),
        "safety_disclaimer": resolve_localized_text(herb_cfg.safety_disclaimer, reply_language),
        "reasons": (
            [{"kind": "direct_symptom_match", "detail": item} for item in direct_match_reasons]
            if bypass_constitution
            else [{"kind": "constitution", "detail": item} for item in evidence]
        ),
        "source_path": {
            "scoring": str(scoring_cfg.source_path),
            "advice": str(herb_cfg.source_path),
        },
        "context_hint": {
            "channel": (context or {}).get("channel", ""),
            "user_id": (context or {}).get("user_id", ""),
            "reply_language": reply_language,
        },
    }


OPTION_LABEL_ENGLISH_FALLBACKS = {
    "入睡困难>30分钟/心烦": "Difficulty falling asleep for more than 30 minutes / irritability",
    "多梦易醒/睡浅": "Vivid dreams / light sleep",
    "早醒且难再睡": "Early awakening and difficulty falling back asleep",
    "睡很久仍疲惫/白天嗜睡": "Long sleep but still tired / daytime sleepiness",
    "夜间怕冷/起夜影响睡眠": "Night cold sensitivity / nocturia affects sleep",
    "睡眠总体正常": "Sleep is generally normal",
    "嗜冷饮/生冷多": "Frequent cold drinks / raw and cold foods",
    "嗜甜/奶茶/精制碳水多": "Frequent sweets / milk tea / refined carbs",
    "油炸烧烤辛辣多": "Frequent fried, grilled, or spicy food",
    "口干爱喝水/偏辛辣且越吃越上火": "Dry mouth with spicy preference and heat signs",
    "食欲不振/吃一点就胀/饭后犯困": "Poor appetite / bloating after little food / sleepy after meals",
    "情绪影响食欲": "Appetite affected by emotions",
    "饮食清淡规律": "Light and regular diet",
    "便秘干结/羊屎/排便费劲": "Dry constipation / pellet stool / difficult bowel movement",
    "大便黏腻冲不净/味重": "Sticky stool / hard to flush / strong odor",
    "便溏/不成形/吃冷就拉": "Loose stool / unformed stool / cold food triggers diarrhea",
    "时干时稀/紧张就想上厕所": "Alternating constipation and loose stool / stress-triggered urgency",
    "大便正常": "Bowel movement is generally normal",
    "易焦虑紧张/胸闷爱叹气": "Anxiety / tension / chest tightness / frequent sighing",
    "易烦躁发火/口苦/痘多": "Irritability / anger / bitter taste / frequent acne",
    "情绪低落/动力不足/说话没劲": "Low mood / low motivation / weak speech",
    "压力大且睡差": "High stress with poor sleep",
    "情绪稳定": "Emotion is generally stable",
    "几乎不运动": "Rarely exercise",
    "每周1-2次轻运动": "Light exercise 1-2 times per week",
    "每周3+次规律运动": "Regular exercise 3+ times per week",
    "稍动就气喘/出汗多/恢复慢": "Easily breathless / heavy sweating / slow recovery",
    "运动后口干心烦/睡更差": "Dry mouth / restlessness / worse sleep after exercise",
    "运动少+明显怕冷/冬天加重": "Little exercise with cold intolerance / worse in winter",
}
OPTION_MATCH_KEYWORD_ENGLISH_FALLBACKS = {
    "入睡困难>30分钟/心烦": ["difficulty falling asleep", "can't fall asleep", "irritability", "awake for 30 minutes"],
    "多梦易醒/睡浅": ["light sleep", "vivid dreams", "wake easily", "restless sleep"],
    "早醒且难再睡": ["wake up early", "early awakening", "can't fall back asleep"],
    "睡很久仍疲惫/白天嗜睡": ["daytime sleepiness", "sleepy during the day", "sleep a lot but still tired"],
    "夜间怕冷/起夜影响睡眠": ["cold at night", "wake up to urinate", "nocturia", "night urination"],
    "睡眠总体正常": ["sleep is normal", "sleep okay", "sleep fine"],
    "嗜冷饮/生冷多": ["cold drinks", "iced drinks", "raw food", "cold food"],
    "嗜甜/奶茶/精制碳水多": ["sweets", "milk tea", "refined carbs", "sugary food"],
    "油炸烧烤辛辣多": ["fried food", "barbecue", "spicy food", "hot pot"],
    "口干爱喝水/偏辛辣且越吃越上火": ["dry mouth", "drink a lot of water", "spicy food causes heat", "heat signs"],
    "食欲不振/吃一点就胀/饭后犯困": ["poor appetite", "bloating after little food", "sleepy after meals"],
    "情绪影响食欲": ["stress affects appetite", "mood affects appetite", "eat when anxious"],
    "饮食清淡规律": ["light diet", "regular diet", "diet is regular"],
    "便秘干结/羊屎/排便费劲": ["constipation", "dry stool", "pellet stool", "hard to pass stool"],
    "大便黏腻冲不净/味重": ["sticky stool", "hard to flush", "strong stool odor"],
    "便溏/不成形/吃冷就拉": ["loose stool", "unformed stool", "diarrhea after cold food"],
    "时干时稀/紧张就想上厕所": ["alternating constipation and diarrhea", "stress triggers bowel urgency"],
    "大便正常": ["bowel is normal", "normal stool", "normal bowel movement"],
    "易焦虑紧张/胸闷爱叹气": ["anxious", "tense", "chest tightness", "sigh a lot"],
    "易烦躁发火/口苦/痘多": ["irritable", "angry", "bitter taste", "acne"],
    "情绪低落/动力不足/说话没劲": ["low mood", "low motivation", "weak voice", "no energy"],
    "压力大且睡差": ["stressed and sleeping badly", "high stress", "stress with poor sleep"],
    "情绪稳定": ["emotionally stable", "mood is stable"],
    "几乎不运动": ["rarely exercise", "sedentary", "no exercise"],
    "每周1-2次轻运动": ["light exercise 1-2 times", "exercise once or twice a week"],
    "每周3+次规律运动": ["exercise three times a week", "regular exercise"],
    "稍动就气喘/出汗多/恢复慢": ["short of breath with little activity", "sweat easily", "slow recovery"],
    "运动后口干心烦/睡更差": ["dry mouth after exercise", "restless after exercise", "sleep worse after exercise"],
    "运动少+明显怕冷/冬天加重": ["little exercise", "cold intolerance", "worse in winter"],
}
SYMPTOM_ENGLISH_FALLBACKS = {
    "易疲劳": "Fatigue",
    "恢复慢": "Slow recovery",
    "手脚冰凉": "Cold hands and feet",
    "腰膝酸软": "Sore lower back and knees",
    "口干咽燥": "Dry mouth and throat",
    "熬夜后上火": "Heat signs after staying up late",
    "身体困重": "Heavy body and sluggishness",
    "食欲差": "Poor appetite",
    "口苦": "Bitter taste in the mouth",
    "大便黏腻": "Sticky stool",
    "胸闷": "Chest tightness",
    "胀气": "Bloating and gas",
    "刺痛固定": "Fixed stabbing pain",
    "肩颈僵硬": "Neck and shoulder stiffness",
    "面色偏淡": "Pale complexion",
    "长期过劳": "Long-term overwork",
    "面色暗淡": "Dull complexion",
    "皮肤干燥": "Dry skin",
    "便秘干结": "Dry constipation",
    "痘痘反复": "Recurrent acne",
    "情绪紧张后便秘": "Stress-related constipation",
    "减肥困难": "Difficulty losing weight",
    "食量小": "Small appetite",
    "放屁频繁": "Frequent gas",
    "失眠多梦": "Insomnia with vivid dreams",
    "怕冷": "Cold intolerance",
    "性欲减退": "Low libido",
    "掉发增多": "Increased hair loss",
    "动则出汗": "Sweating with slight exertion",
    "胃胀气": "Stomach bloating",
    "月经量少": "Light menstrual flow",
    "记忆力下降": "Memory decline",
    "容易积食": "Easily gets food stagnation",
    "腹泻": "Diarrhea",
    "口腔溃疡反复": "Recurrent mouth ulcers",
    "腰酸": "Lower back soreness",
    "压力大": "High stress",
}
HERB_ENGLISH_FALLBACKS = {
    "冬虫夏草": "Cordyceps",
    "黄芪": "Astragalus",
    "党参": "Codonopsis",
    "枸杞": "Goji berry",
    "西洋参片": "American ginseng slices",
    "五味子": "Schisandra",
    "山药": "Chinese yam",
    "芡实": "Euryale seed",
    "杜仲": "Eucommia bark",
    "干姜": "Dried ginger",
    "麦冬": "Ophiopogon root",
    "石斛": "Dendrobium",
    "玉竹": "Polygonatum odoratum",
    "火麻仁": "Hemp seed",
    "茯苓": "Poria",
    "薏苡仁": "Job's tears seed",
    "陈皮": "Aged tangerine peel",
    "砂仁": "Amomum fruit",
    "山楂": "Hawthorn",
    "麦芽": "Barley malt",
    "蒲公英": "Dandelion",
    "金银花": "Honeysuckle",
    "菊花": "Chrysanthemum",
    "车前草": "Plantain herb",
    "玉米须": "Corn silk",
    "玫瑰花": "Rose bud",
    "佛手": "Finger citron",
    "合欢皮": "Albizia bark",
    "香附": "Cyperus rhizome",
    "柴胡": "Bupleurum",
    "红花": "Safflower",
    "丹参": "Salvia root",
    "川芎": "Ligusticum",
    "当归": "Angelica sinensis",
    "连翘": "Forsythia",
    "酸枣仁": "Sour jujube seed",
    "柏子仁": "Biota seed",
}
HANDOFF_LABEL_ENGLISH_FALLBACKS = {
    "公司地址": "Company address",
}
ENGLISH_TITLE_HINT_STOPWORDS = {"and", "or", "with", "the", "for", "of", "to", "in", "on", "a", "an"}
