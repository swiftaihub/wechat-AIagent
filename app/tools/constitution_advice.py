import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


PROFILE_FIELDS = ("age", "gender", "sleep", "diet", "bowel", "emotion", "exercise", "recent_discomfort")
TITLE_HINT_PATTERN = re.compile(r"[（(]([^()（）]{2,64})[）)]")
TITLE_HINT_SPLIT_PATTERN = re.compile(r"[、，,；;\\/|\s]+")


@dataclass(frozen=True)
class ConstitutionScoringConfig:
    source_path: Path
    constitutions: tuple[str, ...]
    tie_breaker_priority: tuple[str, ...]
    fields: tuple[str, ...]
    rules: dict[str, Any]
    output_policy: dict[str, Any]


@dataclass(frozen=True)
class HerbalAdviceConfig:
    source_path: Path
    recommendations: tuple[dict[str, Any], ...]
    safety_disclaimer: str
    required_append_text: str
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

        add_map = _normalize_add_map(option_cfg.get("add"), f"{field_name}.options.{name}.add", constitutions)
        match_keywords = option_cfg.get("match_keywords", [])
        if match_keywords and not isinstance(match_keywords, list):
            raise ValueError(f"Field '{field_name}.options.{name}.match_keywords' must be a list.")
        keywords = [name]
        keywords.extend(str(item).strip() for item in (match_keywords or []) if str(item).strip())

        normalized.append(
            {
                "option": name,
                "add": add_map,
                "keywords": tuple(dict.fromkeys(keywords)),
            }
        )

    return normalized


def _normalize_company_handoffs(value: Any) -> tuple[dict[str, str], ...]:
    if not value:
        return ()
    if not isinstance(value, list):
        raise ValueError("company_handoffs must be a list.")

    handoffs: list[dict[str, str]] = []
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"company_handoffs[{idx}] must be a mapping.")
        handoff_type = _to_non_empty_string(item.get("type"), f"company_handoffs[{idx}].type")
        label = _to_non_empty_string(item.get("label"), f"company_handoffs[{idx}].label")

        normalized: dict[str, str] = {"type": handoff_type, "label": label}
        if handoff_type in {"questionnaire", "link"}:
            normalized["url"] = _to_non_empty_string(item.get("url"), f"company_handoffs[{idx}].url")
        elif handoff_type == "address":
            normalized["address"] = _to_non_empty_string(
                item.get("address"),
                f"company_handoffs[{idx}].address",
            )
        elif handoff_type == "contact":
            phone = str(item.get("phone", "")).strip()
            email = str(item.get("email", "")).strip()
            if not phone and not email:
                raise ValueError(f"company_handoffs[{idx}] contact must include phone or email.")
            if phone:
                normalized["phone"] = phone
            if email:
                normalized["email"] = email
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

    safety_disclaimer = str(raw.get("safety_disclaimer", "")).strip()
    required_append_text = str(raw.get("required_append_text", "")).strip()
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
            tuple(sorted(item["symptoms"])),
            tuple(sorted(item["herbs"])),
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
    for field in PROFILE_FIELDS:
        from_profile = ""
        if isinstance(profile, dict):
            from_profile = str(profile.get(field, "")).strip()
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
