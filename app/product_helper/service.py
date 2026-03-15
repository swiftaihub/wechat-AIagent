from __future__ import annotations

import re
from functools import lru_cache

from app.i18n import normalize_language
from app.product_helper.config import (
    load_commerce_guardrail_config,
    load_constitution_config,
    load_knowledge_base_config,
    load_link_routing_config,
    load_questionnaire_config,
    load_runtime_limits_config,
)
from app.product_helper.constitution import assess_constitutions
from app.product_helper.content import load_catalog_bundle
from app.product_helper.intake import extract_intake_from_text, merge_intake, next_followup_question, normalize_intake
from app.product_helper.intent_router import IntentRoute, route_intent
from app.product_helper.links import select_supporting_links
from app.product_helper.models import HelperResult
from app.product_helper.recommendation import rank_products
from app.product_helper.session import get_product_helper_session_store


HIGH_RISK_FALLBACK = {
    "zh": "你提到的情况有急性风险，这类情况不适合用茶来判断或拖一拖。请尽快联系急救或及时就医。",
    "en": "What you described can carry acute risk, so this is not something to manage through tea recommendations. Please seek urgent medical care right away.",
}
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


def _high_risk_match(text: str) -> bool:
    guardrail = load_commerce_guardrail_config()
    normalized = str(text or "").strip().lower()
    for item in guardrail.high_risk_patterns:
        patterns = [str(pattern).strip() for pattern in item.get("patterns", []) if str(pattern).strip()]
        if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns):
            return True
    return False


def _caution_notes(text: str, language: str) -> tuple[str, ...]:
    guardrail = load_commerce_guardrail_config()
    normalized = str(text or "").strip().lower()
    notes: list[str] = []
    for item in guardrail.caution_patterns:
        patterns = [str(pattern).strip() for pattern in item.get("patterns", []) if str(pattern).strip()]
        if any(re.search(pattern, normalized, re.IGNORECASE) for pattern in patterns):
            message = item.get("message", {})
            selected = message.get(language) or message.get("zh") or message.get("en") or ""
            if selected:
                notes.append(selected)
    return tuple(dict.fromkeys(notes))


def _localized_name(zh: str, en: str, language: str) -> str:
    return f"{en} ({zh})" if language == "en" else f"{zh} / {en}"


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


def _gifting_has_context(text: str, intake_state: dict[str, object]) -> bool:
    if any(
        value not in ("", [], None)
        for key, value in intake_state.items()
        if key in {"gift_target", "gift_budget_tier", "premium_preference", "flavor_preference"}
    ):
        return True
    return _contains_any(
        text,
        (
            "妈妈",
            "长辈",
            "女生",
            "女朋友",
            "闺蜜",
            "for her",
            "girlfriend",
            "mom",
            "mother",
            "not too bitter",
            "不想太苦",
            "premium",
            "高端",
            "体面",
        ),
    )


def _compose_followup(*, language: str, question: str, use_case: str) -> str:
    if language == "en":
        prefix = "I can narrow this down more cleanly with one quick detail."
        if use_case == "gifting":
            prefix = "I can make the gift recommendation feel more on-point with one quick detail."
        return f"{prefix}\n\n{question}"
    prefix = "我可以先补一个小信息，让推荐更贴一点。"
    if use_case == "gifting":
        prefix = "我先补一个小信息，送礼会更容易选得准。"
    return f"{prefix}\n\n{question}"


def _render_links(language: str, links: tuple) -> str:
    if not links:
        return ""
    lines = []
    for entry in links:
        title = entry.en_title if language == "en" else entry.zh_title
        lines.append(f"- {title}: {entry.url}")
    lead = "Useful next step:" if language == "en" and len(lines) == 1 else "Useful next steps:"
    if language == "zh":
        lead = "下一步可以先看："
    return f"{lead}\n" + "\n".join(lines)


def _render_product_recommendation(
    *,
    language: str,
    intent: str,
    route: IntentRoute,
    query_text: str,
    recommendations,
    constitution_assessment,
    links,
    safety_notes: tuple[str, ...],
) -> str:
    if not recommendations:
        return (
            "我先不急着硬推产品。你可以告诉我最近更偏累、口干、怕冷、饭后困重，还是主要是送礼场景，我再帮你收得更准。"
            if language == "zh"
            else "I would not force a product pick yet. If you tell me whether this is more about fatigue, dryness, cold sensitivity, post-meal heaviness, or gifting, I can narrow it down more cleanly."
        )

    lead = recommendations[0]
    lines: list[str] = []
    normalized_query = str(query_text or "").strip().lower()
    if intent == "gifting_recommendation":
        if language == "en":
            if _contains_any(normalized_query, ("mom", "mother", "parent")):
                opener = f"If this is for your mom or another older family member, I would lean first toward {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}."
            elif _contains_any(normalized_query, ("for her", "girlfriend", "women", "woman", "female")):
                opener = f"If you want something that feels elegant and easy to enjoy, I would lean first toward {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}."
            elif _contains_any(normalized_query, ("not too bitter", "less bitter")):
                opener = f"If you want the gift to feel polished without tasting too bitter, I would start with {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}."
            else:
                opener = f"For a refined gifting direction, I would start with {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}."
        else:
            if _contains_any(normalized_query, ("妈妈", "长辈")):
                opener = f"如果是送妈妈或长辈，我会先偏向 {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}。"
            elif _contains_any(normalized_query, ("女生", "女朋友", "闺蜜")):
                opener = f"如果你想送给女生、又希望精致感和接受度都在线，我会先偏向 {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}。"
            elif _contains_any(normalized_query, ("不想太苦",)):
                opener = f"如果你想送得体面、又不想太苦，我会先偏向 {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}。"
            else:
                opener = f"如果你想先从体面、好入口的送礼方向来选，我会先偏向 {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}。"
    elif intent == "compare_products":
        opener = "这两类茶的气质不太一样，我先帮你把重点差别收一下。" if language == "zh" else "These teas have a different overall feel, so here is the cleanest way to separate them."
    else:
        if language == "en":
            if _contains_any(normalized_query, ("dry", "dryness", "staying up late", "late night")):
                opener = f"If the more obvious pattern lately is dryness after late nights, I would lean first toward {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}."
            elif _contains_any(normalized_query, ("fatigue", "slow recovery", "low energy", "tired")):
                opener = f"If the bigger issue lately is low energy and slow recovery, I would lean first toward {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}."
            elif constitution_assessment and constitution_assessment.candidates:
                opener = f"You seem to lean more toward {constitution_assessment.candidates[0].label['en']}, so I would lean first toward {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}."
            else:
                opener = f"I would lean first toward {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}."
        else:
            if _contains_any(normalized_query, ("口干", "干", "熬夜")):
                opener = f"如果你最近更明显的是口干、偏燥或熬夜后的状态，我会先偏向 {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}。"
            elif _contains_any(normalized_query, ("累", "疲劳", "恢复慢", "说话都懒")):
                opener = f"如果你最近更明显的是累、气短感或恢复慢，我会先偏向 {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}。"
            elif constitution_assessment and constitution_assessment.candidates:
                opener = f"你现在更偏向 {constitution_assessment.candidates[0].label['zh']} 这一边，我会先偏向 {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}。"
            else:
                opener = f"我会先偏向 {_localized_name(lead.product.name['zh'], lead.product.name['en'], language)}。"
    lines.append(opener)

    for recommendation in recommendations:
        name = _localized_name(recommendation.product.name["zh"], recommendation.product.name["en"], language)
        body = recommendation.why[0] if recommendation.why else ""
        taste = recommendation.taste
        if language == "zh":
            lines.append(f"{name}：{body} 口感会更偏 {taste}。")
        else:
            lines.append(f"{name}: {body} Taste-wise, expect {taste}.")
        if len(recommendations) == 1:
            lines.append(recommendation.when_to_drink)
        if recommendation.caution:
            lines.append(f"温和提醒：{recommendation.caution}" if language == "zh" else f"Gentle note: {recommendation.caution}")

    if safety_notes:
        lines.extend(safety_notes[:1])
    link_block = _render_links(language, links)
    if link_block:
        lines.append(link_block)
    return "\n\n".join(line for line in lines if line.strip())


def _render_compare_answer(language: str, route: IntentRoute, recommendations, links) -> str:
    if len(route.mentioned_products) < 2:
        return _render_product_recommendation(
            language=language,
            intent="compare_products",
            route=route,
            query_text="",
            recommendations=recommendations,
            constitution_assessment=None,
            links=links,
            safety_notes=(),
        )

    bundle = load_catalog_bundle()
    first = bundle.products_by_slug.get(route.mentioned_products[0])
    second = bundle.products_by_slug.get(route.mentioned_products[1])
    if not first or not second:
        return _render_product_recommendation(
            language=language,
            intent="compare_products",
            route=route,
            query_text="",
            recommendations=recommendations,
            constitution_assessment=None,
            links=links,
            safety_notes=(),
        )

    if language == "en":
        text = (
            f"{_localized_name(first.name['zh'], first.name['en'], language)} feels more like {first.tagline['en'].lower()}.\n\n"
            f"{_localized_name(second.name['zh'], second.name['en'], language)} is more about {second.tagline['en'].lower()}.\n\n"
            f"If you want elegance and premium presentation, I would lean more toward {second.name['en'] if second.price > first.price else first.name['en']}. "
            f"If you want a gentler everyday entry, the other one is easier."
        )
    else:
        text = (
            f"{_localized_name(first.name['zh'], first.name['en'], language)} 更像 {first.tagline['zh']}。\n\n"
            f"{_localized_name(second.name['zh'], second.name['en'], language)} 则更偏 {second.tagline['zh']}。\n\n"
            f"如果你更看重体面感和高级感，我会更偏向 {second.name['zh'] if second.price > first.price else first.name['zh']}；如果你想选一款更日常、入口更轻松的，另一款会更稳。"
        )
    link_block = _render_links(language, links)
    return text if not link_block else f"{text}\n\n{link_block}"


def _render_ingredient_answer(language: str, ingredient_slug: str, links) -> str:
    bundle = load_catalog_bundle()
    ingredient = bundle.ingredients_by_slug.get(ingredient_slug)
    if not ingredient:
        return "我可以继续帮你看这味原料更适合哪类茶饮场景。" if language == "zh" else "I can still help narrow down which kind of tea this ingredient is best suited for."
    summary = ingredient.summary["en"] if language == "en" else ingredient.summary["zh"]
    flavor = ingredient.flavor_profile["en"] if language == "en" else ingredient.flavor_profile["zh"]
    traditional = ingredient.traditional_use["en"] if language == "en" else ingredient.traditional_use["zh"]
    if language == "en":
        text = (
            f"{_localized_name(ingredient.name['zh'], ingredient.name['en'], language)} is often used in a wellness-tea setting because {summary.lower()} "
            f"It tends to taste {flavor}, and it is commonly seen in blends where {traditional.lower()}"
        )
    else:
        text = (
            f"{_localized_name(ingredient.name['zh'], ingredient.name['en'], language)} 放在草本茶语境里，通常会因为 {summary} 而被选进配方。"
            f"口感上会偏 {flavor}，也常见于 {traditional}"
        )
    caution = ingredient.cautions["en"] if language == "en" else ingredient.cautions["zh"]
    link_block = _render_links(language, links)
    return f"{text}\n\n{caution}" + (f"\n\n{link_block}" if link_block else "")


def _bucket_products(bundle, desired_tags: tuple[str, ...], limit: int = 2) -> tuple:
    matches = []
    for product in bundle.products:
        tags = set(product.benefit_tags) | set(product.extra_tags)
        if tags & set(desired_tags):
            matches.append(product)
        if len(matches) >= limit:
            break
    return tuple(matches)


def _render_product_catalog_answer(language: str, bundle, route: IntentRoute, links) -> str:
    energy = _bucket_products(bundle, ("qi-support", "restorative", "overwork-support"), 2)
    dryness = _bucket_products(bundle, ("dryness-relief", "night-recovery", "yin-support"), 2)
    cooling = _bucket_products(bundle, ("cooling", "light-cooling", "damp-heat-balance", "eye-comfort"), 2)
    lifestyle = _bucket_products(bundle, ("mood-support", "qi-move", "digestive-comfort", "post-meal"), 2)
    gifting = _bucket_products(bundle, ("premium", "gift-worthy", "beauty-wellness", "circulation-support"), 2)

    def format_names(products: tuple) -> str:
        names = [_localized_name(item.name["zh"], item.name["en"], language) for item in products]
        if language == "en":
            return ", ".join(names)
        return "、".join(names)

    if route.use_case == "gifting":
        core = gifting or energy
        if language == "en":
            lines = [
                "If you want the gift-ready part of the lineup first, these are the ones I would start from:",
                f"Premium gifting: {format_names(core)}",
                "If you want, I can narrow that further into elegant floral, more restorative, or easier everyday gifting.",
            ]
        else:
            lines = [
                "如果你是想先看送礼向的茶单，我会先从这几款开始：",
                f"礼赠精选：{format_names(core)}",
                "如果你愿意，我还可以继续帮你缩成更偏花香体面、偏高阶滋养，或偏稳妥好入口这几种方向。",
            ]
    else:
        if language == "en":
            lines = [
                "Here is the current tea lineup in the cleanest way to read it:",
                f"Energy and recovery: {format_names(energy)}",
                f"Dryness and late-night support: {format_names(dryness)}",
                f"Cooling and lighter balance: {format_names(cooling)}",
                f"Mood or post-meal ease: {format_names(lifestyle)}",
                f"Premium gifting: {format_names(gifting)}",
                "If you tell me whether you are shopping for daily use, late nights, post-meal lightness, or gifting, I can narrow this to 1-2 teas right away.",
            ]
        else:
            lines = [
                "目前站内的茶单，大致可以这样看：",
                f"元气与恢复感：{format_names(energy)}",
                f"清润与熬夜后：{format_names(dryness)}",
                f"轻清与平衡感：{format_names(cooling)}",
                f"舒心或饭后轻饮：{format_names(lifestyle)}",
                f"礼赠与高级感：{format_names(gifting)}",
                "如果你告诉我更偏日常、熬夜后、饭后轻负担，还是送礼，我可以直接帮你缩到 1-2 款。",
            ]

    link_block = _render_links(language, links)
    return "\n\n".join(lines + ([link_block] if link_block else []))


def _render_article_or_brand_answer(language: str, intent: str, query_text: str, links) -> str:
    normalized = str(query_text or "").strip().lower()
    if language == "en":
        if intent == "article_request":
            base = "If you want to learn first instead of jumping straight into products, this is the cleanest next read."
        elif intent == "brewing_or_usage_question":
            base = "I can answer brewing, taste, and when-to-drink questions directly, then point you to one useful page if you want to keep going."
        elif _contains_any(normalized, ("what can you do", "help with", "how can you help")):
            base = "I can help with tea recommendations, gifting choices, ingredient explanations, brewing questions, and the most useful paths through the brand site."
        else:
            base = "I can keep this practical around products, ingredients, gifting, tea rituals, and the most relevant content across the brand site."
    else:
        if intent == "article_request":
            base = "如果你想先了解再决定买哪款，这篇会是更顺手的起点。"
        elif intent == "brewing_or_usage_question":
            base = "这类问题我可以先直接回答饮用场景、口感和冲泡节奏，再补一个最有用的页面给你。"
        elif _contains_any(normalized, ("你能做什么", "能帮我什么", "怎么帮")):
            base = "我可以帮你做产品挑选、送礼建议、原料解释、冲泡与饮用场景判断，也能带你走品牌站内最相关的内容路径。"
        else:
            base = "这类问题我会尽量回答得更实用一点，围绕产品、原料、送礼、饮用场景和站内内容来帮你收方向。"
    link_block = _render_links(language, links)
    return base if not link_block else f"{base}\n\n{link_block}"


class ProductHelperService:
    def __init__(self) -> None:
        self._sessions = get_product_helper_session_store()

    def handle(self, *, user_id: str, text: str, preferred_language: str | None = None, channel: str = "web", history_text: str = "") -> HelperResult:
        session = self._sessions.get(user_id)
        language = _detect_language(text, preferred_language, fallback=session.language or "zh")
        questionnaire = load_questionnaire_config()
        knowledge_base = load_knowledge_base_config()
        bundle = load_catalog_bundle()
        route = route_intent(text=text, language=language, knowledge_base=knowledge_base)

        extracted_intake = extract_intake_from_text(text, questionnaire)
        merged_intake = merge_intake(session.intake, extracted_intake)
        if route.use_case:
            merged_intake["use_case"] = str(extracted_intake.get("use_case") or route.use_case).strip()
        intake_state = normalize_intake(merged_intake, questionnaire)

        if _high_risk_match(text):
            reply = HIGH_RISK_FALLBACK[language]
            self._sessions.upsert(user_id, language=language, intake=intake_state, current_intent="high_risk_medical", current_use_case=route.use_case, last_question=text)
            return HelperResult(language, "high_risk_medical", "fallback_safe", _trim_reply(reply, channel), False, (), intake_state, None, (), (), (), {"route": route})

        if route.intent == "out_of_scope":
            reply = OUT_OF_SCOPE_REPLY[language]
            self._sessions.upsert(user_id, language=language, intake=intake_state, current_intent=route.intent, current_use_case=route.use_case, last_question=text)
            return HelperResult(language, route.intent, route.mode, _trim_reply(reply, channel), False, (), intake_state, None, (), (), (), {"route": route})

        needs_constitution = route.intent in {"symptom_or_discomfort_guidance", "constitution_guidance", "product_recommendation_direct", "gifting_recommendation"}
        constitution_assessment = assess_constitutions(query_text=text, intake=intake_state, config=load_constitution_config(), language=language) if needs_constitution else None
        if route.intent in {"gifting_recommendation", "symptom_or_discomfort_guidance"} and not intake_state.get("use_case"):
            intake_state["use_case"] = route.use_case

        sparse_intake = len([value for value in intake_state.values() if value not in ("", [], None)]) <= 1
        weak_constitution = not constitution_assessment or not constitution_assessment.candidates
        should_followup = route.intent in {"symptom_or_discomfort_guidance", "gifting_recommendation"} and sparse_intake and weak_constitution
        if route.intent == "gifting_recommendation" and _gifting_has_context(text, intake_state):
            should_followup = False
        if should_followup:
            followup = next_followup_question(questionnaire=questionnaire, intake=intake_state, intent=route.intent, language=language)
            reply = _compose_followup(language=language, question=followup, use_case=route.use_case)
            self._sessions.upsert(user_id, language=language, intake=intake_state, current_intent=route.intent, current_use_case=route.use_case, last_question=text)
            return HelperResult(language, route.intent, "intake_followup", _trim_reply(reply, channel), True, (followup,), intake_state, constitution_assessment, (), (), (), {"route": route})

        recommendations = ()
        if route.intent in {"symptom_or_discomfort_guidance", "constitution_guidance", "product_recommendation_direct", "gifting_recommendation", "compare_products"}:
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
        safety_notes = _caution_notes(text, language)

        if route.intent == "compare_products":
            reply = _render_compare_answer(language, route, recommendations, links)
        elif route.intent == "ingredient_explanation":
            ingredient_slug = route.mentioned_ingredients[0] if route.mentioned_ingredients else (recommendations[0].product.ingredients[0] if recommendations else "")
            reply = _render_ingredient_answer(language, ingredient_slug, links)
        elif route.intent == "product_catalog_request":
            reply = _render_product_catalog_answer(language, bundle, route, links)
        elif route.intent in {"article_request", "brewing_or_usage_question", "general_brand_scope_qna"}:
            reply = _render_article_or_brand_answer(language, route.intent, text, links)
        else:
            reply = _render_product_recommendation(
                language=language,
                intent=route.intent,
                route=route,
                query_text=text,
                recommendations=recommendations,
                constitution_assessment=constitution_assessment,
                links=links,
                safety_notes=safety_notes,
            )

        shortlist_products = [item.product.slug for item in recommendations[:3]]
        shortlist_constitutions = [candidate.label["zh"] for candidate in constitution_assessment.candidates[:2]] if constitution_assessment else []
        self._sessions.upsert(
            user_id,
            language=language,
            intake=intake_state,
            current_intent=route.intent,
            current_use_case=route.use_case,
            shortlisted_products=shortlist_products,
            shortlisted_ingredients=list(route.mentioned_ingredients[:3]),
            last_constitutions=shortlist_constitutions,
            last_question=text,
        )

        return HelperResult(language, route.intent, route.mode, _trim_reply(reply, channel), False, (), intake_state, constitution_assessment, recommendations, links, safety_notes, {"route": route, "history_text": history_text})


@lru_cache(maxsize=1)
def get_product_helper_service() -> ProductHelperService:
    return ProductHelperService()
