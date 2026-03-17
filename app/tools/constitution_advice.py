from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.product_helper.catalog_links import localized_catalog_link
from app.product_helper.config import (
    ConstitutionConfig,
    KnowledgeBaseConfig,
    load_constitution_config,
    load_knowledge_base_config,
    load_questionnaire_config,
    reload_product_helper_configs,
)
from app.product_helper.content import load_catalog_bundle, reload_catalog_bundle
from app.product_helper.service import get_product_helper_service


@dataclass(frozen=True)
class ConstitutionScoringConfig:
    source_path: Path
    schema: dict[str, Any]
    constitutions: dict[str, dict[str, Any]]
    signals: dict[str, Any]
    free_text_signals: tuple[dict[str, Any], ...]
    output_policy: dict[str, Any]


@dataclass(frozen=True)
class HerbalAdviceConfig:
    source_path: Path
    conversation_modes: dict[str, Any]
    intents: tuple[dict[str, Any], ...]
    brand_scope: dict[str, Any]


def _map_constitution_config(config: ConstitutionConfig) -> ConstitutionScoringConfig:
    return ConstitutionScoringConfig(
        source_path=config.source_path,
        schema=config.schema,
        constitutions=config.constitutions,
        signals=config.signals,
        free_text_signals=config.free_text_signals,
        output_policy=config.output_policy,
    )


def _map_knowledge_base(config: KnowledgeBaseConfig) -> HerbalAdviceConfig:
    return HerbalAdviceConfig(
        source_path=config.source_path,
        conversation_modes=config.conversation_modes,
        intents=config.intents,
        brand_scope=config.brand_scope,
    )


@lru_cache(maxsize=1)
def load_constitution_scoring_config() -> ConstitutionScoringConfig:
    return _map_constitution_config(load_constitution_config())


@lru_cache(maxsize=1)
def load_herbal_advice_config() -> HerbalAdviceConfig:
    return _map_knowledge_base(load_knowledge_base_config())


def reload_constitution_advice_configs() -> None:
    load_constitution_scoring_config.cache_clear()
    load_herbal_advice_config.cache_clear()
    reload_product_helper_configs()
    reload_catalog_bundle()


def extract_recent_discomfort_options(_: HerbalAdviceConfig | None = None) -> tuple[dict[str, Any], ...]:
    questionnaire = load_questionnaire_config()
    field = next((item for item in questionnaire.fields if item["name"] == "recent_discomfort_multi"), None)
    if field is None:
        bundle = load_catalog_bundle()
        values = []
        for product in bundle.products:
            for discomfort in product.recent_discomforts:
                if discomfort not in values:
                    values.append(discomfort)
        return tuple({"value": item, "label": {"zh": item, "en": item}} for item in values)
    return tuple(field.get("options", []))


def extract_recent_discomfort_option_values(config: HerbalAdviceConfig | None = None) -> tuple[str, ...]:
    return tuple(str(item.get("value", "")).strip() for item in extract_recent_discomfort_options(config) if str(item.get("value", "")).strip())


def assess_constitution_and_recommend_herbs(query: str, profile: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> dict[str, Any]:
    preferred_language = (context or {}).get("preferred_language")
    service = get_product_helper_service()
    result = service.handle(
        user_id=str((context or {}).get("user_id", "tool-user")).strip() or "tool-user",
        text=query,
        preferred_language=preferred_language,
        channel=str((context or {}).get("channel", "web")).strip() or "web",
        history_text=str((context or {}).get("recent_history", "")).strip(),
    )

    constitutions = [
        {
            "constitution": candidate.label["zh"],
            "label": candidate.label,
            "score": candidate.score,
            "confidence": candidate.confidence,
            "evidence": list(candidate.evidence),
            "description": candidate.description,
        }
        for candidate in (result.constitution_assessment.candidates if result.constitution_assessment else ())
    ]
    product_rows = [
        {
            "id": recommendation.product.slug,
            "slug": recommendation.product.slug,
            "name": recommendation.product.name,
            "tagline": recommendation.product.tagline,
            "summary": recommendation.product.summary,
            "why": list(recommendation.why),
            "taste": recommendation.taste,
            "when_to_drink": recommendation.when_to_drink,
            "caution": recommendation.caution,
            "links": recommendation.product.links,
            "buy_link": localized_catalog_link(recommendation.product.links, result.language) or recommendation.product.buy_link,
            "ingredients": list(recommendation.product.ingredients),
        }
        for recommendation in result.product_recommendations
    ]
    link_rows = [
        {
            "id": entry.id,
            "type": entry.type,
            "slug": entry.slug,
            "title": {"zh": entry.zh_title, "en": entry.en_title},
            "url": entry.url,
            "tags": list(entry.tags),
        }
        for entry in result.support_links
    ]

    return {
        "ok": True,
        "tool": "assess_constitution_and_recommend_products",
        "intent": result.intent,
        "mode": result.mode,
        "language": result.language,
        "constitution_assessment": {
            "selected": constitutions,
            "summary": result.constitution_assessment.summary if result.constitution_assessment else {},
            "signal_summary": list(result.constitution_assessment.signal_summary) if result.constitution_assessment else [],
            "confidence": result.constitution_assessment.confidence if result.constitution_assessment else "low",
            "conservative_note": result.constitution_assessment.conservative_note if result.constitution_assessment else {},
        },
        "product_recommendations": product_rows,
        "herbal_recommendations": product_rows,
        "support_links": link_rows,
        "followup_questions": list(result.followup_questions),
        "matched_items": product_rows,
        "reasons": [{"kind": "intent", "value": result.intent}],
        "requires_company_append": False,
        "required_append_text": "",
        "safety_notes": list(result.safety_notes),
        "reply_preview": result.reply,
        "intake_state": result.intake_state,
    }
