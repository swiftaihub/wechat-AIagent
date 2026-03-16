from __future__ import annotations

import re
from typing import Iterable

from app.i18n import normalize_language, resolve_localized_text
from app.product_helper.models import ConstitutionAssessment, Product, ProductRecommendation


def _normalized_text(*values: object) -> str:
    joined = " ".join(str(value or "") for value in values)
    return re.sub(r"\s+", " ", joined).strip().lower()


def _matches_any(text: str, values: Iterable[str]) -> bool:
    normalized = _normalized_text(text)
    return any(_normalized_text(value) in normalized for value in values if str(value).strip())


def _taste_summary(product: Product, language: str) -> str:
    return resolve_localized_text(product.flavor_notes, language, fallback="")


def _when_to_drink(product: Product, language: str) -> str:
    guide = resolve_localized_text(product.brew_guide, language, fallback="")
    if language == "en":
        return f"Best suited to a calm daily cup or a deliberate tea break. Brewing guide: {guide}"
    return f"更适合放在日常慢慢喝，或作为需要一点恢复感时的茶饮。冲泡参考：{guide}"


def _caution_line(product: Product, language: str) -> str:
    return resolve_localized_text(product.cautions, language, fallback="")


def _intent_bonus(intent: str, product: Product, use_case: str, intake: dict[str, object], text: str) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    normalized = _normalized_text(text)
    benefit_tags = set(product.benefit_tags) | set(product.extra_tags)

    if intent == "gifting_recommendation":
        if "premium" in benefit_tags or "gift-worthy" in benefit_tags:
            score += 4.0
            reasons.append("gift fit")
        if product.price >= 35:
            score += 1.5
    elif intent == "compare_products":
        score += 1.0
    elif use_case == "daily_wellness" and "daily-wellness" in benefit_tags:
        score += 1.5
        reasons.append("daily ritual fit")

    premium_preference = str(intake.get("premium_preference", "")).strip().lower()
    if premium_preference in {"yes", "true", "premium-forward", "高", "偏高"} and ("premium" in benefit_tags or product.price >= 39):
        score += 2.5
        reasons.append("premium preference")

    flavor_preference = _normalized_text(intake.get("flavor_preference", ""))
    if flavor_preference and _matches_any(flavor_preference, resolve_localized_text(product.flavor_notes, "en").split(",")):
        score += 1.5
        reasons.append("flavor preference")

    if "not too bitter" in normalized or "不想太苦" in normalized:
        if "sweet" in _normalized_text(product.flavor_notes):
            score += 2.0
            reasons.append("easy drinking profile")
        elif "lightly bitter" in _normalized_text(product.flavor_notes):
            score -= 1.0

    return score, reasons


def _caution_penalty(product: Product, intake: dict[str, object], text: str) -> float:
    penalty = 0.0
    normalized = _normalized_text(text, intake.get("dryness_signs", ""), intake.get("cold_heat_preference_or_sensitivity", ""))
    benefit_tags = set(product.benefit_tags)
    caution_text = _normalized_text(product.cautions)

    if any(term in normalized for term in ("dry", "dryness", "口干", "dry throat", "yin")):
        if "warming" in benefit_tags or "gently-warming" in benefit_tags:
            penalty -= 2.0
        if "dryness" in caution_text or "口干" in caution_text:
            penalty -= 2.0

    if any(term in normalized for term in ("heat", "上火", "constipation", "便秘", "acne", "口苦")):
        if "warming" in benefit_tags or "gently-warming" in benefit_tags:
            penalty -= 3.0

    if any(term in normalized for term in ("cold", "怕冷", "cold-sensitive", "手脚冰凉")):
        if "cooling" in benefit_tags or "light-cooling" in benefit_tags:
            penalty -= 3.0

    return penalty


def rank_products(
    *,
    products: tuple[Product, ...],
    constitution_assessment: ConstitutionAssessment | None,
    intake: dict[str, object],
    query_text: str,
    intent: str,
    use_case: str,
    language: str,
    preferred_slugs: tuple[str, ...] = (),
) -> tuple[ProductRecommendation, ...]:
    lang = normalize_language(language)
    recommendations: list[ProductRecommendation] = []
    preferred_slug_set = set(preferred_slugs)
    combined_text = _normalized_text(
        query_text,
        intake.get("free_text_recent_discomfort", ""),
        " ".join(intake.get("recent_discomfort_combined", [])) if isinstance(intake.get("recent_discomfort_combined"), list) else "",
    )
    constitution_labels = {candidate.label["zh"] for candidate in constitution_assessment.candidates} if constitution_assessment else set()

    for product in products:
        if product.status != "active":
            continue
        if intent == "compare_products" and preferred_slug_set and product.slug not in preferred_slug_set:
            continue

        score = 0.0
        discomfort_hits: list[str] = []

        for candidate in constitution_assessment.candidates if constitution_assessment else ():
            if candidate.label["zh"] in product.constitution_types:
                score += {"high": 6.0, "medium": 4.0, "low": 2.5}.get(candidate.confidence, 2.0)

        discomfort_hits = [
            discomfort
            for discomfort in product.recent_discomforts
            if _normalized_text(discomfort) in combined_text
        ]
        if discomfort_hits:
            score += min(6.0, 2.0 * len(discomfort_hits))

        target_text = _normalized_text(product.target_users["zh"], product.target_users["en"], product.summary["zh"], product.summary["en"])
        for cue in ("fatigue", "恢复慢", "gift", "送礼", "dry", "dryness", "口干", "heavy", "困重", "bloating", "胀", "mood", "情绪", "sleep", "熬夜", "digestive", "饭后"):
            if cue in combined_text and cue in target_text:
                score += 1.2

        intent_score, _ = _intent_bonus(intent, product, use_case, intake, query_text)
        score += intent_score

        if preferred_slugs and product.slug in preferred_slugs:
            score += 2.5

        score += _caution_penalty(product, intake, query_text)
        if score <= 0:
            continue

        why_lines = []
        if constitution_labels & set(product.constitution_types):
            why_lines.append(
                "它和你目前更偏向的体感方向比较贴近。"
                if lang == "zh"
                else "It lines up well with the constitution direction that seems most relevant right now."
            )
        if discomfort_hits:
            hit_text = "、".join(discomfort_hits[:2]) if lang == "zh" else ", ".join(discomfort_hits[:2])
            why_lines.append(
                f"它更像是在照顾你提到的 {hit_text}。"
                if lang == "zh"
                else f"It feels especially aligned with the concerns you mentioned around {hit_text}."
            )
        if intent == "gifting_recommendation":
            why_lines.append("它的整体定位更体面、接受度也更高。" if lang == "zh" else "It feels polished and broadly gift-friendly.")
        if not why_lines:
            why_lines.append("它在口感和日常适配度之间比较平衡。" if lang == "zh" else "It strikes a balanced note between taste and day-to-day fit.")

        recommendations.append(
            ProductRecommendation(
                product=product,
                score=round(score, 2),
                why=tuple(why_lines[:2]),
                taste=_taste_summary(product, lang),
                when_to_drink=_when_to_drink(product, lang),
                caution=_caution_line(product, lang),
            )
        )

    recommendations.sort(key=lambda item: item.score, reverse=True)
    if intent == "compare_products":
        max_items = min(max(2, len(preferred_slugs)), len(recommendations))
    elif intent == "gifting_recommendation":
        max_items = min(2, len(recommendations))
    elif recommendations and len(recommendations) > 1 and recommendations[0].score - recommendations[1].score >= 3:
        max_items = 1
    else:
        max_items = 2
    return tuple(recommendations[: max_items or 1])
