from __future__ import annotations

import re
from dataclasses import replace
from functools import lru_cache

from app.i18n import normalize_language
from app.product_helper.config import (
    load_constitution_config,
    load_knowledge_base_config,
    load_link_routing_config,
    load_questionnaire_config,
    load_runtime_limits_config,
)
from app.product_helper.constitution import assess_constitutions
from app.product_helper.content import load_catalog_bundle
from app.product_helper.guardrails import (
    collect_caution_notes,
    detect_high_risk_response,
    enforce_domain_response_policy,
)
from app.product_helper.intake import extract_intake_from_text, merge_intake, next_followup_question, normalize_intake
from app.product_helper.intent_router import IntentRoute, route_intent
from app.product_helper.links import select_supporting_links
from app.product_helper.models import HelperResult
from app.product_helper.recommendation import rank_products
from app.product_helper.response_policy import compose_reply
from app.product_helper.session import get_product_helper_session_store


OUT_OF_SCOPE_REPLY = {
    "zh": "我主要帮助你做草本茶产品推荐、原料说明、送礼选择和品牌内容导览。像编程、法律、投资这类问题我就不展开了。",
    "en": "I mainly help with tea recommendations, ingredient education, gifting choices, and brand-related guidance. I won't be very useful for topics like coding, law, or investing.",
}


def _detect_language(text: str, preferred: str | None, fallback: str = "zh") -> str:
    explicit = normalize_language(preferred, default="")
    if explicit:
        return explicit
    raw = str(text or "")
    if re.search(r"[\u4e00-\u9fff]", raw):
        english_words = re.findall(r"[A-Za-z]{2,}", raw)
        if len(english_words) > 4 and len(re.findall(r"[\u4e00-\u9fff]", raw)) < 4:
            return "en"
        return "zh"
    if re.search(r"[A-Za-z]{2,}", raw):
        return "en"
    return fallback


def _runtime_channel_name(channel: str) -> str:
    return "wechat" if channel == "wechat" else "web"


def _trim_reply(text: str, channel: str) -> str:
    limits = load_runtime_limits_config()
    channel_limits = limits.channels.get(_runtime_channel_name(channel), {})
    max_chars = int(channel_limits.get("max_output_chars", 480) or 480)
    trim_suffix = str(limits.shared.get("trim_suffix", "…")).strip() or "…"
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max(0, max_chars - len(trim_suffix))].rstrip()}{trim_suffix}"


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    normalized = str(text or "").strip().lower()
    return any(term in normalized for term in terms)


def _has_contextual_product_reference(text: str) -> bool:
    return _contains_any(
        text,
        (
            "这款",
            "这个",
            "它",
            "this one",
            "this tea",
            "that one",
            "it",
        ),
    )


def _compose_followup(*, language: str, question: str, use_case: str) -> str:
    if language == "en":
        prefix = "One quick detail would help me narrow this down cleanly."
        if use_case == "gifting":
            prefix = "One quick detail would help me make the gift pick feel more on-point."
        return f"{prefix}\n\n{question}"
    prefix = "我只补一个小信息，就能把方向收得更准。"
    if use_case == "gifting":
        prefix = "我只补一个小信息，送礼会更容易选得稳。"
    return f"{prefix}\n\n{question}"


def _route_from_intake(route: IntentRoute, intake_state: dict[str, object]) -> IntentRoute:
    use_case = str(intake_state.get("use_case", "")).strip()
    selected_product_slug = str(intake_state.get("selected_product_slug", "")).strip()
    has_recent_discomfort = bool(intake_state.get("recent_discomfort_combined"))

    if use_case == "ingredient_learning" and selected_product_slug:
        return IntentRoute("product_detail", "product_detail", use_case, (selected_product_slug,), route.mentioned_ingredients)
    if route.intent == "general_brand_scope_qna" and use_case == "gifting":
        return IntentRoute("gifting_recommendation", "gifting_guide", use_case, route.mentioned_products, route.mentioned_ingredients)
    if route.intent == "general_brand_scope_qna" and use_case == "article_recommendation":
        return IntentRoute("article_request", "article_navigator", use_case, route.mentioned_products, route.mentioned_ingredients)
    if route.intent == "general_brand_scope_qna" and selected_product_slug:
        return IntentRoute("product_detail", "product_detail", "daily_wellness", (selected_product_slug,), route.mentioned_ingredients)
    if route.intent == "general_brand_scope_qna" and has_recent_discomfort:
        return IntentRoute("symptom_or_discomfort_guidance", "quick_recommendation", "recent_discomfort_guidance", route.mentioned_products, route.mentioned_ingredients)
    return route


def _apply_session_context(route: IntentRoute, session, text: str, intake_state: dict[str, object]) -> IntentRoute:
    selected_product_slug = str(intake_state.get("selected_product_slug", "")).strip()
    if selected_product_slug and selected_product_slug not in route.mentioned_products:
        route = replace(route, mentioned_products=(selected_product_slug,) + route.mentioned_products)

    if route.intent == "compare_products" and len(route.mentioned_products) < 2 and len(session.shortlisted_products) >= 2:
        return replace(route, mentioned_products=tuple(session.shortlisted_products[:2]))

    if route.intent == "product_detail" and route.mentioned_products:
        return route

    if _has_contextual_product_reference(text) and session.shortlisted_products:
        primary_slug = session.shortlisted_products[0]
        if route.intent in {"general_brand_scope_qna", "brewing_or_usage_question", "gifting_recommendation", "product_recommendation_direct"}:
            return IntentRoute("product_detail", "product_detail", route.use_case or session.current_use_case or "daily_wellness", (primary_slug,), route.mentioned_ingredients)
        if route.intent == "product_detail":
            return replace(route, mentioned_products=(primary_slug,))

    return route


class ProductHelperService:
    def __init__(self) -> None:
        self._sessions = get_product_helper_session_store()

    def handle(self, *, user_id: str, text: str, preferred_language: str | None = None, channel: str = "web", history_text: str = "") -> HelperResult:
        session = self._sessions.get(user_id)
        language = _detect_language(text, preferred_language, fallback=session.language or "zh")
        safety_notes = collect_caution_notes(text, language)

        high_risk_reply = detect_high_risk_response(text, language)
        if high_risk_reply:
            safe_reply = _trim_reply(high_risk_reply, channel)
            self._sessions.upsert(
                user_id,
                language=language,
                intake=session.intake,
                current_intent="high_risk_medical",
                current_use_case=session.current_use_case,
                last_question=text,
            )
            return HelperResult(
                language=language,
                intent="high_risk_medical",
                mode="fallback_safe",
                reply=safe_reply,
                needs_followup=False,
                followup_questions=(),
                intake_state=dict(session.intake),
                constitution_assessment=None,
                product_recommendations=(),
                support_links=(),
                safety_notes=safety_notes,
                metadata={"history_text": history_text, "allow_naturalization": False},
            )

        questionnaire = load_questionnaire_config()
        knowledge_base = load_knowledge_base_config()
        bundle = load_catalog_bundle()

        extracted_intake = extract_intake_from_text(text, questionnaire)
        merged_intake = merge_intake(session.intake, extracted_intake)
        intake_state = normalize_intake(merged_intake, questionnaire)

        route = route_intent(text=text, language=language, knowledge_base=knowledge_base)
        route = _route_from_intake(route, intake_state)
        route = _apply_session_context(route, session, text, intake_state)

        if route.use_case and not intake_state.get("use_case"):
            intake_state["use_case"] = route.use_case

        if route.intent == "out_of_scope":
            reply = _trim_reply(OUT_OF_SCOPE_REPLY[language], channel)
            self._sessions.upsert(
                user_id,
                language=language,
                intake=intake_state,
                current_intent=route.intent,
                current_use_case=route.use_case,
                last_question=text,
            )
            return HelperResult(
                language=language,
                intent=route.intent,
                mode=route.mode,
                reply=reply,
                needs_followup=False,
                followup_questions=(),
                intake_state=intake_state,
                constitution_assessment=None,
                product_recommendations=(),
                support_links=(),
                safety_notes=safety_notes,
                metadata={"route": route, "history_text": history_text, "allow_naturalization": False},
            )

        needs_constitution = route.intent in {
            "symptom_or_discomfort_guidance",
            "constitution_guidance",
            "product_recommendation_direct",
            "gifting_recommendation",
            "compare_products",
        }
        constitution_assessment = (
            assess_constitutions(
                query_text=text,
                intake=intake_state,
                config=load_constitution_config(),
                language=language,
            )
            if needs_constitution
            else None
        )

        sparse_intake = len([value for value in intake_state.values() if value not in ("", [], None)]) <= 1
        weak_constitution = not constitution_assessment or not constitution_assessment.candidates
        should_followup = route.intent in {"symptom_or_discomfort_guidance", "gifting_recommendation"} and sparse_intake and weak_constitution
        if route.intent == "gifting_recommendation" and any(
            value not in ("", [], None)
            for key, value in intake_state.items()
            if key in {"gift_target", "gift_budget_tier", "premium_preference", "flavor_preference"}
        ):
            should_followup = False
        if route.intent == "gifting_recommendation" and _contains_any(text, ("妈妈", "长辈", "女生", "女朋友", "闺蜜", "mom", "mother", "not too bitter", "高端", "premium")):
            should_followup = False

        if should_followup:
            followup = next_followup_question(questionnaire=questionnaire, intake=intake_state, intent=route.intent, language=language)
            reply = _trim_reply(_compose_followup(language=language, question=followup, use_case=route.use_case), channel)
            self._sessions.upsert(
                user_id,
                language=language,
                intake=intake_state,
                current_intent=route.intent,
                current_use_case=route.use_case,
                last_question=text,
            )
            return HelperResult(
                language=language,
                intent=route.intent,
                mode="intake_followup",
                reply=reply,
                needs_followup=True,
                followup_questions=(followup,),
                intake_state=intake_state,
                constitution_assessment=constitution_assessment,
                product_recommendations=(),
                support_links=(),
                safety_notes=safety_notes,
                metadata={"route": route, "history_text": history_text, "allow_naturalization": False},
            )

        recommendations = ()
        if route.intent in {
            "symptom_or_discomfort_guidance",
            "constitution_guidance",
            "product_recommendation_direct",
            "gifting_recommendation",
            "compare_products",
        }:
            recommendations = rank_products(
                products=bundle.products,
                constitution_assessment=constitution_assessment,
                intake=intake_state,
                query_text=text,
                intent=route.intent,
                use_case=route.use_case,
                language=language,
                preferred_slugs=route.mentioned_products,
            )

        links = select_supporting_links(
            bundle=bundle,
            config=load_link_routing_config(),
            language=language,
            intent=route.intent,
            use_case=route.use_case,
            product_recommendations=recommendations,
            mentioned_products=route.mentioned_products,
            mentioned_ingredients=route.mentioned_ingredients,
        )

        draft_reply = compose_reply(
            language=language,
            intent=route.intent,
            route=route,
            query_text=text,
            bundle=bundle,
            constitution_assessment=constitution_assessment,
            recommendations=recommendations,
            links=links,
            safety_notes=safety_notes,
        )
        draft_reply = enforce_domain_response_policy(draft_reply, language, fallback_text=draft_reply)
        final_reply = _trim_reply(draft_reply, channel)

        shortlist_products = list(route.mentioned_products[:2]) or [item.product.slug for item in recommendations[:2]]
        shortlist_ingredients = list(route.mentioned_ingredients[:3])
        shortlist_constitutions = [candidate.label["zh"] for candidate in constitution_assessment.candidates[:2]] if constitution_assessment else []
        self._sessions.upsert(
            user_id,
            language=language,
            intake=intake_state,
            current_intent=route.intent,
            current_use_case=route.use_case,
            shortlisted_products=shortlist_products,
            shortlisted_ingredients=shortlist_ingredients,
            last_constitutions=shortlist_constitutions,
            last_question=text,
        )

        return HelperResult(
            language=language,
            intent=route.intent,
            mode=route.mode,
            reply=final_reply,
            needs_followup=False,
            followup_questions=(),
            intake_state=intake_state,
            constitution_assessment=constitution_assessment,
            product_recommendations=recommendations,
            support_links=links,
            safety_notes=safety_notes,
            metadata={
                "route": route,
                "history_text": history_text,
                "allow_naturalization": route.intent in {
                    "symptom_or_discomfort_guidance",
                    "constitution_guidance",
                    "product_recommendation_direct",
                    "gifting_recommendation",
                    "product_detail",
                    "ingredient_explanation",
                    "compare_products",
                    "article_request",
                    "wellness_education_in_scope",
                    "general_brand_scope_qna",
                },
            },
        )


@lru_cache(maxsize=1)
def get_product_helper_service() -> ProductHelperService:
    return ProductHelperService()
