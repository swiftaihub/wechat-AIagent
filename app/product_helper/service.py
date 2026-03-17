from __future__ import annotations

import re
from dataclasses import replace
from functools import lru_cache
from typing import Any

from app.memory_store import MemoryMessage
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
from app.product_helper.intent_router import DomainClassification, IntentRoute, classify_domain_intent, route_intent
from app.product_helper.links import select_supporting_links
from app.product_helper.models import HelperResult
from app.product_helper.recommendation import rank_products
from app.product_helper.response_policy import compose_reply
from app.product_helper.session import get_product_helper_session_store
from app.product_helper.validation import validate_product_recommendations, validate_support_links
from app.text_trimming import smart_trim_to_limit


OUT_OF_SCOPE_REPLY = {
    "zh": "我主要帮助你做草本茶产品推荐、原料说明、送礼选择和品牌内容导览。像编程、法律、投资这类问题我就不展开了。",
    "en": "I mainly help with tea recommendations, ingredient education, gifting choices, and brand-related guidance. I won't be very useful for topics like coding, law, or investing.",
}
_CONFIRM_TERMS = (
    "yes",
    "yeah",
    "yep",
    "ok",
    "okay",
    "sure",
    "sounds good",
    "please do",
    "need",
    "需要",
    "好",
    "好的",
    "行",
    "可以",
    "要",
    "麻烦你了",
)
_REJECT_TERMS = (
    "no",
    "nope",
    "not now",
    "no thanks",
    "不用",
    "不需要",
    "先不用",
    "不用了",
    "算了",
    "不了",
)


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
    if _runtime_channel_name(channel) == "web":
        max_chars = max(max_chars, 1400)
        if normalized.count("\n- ") >= 2:
            max_chars = max(max_chars, 4500)
        elif "](" in normalized:
            max_chars = max(max_chars, 1600)
    if len(normalized) <= max_chars:
        return normalized
    return smart_trim_to_limit(normalized, max_chars=max_chars, trim_suffix=trim_suffix)


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


def _compose_out_of_scope_reply(language: str, classification: DomainClassification) -> str:
    if language == "en":
        if "math" in classification.reason:
            return "I can help with tea, ingredients, and wellness. If you want, tell me how you’ve been feeling and I can suggest a tea."
        return "I can help with tea, ingredients, and gifting. If you want, I can help you choose a tea."

    if "math" in classification.reason:
        return "我可以帮你看草本茶、原料和送礼方向。要是愿意，也可以告诉我最近的状态，我来帮你选茶。"
    return "我可以帮你看草本茶、原料和送礼方向。要是愿意，我也可以按你现在的状态帮你选茶。"


def _normalize_short_reply(text: str) -> str:
    return re.sub(r"[\s!,.?。！？]+", " ", str(text or "").strip().lower()).strip()


def _detect_confirmation_intent(text: str) -> str:
    normalized = _normalize_short_reply(text)
    if not normalized:
        return ""
    if normalized in _CONFIRM_TERMS:
        return "confirm"
    if normalized in _REJECT_TERMS:
        return "reject"
    return ""


def _gift_target_label(gift_target: str, language: str) -> str:
    normalized = str(gift_target or "").strip().lower()
    if language == "en":
        return {
            "mother": "your mom",
            "father": "your dad",
            "partner": "your partner",
            "friend_colleague": "your friend",
        }.get(normalized, "someone you care about")
    return {
        "mother": "妈妈",
        "father": "爸爸",
        "partner": "伴侣",
        "friend_colleague": "朋友",
    }.get(normalized, "对方")


def _infer_gift_target(text: str, intake_state: dict[str, Any]) -> str:
    explicit = str(intake_state.get("gift_target", "")).strip().lower()
    if explicit:
        return explicit
    normalized = str(text or "").strip().lower()
    if any(term in normalized for term in ("mom", "mother", "妈妈", "妈", "长辈女性")):
        return "mother"
    if any(term in normalized for term in ("dad", "father", "爸爸", "爸", "长辈男性")):
        return "father"
    if any(term in normalized for term in ("partner", "girlfriend", "boyfriend", "伴侣", "女朋友", "男朋友")):
        return "partner"
    if any(term in normalized for term in ("friend", "colleague", "朋友", "同事", "闺蜜")):
        return "friend_colleague"
    return ""


def _gift_card_offer(language: str) -> str:
    if language == "en":
        return "If you want, I can also draft a short handwritten gift card message for it."
    return "如果你愿意，我也可以顺手帮你写一版手写卡片文案。"


def _gift_card_clarification(language: str) -> str:
    if language == "en":
        return "Just to confirm — would you like me to write a short gift card message for you?"
    return "我确认一下，你是想让我直接帮你写一版手写卡片文案吗？"


def _build_pending_action_context(
    *,
    language: str,
    query_text: str,
    intake_state: dict[str, Any],
    recommendations,
) -> dict[str, Any]:
    product = recommendations[0].product if recommendations else None
    return {
        "action": "gift_card_message",
        "gift_target": _infer_gift_target(query_text, intake_state),
        "product_slug": product.slug if product else "",
        "product_name": product.name.get(language, "") if product else "",
        "product_name_zh": product.name["zh"] if product else "",
        "product_name_en": product.name["en"] if product else "",
    }


def _compose_gift_card_message(language: str, pending_context: dict[str, Any]) -> str:
    product_name = str(
        pending_context.get("product_name")
        or pending_context.get("product_name_en")
        or pending_context.get("product_name_zh")
        or ""
    ).strip()
    recipient = _gift_target_label(str(pending_context.get("gift_target", "")).strip(), language)

    if language == "en":
        opening = "Of course — here is a short handwritten card version:"
        body = (
            f"\"I picked {product_name or 'this tea'} for {recipient} in the hope that it brings a calmer, more cared-for moment to the day. "
            "Wishing you steady energy, gentle pauses, and a little more ease in the middle of everything. "
            "I hope you enjoy it.\""
        )
        close = "If you want, I can also rewrite it in a warmer, more formal, or more concise tone."
        return f"{opening}\n\n{body}\n\n{close}"

    opening = "可以，给你一版更适合手写的小卡片文案："
    body = (
        f"“这次特地挑了{product_name or '这份茶礼'}送给{recipient}，希望它能替我带去一点温柔的照顾。"
        "愿你在忙碌里也记得慢下来，好好喝茶、好好休息，日常多一点松弛和顺心。希望你会喜欢这份心意。”"
    )
    close = "如果你愿意，我也可以再帮你改成更温柔一点、正式一点，或者更短一点的版本。"
    return f"{opening}\n\n{body}\n\n{close}"


def _pending_action_response(
    *,
    language: str,
    session,
    text: str,
    channel: str,
) -> tuple[HelperResult | None, str | None, dict[str, Any] | None]:
    if session.pending_action != "gift_card_message":
        return None, None, None

    confirmation = _detect_confirmation_intent(text)
    if confirmation == "confirm":
        reply = _trim_reply(_compose_gift_card_message(language, dict(session.pending_context)), channel)
        return (
            HelperResult(
                language=language,
                intent=session.current_intent or "gifting_recommendation",
                mode="pending_action_resolution",
                reply=reply,
                needs_followup=False,
                followup_questions=(),
                intake_state=dict(session.intake),
                constitution_assessment=None,
                product_recommendations=(),
                support_links=(),
                safety_notes=(),
                metadata={"pending_action_resolved": True, "grounding_required": False, "allow_naturalization": False},
            ),
            "",
            {},
        )
    if confirmation == "reject":
        reply = (
            "好的，那我先不写卡片文案了。如果你愿意，我也可以再帮你把送礼理由润色成一句更自然的话。"
            if language == "zh"
            else "No problem — I’ll skip the card note for now. If you want, I can still help polish a one-line gifting reason."
        )
        return (
            HelperResult(
                language=language,
                intent=session.current_intent or "gifting_recommendation",
                mode="pending_action_resolution",
                reply=_trim_reply(reply, channel),
                needs_followup=False,
                followup_questions=(),
                intake_state=dict(session.intake),
                constitution_assessment=None,
                product_recommendations=(),
                support_links=(),
                safety_notes=(),
                metadata={"pending_action_resolved": True, "grounding_required": False, "allow_naturalization": False},
            ),
            "",
            {},
        )
    if len(str(text or "").strip()) <= 20:
        clarification = _gift_card_clarification(language)
        return (
            HelperResult(
                language=language,
                intent=session.current_intent or "gifting_recommendation",
                mode="pending_action_clarify",
                reply=_trim_reply(clarification, channel),
                needs_followup=True,
                followup_questions=(clarification,),
                intake_state=dict(session.intake),
                constitution_assessment=None,
                product_recommendations=(),
                support_links=(),
                safety_notes=(),
                metadata={"pending_action_resolved": False, "grounding_required": False, "allow_naturalization": False},
            ),
            session.pending_action,
            dict(session.pending_context),
        )
    return None, None, None


def _context_reconnect_reply(*, language: str, session) -> str:
    if not str(session.current_intent or "").strip():
        return ""

    if session.current_intent == "gifting_recommendation":
        if language == "en":
            return "Do you want to keep narrowing the gift, or should I write the card message next?"
        return "你是想继续收窄送礼推荐，还是要我直接写卡片文案？"

    primary_slug = session.shortlisted_products[0] if session.shortlisted_products else ""
    if primary_slug:
        bundle = load_catalog_bundle()
        product = bundle.products_by_slug.get(primary_slug)
        product_name = (
            product.name.get(language, "")
            if product is not None
            else ""
        )
        if language == "en":
            if product_name:
                return f"Do you want to keep looking at {product_name}, or should I focus on ingredients, taste, or who it fits?"
            return "Do you want me to focus on ingredients, taste, or who it fits?"
        if product_name:
            return f"你是想继续看{product_name}，还是想让我重点讲原料、口感，或适合谁喝？"
        return "你是想继续看这款茶，还是想让我重点讲原料、口感，或适合谁喝？"

    if session.current_intent in {"symptom_or_discomfort_guidance", "product_recommendation_direct", "ingredient_explanation", "product_detail"}:
        if language == "en":
            return "Do you want me to keep going from the current tea question, or focus on ingredients, taste, or a recommendation?"
        return "你是想继续接着现在这条茶的问题往下聊，还是想让我重点讲原料、口感，或直接推荐？"

    return ""


def _previous_user_messages(history_messages: tuple[MemoryMessage, ...], current_text: str) -> tuple[str, ...]:
    current_normalized = str(current_text or "").strip()
    user_messages = [str(message.content or "").strip() for message in history_messages if message.role == "user" and str(message.content or "").strip()]
    if user_messages and user_messages[-1] == current_normalized:
        user_messages.pop()
    return tuple(user_messages)


def _previous_assistant_message(history_messages: tuple[MemoryMessage, ...]) -> str:
    for message in reversed(history_messages):
        if message.role == "assistant" and str(message.content or "").strip():
            return str(message.content).strip()
    return ""


def _looks_like_followup(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if len(normalized) <= 36 and normalized.endswith("?"):
        return True
    followup_terms = (
        "this one",
        "that one",
        "it",
        "what should i drink",
        "what can i drink",
        "what about",
        "tell me more",
        "ingredients",
        "what's in it",
        "介绍一下",
        "展开讲讲",
        "这款",
        "这个",
        "它",
        "那款",
        "喝什么",
        "怎么喝",
        "适合我吗",
        "原料",
        "成分",
    )
    return any(term in normalized for term in followup_terms)


def _build_analysis_text(text: str, history_messages: tuple[MemoryMessage, ...]) -> str:
    current_text = str(text or "").strip()
    if not current_text:
        return ""
    previous_users = _previous_user_messages(history_messages, current_text)
    if not previous_users:
        return current_text
    if not _looks_like_followup(current_text):
        return current_text
    context_parts = list(previous_users[-2:]) + [current_text]
    return "\n".join(part for part in context_parts if part).strip()


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

    def handle(
        self,
        *,
        user_id: str,
        text: str,
        preferred_language: str | None = None,
        channel: str = "web",
        history_text: str = "",
        history_messages: tuple[MemoryMessage, ...] = (),
        loop_detected: bool = False,
    ) -> HelperResult:
        session = self._sessions.get(user_id)
        language = _detect_language(text, preferred_language, fallback=session.language or "zh")
        analysis_text = _build_analysis_text(text, history_messages)
        contextual_followup = bool(analysis_text and analysis_text != str(text or "").strip())
        previous_assistant_text = _previous_assistant_message(history_messages)
        safety_notes = collect_caution_notes(analysis_text or text, language)
        pending_result, next_pending_action, next_pending_context = _pending_action_response(
            language=language,
            session=session,
            text=text,
            channel=channel,
        )
        if pending_result is not None:
            self._sessions.upsert(
                user_id,
                language=language,
                intake=session.intake,
                current_intent=pending_result.intent,
                current_use_case=session.current_use_case,
                pending_action=next_pending_action,
                pending_context=next_pending_context,
                last_user_need=session.last_user_need or session.current_use_case,
                last_question=text,
            )
            return pending_result

        confirmation_intent = _detect_confirmation_intent(text)
        if confirmation_intent and session.current_intent == "gifting_recommendation" and session.shortlisted_products:
            clarification_context = dict(session.pending_context or {})
            if not clarification_context:
                bundle = load_catalog_bundle()
                product = bundle.products_by_slug.get(session.shortlisted_products[0])
                clarification_context = {
                    "action": "gift_card_message",
                    "gift_target": _infer_gift_target(session.last_question, session.intake),
                    "product_slug": product.slug if product else "",
                    "product_name": product.name.get(language, "") if product else "",
                    "product_name_zh": product.name["zh"] if product else "",
                    "product_name_en": product.name["en"] if product else "",
                }
            clarification = _trim_reply(_gift_card_clarification(language), channel)
            self._sessions.upsert(
                user_id,
                language=language,
                intake=session.intake,
                current_intent=session.current_intent,
                current_use_case=session.current_use_case,
                pending_action="gift_card_message",
                pending_context=clarification_context,
                last_user_need=session.last_user_need or "gifting",
                last_question=text,
            )
            return HelperResult(
                language=language,
                intent="gifting_recommendation",
                mode="pending_action_clarify",
                reply=clarification,
                needs_followup=True,
                followup_questions=(clarification,),
                intake_state=dict(session.intake),
                constitution_assessment=None,
                product_recommendations=(),
                support_links=(),
                safety_notes=(),
                metadata={"pending_action_resolved": False, "grounding_required": False, "allow_naturalization": False},
            )

        high_risk_reply = detect_high_risk_response(text, language)
        if high_risk_reply:
            safe_reply = _trim_reply(high_risk_reply, channel)
            self._sessions.upsert(
                user_id,
                language=language,
                intake=session.intake,
                current_intent="high_risk_medical",
                current_use_case=session.current_use_case,
                pending_action="",
                pending_context={},
                last_user_need=session.last_user_need,
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
                metadata={
                    "history_text": history_text,
                    "analysis_text": analysis_text,
                    "previous_assistant_text": previous_assistant_text,
                    "allow_naturalization": False,
                },
            )

        questionnaire = load_questionnaire_config()
        knowledge_base = load_knowledge_base_config()
        bundle = load_catalog_bundle()

        extracted_intake = extract_intake_from_text(analysis_text or text, questionnaire)
        merged_intake = merge_intake(session.intake, extracted_intake)
        intake_state = normalize_intake(merged_intake, questionnaire)

        route = route_intent(text=analysis_text or text, language=language, knowledge_base=knowledge_base)
        route = _route_from_intake(route, intake_state)
        route = _apply_session_context(route, session, text, intake_state)
        intent_classification = classify_domain_intent(text=analysis_text or text, route=route)
        grounding_required = intent_classification.label in {"PRODUCT_RECOMMENDATION", "INGREDIENT_QUERY"} or route.intent == "article_request"

        if route.use_case and not intake_state.get("use_case"):
            intake_state["use_case"] = route.use_case

        if not intent_classification.allowed or route.intent == "out_of_scope":
            if (
                intent_classification.reason == "unsupported_topic"
                and not session.pending_action
                and (contextual_followup or (session.current_intent and len(str(text or "").strip()) <= 20))
            ):
                reconnect_reply = _context_reconnect_reply(language=language, session=session)
                if reconnect_reply:
                    reply = _trim_reply(reconnect_reply, channel)
                    self._sessions.upsert(
                        user_id,
                        language=language,
                        intake=intake_state,
                        current_intent=session.current_intent or "general_brand_scope_qna",
                        current_use_case=session.current_use_case,
                        pending_action="",
                        pending_context={},
                        last_user_need=session.last_user_need or session.current_use_case,
                        shortlisted_products=list(session.shortlisted_products),
                        shortlisted_ingredients=list(session.shortlisted_ingredients),
                        last_constitutions=list(session.last_constitutions),
                        last_question=text,
                    )
                    return HelperResult(
                        language=language,
                        intent=session.current_intent or "general_brand_scope_qna",
                        mode="context_reconnect",
                        reply=reply,
                        needs_followup=True,
                        followup_questions=(reply,),
                        intake_state=intake_state,
                        constitution_assessment=None,
                        product_recommendations=(),
                        support_links=(),
                        safety_notes=safety_notes,
                        metadata={
                            "route": route,
                            "intent_classification": intent_classification.label,
                            "history_text": history_text,
                            "analysis_text": analysis_text,
                            "previous_assistant_text": previous_assistant_text,
                            "grounding_required": False,
                            "allow_naturalization": False,
                        },
                    )
            reply = _trim_reply(_compose_out_of_scope_reply(language, intent_classification), channel)
            self._sessions.upsert(
                user_id,
                language=language,
                intake=intake_state,
                current_intent="out_of_scope",
                current_use_case=route.use_case,
                pending_action="",
                pending_context={},
                last_user_need=session.last_user_need,
                last_question=text,
            )
            return HelperResult(
                language=language,
                intent="out_of_scope",
                mode="fallback_safe",
                reply=reply,
                needs_followup=False,
                followup_questions=(),
                intake_state=intake_state,
                constitution_assessment=None,
                product_recommendations=(),
                support_links=(),
                safety_notes=safety_notes,
                metadata={
                    "route": route,
                    "intent_classification": intent_classification.label,
                    "history_text": history_text,
                    "analysis_text": analysis_text,
                    "previous_assistant_text": previous_assistant_text,
                    "grounding_required": False,
                    "allow_naturalization": False,
                },
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
                query_text=analysis_text or text,
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
        if contextual_followup and route.intent in {"symptom_or_discomfort_guidance", "product_recommendation_direct"}:
            should_followup = False
        if loop_detected and route.intent in {"symptom_or_discomfort_guidance", "product_recommendation_direct"}:
            should_followup = False
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
                pending_action="",
                pending_context={},
                last_user_need=route.use_case or session.last_user_need,
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
                metadata={
                    "route": route,
                    "history_text": history_text,
                    "analysis_text": analysis_text,
                    "previous_assistant_text": previous_assistant_text,
                    "allow_naturalization": False,
                },
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
                query_text=analysis_text or text,
                intent=route.intent,
                use_case=route.use_case,
                language=language,
                preferred_slugs=route.mentioned_products,
            )
            recommendations = validate_product_recommendations(recommendations)

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
        links = validate_support_links(links)

        draft_reply = compose_reply(
            language=language,
            intent=route.intent,
            route=route,
            query_text=analysis_text or text,
            bundle=bundle,
            constitution_assessment=constitution_assessment,
            recommendations=recommendations,
            links=links,
            safety_notes=safety_notes,
            previous_assistant_text=previous_assistant_text,
            loop_detected=loop_detected,
        )
        next_pending_action = ""
        next_pending_context: dict[str, Any] = {}
        if route.intent == "gifting_recommendation" and recommendations:
            offer = _gift_card_offer(language)
            if offer not in draft_reply:
                draft_reply = f"{draft_reply}\n\n{offer}"
            next_pending_action = "gift_card_message"
            next_pending_context = _build_pending_action_context(
                language=language,
                query_text=analysis_text or text,
                intake_state=intake_state,
                recommendations=recommendations,
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
            pending_action=next_pending_action,
            pending_context=next_pending_context,
            last_user_need=route.use_case or intent_classification.label.lower(),
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
                "intent_classification": intent_classification.label,
                "history_text": history_text,
                "analysis_text": analysis_text,
                "previous_assistant_text": previous_assistant_text,
                "grounding_required": grounding_required,
                "pending_action": next_pending_action,
                "allow_naturalization": route.intent in {"wellness_education_in_scope", "general_brand_scope_qna"} and not grounding_required,
            },
        )


@lru_cache(maxsize=1)
def get_product_helper_service() -> ProductHelperService:
    return ProductHelperService()
