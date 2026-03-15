from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.i18n import normalize_localized_text


@dataclass(frozen=True)
class QuestionnaireConfig:
    source_path: Path
    title: dict[str, str]
    description: dict[str, str]
    submit_button: dict[str, str]
    reset_button: dict[str, str]
    submit_notice: dict[str, str]
    fields: tuple[dict[str, Any], ...]
    auto_collapse_on_submit: bool
    enabled: bool


@dataclass(frozen=True)
class ConstitutionConfig:
    source_path: Path
    schema: dict[str, Any]
    constitutions: dict[str, dict[str, Any]]
    signals: dict[str, Any]
    free_text_signals: tuple[dict[str, Any], ...]
    output_policy: dict[str, Any]
    purchase_signals: dict[str, Any]


@dataclass(frozen=True)
class KnowledgeBaseConfig:
    source_path: Path
    conversation_modes: dict[str, Any]
    intents: tuple[dict[str, Any], ...]
    brand_scope: dict[str, Any]


@dataclass(frozen=True)
class LinkRoutingConfig:
    source_path: Path
    base_url: str
    routes: dict[str, str]
    collections: tuple[dict[str, Any], ...]
    product_overrides: dict[str, dict[str, Any]]
    ingredient_overrides: dict[str, dict[str, Any]]
    article_overrides: dict[str, dict[str, Any]]
    selection_rules: dict[str, Any]


@dataclass(frozen=True)
class RuntimeLimitsConfig:
    source_path: Path
    channels: dict[str, dict[str, Any]]
    shared: dict[str, Any]


@dataclass(frozen=True)
class CommerceGuardrailConfig:
    source_path: Path
    blocked_medical_diagnosis_patterns: tuple[str, ...]
    blocked_treatment_promise_patterns: tuple[str, ...]
    blocked_cure_patterns: tuple[str, ...]
    blocked_replacement_of_care_patterns: tuple[str, ...]
    high_risk_patterns: tuple[dict[str, Any], ...]
    caution_patterns: tuple[dict[str, Any], ...]
    allowed_educational_claims: tuple[str, ...]
    disallowed_medical_claims: tuple[str, ...]
    compliant_phrasing: tuple[dict[str, str], ...]
    safer_rewrite_examples: tuple[dict[str, str], ...]
    fallback_responses: dict[str, dict[str, str]]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_path(path_value: str) -> Path:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (_repo_root() / candidate).resolve()


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def _candidate_paths(*values: str) -> tuple[Path, ...]:
    paths: list[Path] = []
    for value in values:
        if not value:
            continue
        path = _resolve_path(value)
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def _resolve_first_existing(candidates: tuple[Path, ...], config_name: str) -> Path:
    for path in candidates:
        if path.exists():
            return path
    tried = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"Could not locate {config_name}. Tried: {tried}")


def _load_private_or_example(
    *,
    env_keys: tuple[str, ...],
    private_candidates: tuple[str, ...],
    example_candidates: tuple[str, ...],
    config_name: str,
) -> Path:
    env_paths: list[str] = []
    for env_key in env_keys:
        env_value = os.getenv(env_key, "").strip()
        if env_value:
            env_paths.append(env_value)
    candidates = _candidate_paths(*env_paths, *private_candidates, *example_candidates)
    return _resolve_first_existing(candidates, config_name)


def _normalize_field(field: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(field)
    normalized["label"] = normalize_localized_text(field.get("label", field.get("name", "")))
    if isinstance(field.get("description"), dict) or field.get("description"):
        normalized["description"] = normalize_localized_text(field.get("description", ""))
    if isinstance(field.get("helper_text"), dict) or field.get("helper_text"):
        normalized["helper_text"] = normalize_localized_text(field.get("helper_text", ""))
    if isinstance(field.get("placeholder"), dict) or field.get("placeholder"):
        normalized["placeholder"] = normalize_localized_text(field.get("placeholder", ""))
    normalized["ui_variant"] = str(field.get("ui_variant", "")).strip()

    options: list[dict[str, Any]] = []
    for option in field.get("options", []) if isinstance(field.get("options"), list) else []:
        if not isinstance(option, dict):
            continue
        value = str(option.get("value", "")).strip()
        if not value:
            continue
        normalized_option = {
            "value": value,
            "label": normalize_localized_text(option.get("label", value), fallback=value),
            "tags": tuple(str(item).strip() for item in option.get("tags", []) if str(item).strip()),
            "constitution_weight": option.get("constitution_weight", {}),
        }
        if isinstance(option.get("description"), dict) or option.get("description"):
            normalized_option["description"] = normalize_localized_text(option.get("description", ""))
        if isinstance(option.get("eyebrow"), dict) or option.get("eyebrow"):
            normalized_option["eyebrow"] = normalize_localized_text(option.get("eyebrow", ""))
        options.append(normalized_option)
    normalized["options"] = options
    normalized["importance"] = dict(field.get("importance", {})) if isinstance(field.get("importance"), dict) else {}
    normalized["show_if"] = dict(field.get("show_if", {})) if isinstance(field.get("show_if"), dict) else {}
    normalized["type"] = str(field.get("type", "single")).strip().lower()
    normalized["name"] = str(field.get("name", "")).strip()
    normalized["required"] = bool(field.get("required", False))
    normalized["full_width"] = bool(field.get("full_width", normalized["type"] == "text"))
    return normalized


@lru_cache(maxsize=1)
def load_questionnaire_config() -> QuestionnaireConfig:
    source_path = _load_private_or_example(
        env_keys=("QUESTIONNAIRE_CONFIG_PATH", "WEBUI_INTAKE_CONFIG_PATH"),
        private_candidates=("config/questionnaire.private.yaml", "config/questionaire.private.yaml"),
        example_candidates=("config/questionnaire.example.yaml", "config/questionaire.example.yaml"),
        config_name="questionnaire config",
    )
    raw = _read_yaml(source_path)
    section = raw.get("questionnaire", raw)
    fields = tuple(
        _normalize_field(field)
        for field in section.get("fields", [])
        if isinstance(field, dict) and str(field.get("name", "")).strip()
    )
    return QuestionnaireConfig(
        source_path=source_path,
        title=normalize_localized_text(section.get("title", "Quick intake")),
        description=normalize_localized_text(section.get("description", "")),
        submit_button=normalize_localized_text(section.get("submit_button", "Submit")),
        reset_button=normalize_localized_text(section.get("reset_button", "Reset")),
        submit_notice=normalize_localized_text(section.get("submit_notice", "")),
        fields=fields,
        auto_collapse_on_submit=bool(section.get("auto_collapse_on_submit", True)),
        enabled=bool(section.get("enabled", True)),
    )


@lru_cache(maxsize=1)
def load_constitution_config() -> ConstitutionConfig:
    source_path = _load_private_or_example(
        env_keys=("CONSTITUTION_SCORING_PATH",),
        private_candidates=("config/constitution_scoring.private.yaml",),
        example_candidates=("config/constitution_scoring.example.yaml",),
        config_name="constitution scoring config",
    )
    raw = _read_yaml(source_path)
    constitutions = raw.get("constitutions", {})
    if not isinstance(constitutions, dict) or not constitutions:
        raise ValueError("constitution_scoring config must define a non-empty 'constitutions' mapping.")
    return ConstitutionConfig(
        source_path=source_path,
        schema=raw.get("schema", {}),
        constitutions=constitutions,
        signals=raw.get("signals", {}),
        free_text_signals=tuple(item for item in raw.get("free_text_signals", []) if isinstance(item, dict)),
        output_policy=raw.get("output_policy", {}),
        purchase_signals=raw.get("purchase_signals", {}),
    )


@lru_cache(maxsize=1)
def load_knowledge_base_config() -> KnowledgeBaseConfig:
    source_path = _load_private_or_example(
        env_keys=("HERBAL_ADVICE_PATH",),
        private_candidates=("config/herbal_advice.private.yaml",),
        example_candidates=("config/herbal_advice.example.yaml",),
        config_name="product helper knowledge config",
    )
    raw = _read_yaml(source_path)
    intents = tuple(item for item in raw.get("intents", []) if isinstance(item, dict))
    return KnowledgeBaseConfig(
        source_path=source_path,
        conversation_modes=raw.get("conversation_modes", {}),
        intents=intents,
        brand_scope=raw.get("brand_scope", {}),
    )


@lru_cache(maxsize=1)
def load_link_routing_config() -> LinkRoutingConfig:
    source_path = _load_private_or_example(
        env_keys=("LINK_INDEX_PATH",),
        private_candidates=("config/link_index.private.yaml",),
        example_candidates=("config/link_index.example.yaml",),
        config_name="link routing config",
    )
    raw = _read_yaml(source_path)
    return LinkRoutingConfig(
        source_path=source_path,
        base_url=str(raw.get("base_url", "https://tea.swiftaihub.com")).rstrip("/"),
        routes=raw.get("routes", {}),
        collections=tuple(item for item in raw.get("collections", []) if isinstance(item, dict)),
        product_overrides=raw.get("product_overrides", {}) if isinstance(raw.get("product_overrides"), dict) else {},
        ingredient_overrides=raw.get("ingredient_overrides", {}) if isinstance(raw.get("ingredient_overrides"), dict) else {},
        article_overrides=raw.get("article_overrides", {}) if isinstance(raw.get("article_overrides"), dict) else {},
        selection_rules=raw.get("selection_rules", {}),
    )


@lru_cache(maxsize=1)
def load_runtime_limits_config() -> RuntimeLimitsConfig:
    source_path = _load_private_or_example(
        env_keys=("RUNTIME_LIMITS_PATH",),
        private_candidates=("config/runtime_limits.private.yaml",),
        example_candidates=("config/runtime_limits.example.yaml",),
        config_name="runtime limits config",
    )
    raw = _read_yaml(source_path)
    return RuntimeLimitsConfig(
        source_path=source_path,
        channels=raw.get("channels", {}),
        shared=raw.get("shared", {}),
    )


@lru_cache(maxsize=1)
def load_commerce_guardrail_config() -> CommerceGuardrailConfig:
    source_path = _load_private_or_example(
        env_keys=("COMMERCE_GUARDRAIL_PATH", "GUARDRAIL_CONFIG_PATH"),
        private_candidates=("config/guardrail.private.yaml",),
        example_candidates=("config/guardrail.example.yaml",),
        config_name="commerce guardrail config",
    )
    raw = _read_yaml(source_path)
    fallback = raw.get("fallback_responses", {}) if isinstance(raw.get("fallback_responses"), dict) else {}
    return CommerceGuardrailConfig(
        source_path=source_path,
        blocked_medical_diagnosis_patterns=tuple(raw.get("blocked_medical_diagnosis_patterns", [])),
        blocked_treatment_promise_patterns=tuple(raw.get("blocked_treatment_promise_patterns", [])),
        blocked_cure_patterns=tuple(raw.get("blocked_cure_patterns", [])),
        blocked_replacement_of_care_patterns=tuple(raw.get("blocked_replacement_of_care_patterns", [])),
        high_risk_patterns=tuple(item for item in raw.get("high_risk_patterns", []) if isinstance(item, dict)),
        caution_patterns=tuple(item for item in raw.get("caution_patterns", []) if isinstance(item, dict)),
        allowed_educational_claims=tuple(str(item).strip() for item in raw.get("allowed_educational_claims", []) if str(item).strip()),
        disallowed_medical_claims=tuple(str(item).strip() for item in raw.get("disallowed_medical_claims", []) if str(item).strip()),
        compliant_phrasing=tuple(item for item in raw.get("compliant_phrasing", []) if isinstance(item, dict)),
        safer_rewrite_examples=tuple(item for item in raw.get("safer_rewrite_examples", []) if isinstance(item, dict)),
        fallback_responses={key: normalize_localized_text(value, fallback="") for key, value in fallback.items()},
    )


def reload_product_helper_configs() -> None:
    load_questionnaire_config.cache_clear()
    load_constitution_config.cache_clear()
    load_knowledge_base_config.cache_clear()
    load_link_routing_config.cache_clear()
    load_runtime_limits_config.cache_clear()
    load_commerce_guardrail_config.cache_clear()
