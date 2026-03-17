from __future__ import annotations

import re
from typing import Iterable

from app.i18n import normalize_language, resolve_localized_text
from app.product_helper.config import load_constitution_config
from app.product_helper.intent_router import IntentRoute
from app.product_helper.models import CatalogBundle, ConstitutionAssessment, Ingredient, LinkEntry, Product, ProductRecommendation


def _normalized_text(*values: object) -> str:
    joined = " ".join(str(value or "") for value in values)
    return re.sub(r"\s+", " ", joined).strip().lower()


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    normalized = _normalized_text(text)
    return any(_normalized_text(term) in normalized for term in terms if str(term).strip())


def _display_product(product: Product, language: str) -> str:
    return product.name["en"] if language == "en" else product.name["zh"]


def _display_constitutions(constitutions: tuple[str, ...], language: str) -> str:
    if not constitutions:
        return ""
    if language == "zh":
        return "、".join(constitutions[:3])

    config = load_constitution_config()
    labels: list[str] = []
    for constitution in constitutions[:3]:
        selected = constitution
        for item in config.constitutions.values():
            label = item.get("label", {})
            if str(label.get("zh", "")).strip() == constitution:
                selected = str(label.get("en", constitution)).strip() or constitution
                break
        labels.append(selected)
    return ", ".join(labels)


def _temperature_tendency(product: Product, language: str) -> str:
    tags = set(product.benefit_tags) | set(product.extra_tags)
    if {"cooling", "light-cooling"} & tags:
        return "更偏清润、清爽" if language == "zh" else "leans lighter and more cooling"
    if {"warming", "gently-warming"} & tags:
        return "更偏温养、温润" if language == "zh" else "leans warmer and more comforting"
    return "走向更平衡" if language == "zh" else "stays more balanced overall"


def _premium_feel(product: Product, language: str) -> str:
    tags = set(product.benefit_tags) | set(product.extra_tags)
    premium = "premium" in tags or "gift-worthy" in tags or product.price >= 39
    if language == "zh":
        return "礼感会更强" if premium else "更偏日常稳妥"
    return "feels more premium" if premium else "feels easier for everyday gifting"


def _is_http_url(url: str) -> bool:
    normalized = str(url or "").strip().lower()
    return normalized.startswith("https://") or normalized.startswith("http://")


def _markdown_link(label: str, url: str) -> str:
    safe_label = str(label or "").strip()
    safe_url = str(url or "").strip()
    if not safe_label or not _is_http_url(safe_url):
        return safe_label or safe_url
    return f"[{safe_label}]({safe_url})"


def _link_label(language: str, entry: LinkEntry) -> str:
    title = entry.en_title if language == "en" else entry.zh_title
    if entry.type == "product":
        return f"View Product: {title}" if language == "en" else f"查看产品：{title}"
    if entry.type == "ingredient":
        return f"Learn more about {title}" if language == "en" else f"查看{title}详情"
    if entry.type == "article":
        return f"Read article: {title}" if language == "en" else f"查看文章：{title}"
    return title


def _format_links(language: str, links: tuple[LinkEntry, ...], *, limit: int) -> str:
    selected = tuple(link for link in links[:limit] if _is_http_url(link.url))
    if not selected:
        return ""
    lead = "If you want the next step, these are the most useful pages:" if language == "en" else "如果你想继续看详情，可以先看："
    lines = []
    for entry in selected:
        lines.append(f"- {_markdown_link(_link_label(language, entry), entry.url)}")
    return f"{lead}\n" + "\n".join(lines)


def _render_safety_note(language: str, safety_notes: tuple[str, ...]) -> str:
    if not safety_notes:
        return ""
    note = safety_notes[0]
    return f"Gentle note: {note}" if language == "en" else f"温和提醒：{note}"


def _product_detail_focus(query_text: str) -> str:
    normalized = _normalized_text(query_text)
    if _contains_any(normalized, ("原材料", "原料", "成分", "配方", "里面都有什么", "每个原料", "介绍一下", "ingredients", "what is in", "what's in", "each ingredient")):
        return "ingredients"
    if _contains_any(normalized, ("口感", "味道", "喝起来", "taste", "flavor", "bitter")):
        return "taste"
    if _contains_any(normalized, ("怎么泡", "什么时候喝", "brew", "when to drink")):
        return "brew"
    if _contains_any(normalized, ("送", "gift", "妈妈", "长辈", "for my mom")):
        return "gift"
    if _contains_any(normalized, ("为什么适合", "适合吗", "why", "fit", "适合气虚", "适合什么人")):
        return "suitability"
    return "overview"


def _primary_symptom_phrase(query_text: str, language: str) -> str:
    normalized = _normalized_text(query_text)
    cue_map = (
        (("口干", "咽干", "dryness", "dry throat"), "口干偏燥" if language == "zh" else "dryness"),
        (("累", "疲劳", "恢复慢", "fatigue", "low energy"), "容易累、恢复慢" if language == "zh" else "low energy and slower recovery"),
        (("怕冷", "cold", "手脚凉"), "怕冷、偏寒" if language == "zh" else "cold sensitivity"),
        (("饭后困", "困重", "heavy after meals"), "饭后困重" if language == "zh" else "post-meal heaviness"),
    )
    for terms, label in cue_map:
        if _contains_any(normalized, terms):
            return label
    return "最近的状态" if language == "zh" else "what you described"


def _top_pair_text(items: list[str], language: str) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}" if language == "en" else f"{items[0]}和{items[1]}"
    if language == "en":
        return ", ".join(items[:-1]) + f", and {items[-1]}"
    return "、".join(items)


def _display_ingredient(ingredient: Ingredient, language: str) -> str:
    return ingredient.name["en"] if language == "en" else ingredient.name["zh"]


def _localized_value(value: object, language: str) -> str:
    if isinstance(value, dict):
        return resolve_localized_text(value, language, fallback="")
    return str(value or "").strip()


def _ingredient_pairings_text(ingredient: Ingredient, bundle: CatalogBundle, language: str) -> str:
    names = [
        _display_ingredient(bundle.ingredients_by_slug[slug], language)
        for slug in ingredient.pairings[:4]
        if slug in bundle.ingredients_by_slug
    ]
    return _top_pair_text(names, language)


def _ingredient_field_label(field: str, language: str) -> str:
    if language == "en":
        labels = {
            "aliases": "Aliases",
            "summary": "Summary",
            "nutrition_focus": "Nutrition focus",
            "traditional_use": "Traditional use",
            "flavor_profile": "Flavor profile",
            "pairings": "Pairings",
            "cautions": "Cautions",
        }
    else:
        labels = {
            "aliases": "别名",
            "summary": "简介",
            "nutrition_focus": "关注点",
            "traditional_use": "传统用法",
            "flavor_profile": "风味",
            "pairings": "常见搭配",
            "cautions": "注意",
        }
    return labels[field]


def _ingredient_detail_block(ingredient: Ingredient, bundle: CatalogBundle, language: str) -> str:
    name = _display_ingredient(ingredient, language)
    aliases = _localized_value(ingredient.aliases, language)
    summary = _localized_value(ingredient.summary, language)
    nutrition_focus = _localized_value(ingredient.nutrition_focus, language)
    traditional_use = _localized_value(ingredient.traditional_use, language)
    flavor_profile = _localized_value(ingredient.flavor_profile, language)
    pairings = _ingredient_pairings_text(ingredient, bundle, language)
    cautions = _localized_value(ingredient.cautions, language)

    lines = [f"- {name}"]
    if aliases:
        lines.append(f"  {_ingredient_field_label('aliases', language)}: {aliases}")
    if summary:
        lines.append(f"  {_ingredient_field_label('summary', language)}: {summary}")
    if nutrition_focus:
        lines.append(f"  {_ingredient_field_label('nutrition_focus', language)}: {nutrition_focus}")
    if traditional_use:
        lines.append(f"  {_ingredient_field_label('traditional_use', language)}: {traditional_use}")
    if flavor_profile:
        lines.append(f"  {_ingredient_field_label('flavor_profile', language)}: {flavor_profile}")
    if pairings:
        lines.append(f"  {_ingredient_field_label('pairings', language)}: {pairings}")
    if cautions:
        lines.append(f"  {_ingredient_field_label('cautions', language)}: {cautions}")
    return "\n".join(lines)


def _missing_ingredient_block(slug: str, language: str) -> str:
    if language == "en":
        return f"- {slug}\n  Summary: This ingredient record was not found in the current catalog."
    return f"- {slug}\n  简介: 当前原料库里暂时没有找到这个原料档案。"


def _product_blurb(recommendation: ProductRecommendation, language: str) -> str:
    name = _display_product(recommendation.product, language)
    tagline = recommendation.product.tagline["en"] if language == "en" else recommendation.product.tagline["zh"]
    taste = recommendation.taste
    if language == "en":
        return f"{name} feels more like {tagline.lower()}, with a taste profile that is {taste}."
    return f"{name} 更偏 {tagline}，喝起来会是 {taste}。"


def _product_recommendation_opener(
    *,
    language: str,
    intent: str,
    query_text: str,
    recommendations: tuple[ProductRecommendation, ...],
) -> str:
    names = [_display_product(item.product, language) for item in recommendations[:2]]
    pair_text = _top_pair_text(names, language)
    normalized = _normalized_text(query_text)

    if intent == "gifting_recommendation":
        if language == "en":
            if _contains_any(normalized, ("mom", "mother", "older family member")):
                return f"If this is for a gift for your mom, I would start with {pair_text}."
            if _contains_any(normalized, ("not too bitter", "easy to drink")):
                return f"If you want a gift that still feels easy to drink, I would start with {pair_text}."
            return f"For gifting, I would start with {pair_text}."
        if _contains_any(normalized, ("妈妈", "长辈")):
            return f"如果是送妈妈或长辈，我会先看 {pair_text}。"
        if _contains_any(normalized, ("不想太苦", "好入口")):
            return f"如果你想送得体面、又不要太苦，我会先看 {pair_text}。"
        return f"如果先从送礼方向收得稳一点，我会先看 {pair_text}。"

    if language == "en":
        if _contains_any(normalized, ("dry", "dryness", "staying up late", "late night")):
            return f"If the bigger theme lately is dryness after late nights, I would start with {pair_text}."
        if _contains_any(normalized, ("fatigue", "low energy", "slow recovery", "tired")):
            return f"If low energy and slower recovery are the main issue, I would start with {pair_text}."
        return f"I would start with {pair_text}."

    if _contains_any(normalized, ("口干", "熬夜", "偏燥")):
        return f"如果你最近更像是口干加熬夜这一路，我会先看 {pair_text}。"
    if _contains_any(normalized, ("累", "疲劳", "恢复慢", "气短")):
        return f"如果你现在更在意的是累和恢复慢，我会先看 {pair_text}。"
    return f"我会先从 {pair_text} 开始。"


def _compose_recommendation_reply(
    *,
    language: str,
    intent: str,
    query_text: str,
    recommendations: tuple[ProductRecommendation, ...],
    safety_notes: tuple[str, ...],
    links: tuple[LinkEntry, ...],
) -> str:
    if not recommendations:
        return (
            "我先不硬推产品。你告诉我更偏累、口干、怕冷，还是送礼场景，我就能直接帮你收成 1-2 款。"
            if language == "zh"
            else "I would not force a product pick yet. Tell me whether this is more about fatigue, dryness, cold sensitivity, or gifting, and I can narrow it to one or two teas."
        )

    lines = [_product_recommendation_opener(language=language, intent=intent, query_text=query_text, recommendations=recommendations)]
    lines.extend(_product_blurb(item, language) for item in recommendations[:2])
    safety_line = _render_safety_note(language, safety_notes)
    if safety_line:
        lines.append(safety_line)
    link_block = _format_links(language, links, limit=2)
    if link_block:
        lines.append(link_block)
    return "\n\n".join(line for line in lines if line.strip())


def _compose_product_detail_reply(
    *,
    language: str,
    query_text: str,
    product: Product,
    bundle: CatalogBundle,
    safety_notes: tuple[str, ...],
    links: tuple[LinkEntry, ...],
) -> str:
    focus = _product_detail_focus(query_text)
    name = _display_product(product, language)
    tagline = product.tagline["en"] if language == "en" else product.tagline["zh"]
    summary = product.summary["en"] if language == "en" else product.summary["zh"]
    taste = resolve_localized_text(product.flavor_notes, language)
    brew = product.brew_guide["en"] if language == "en" else product.brew_guide["zh"]
    ingredient_names = [
        ingredient.name["en"] if language == "en" else ingredient.name["zh"]
        for slug in product.ingredients
        for ingredient in (bundle.ingredients_by_slug.get(slug),)
        if ingredient is not None
    ]
    ingredient_text = _top_pair_text(ingredient_names[:3], language)
    extra_ingredients = ingredient_names[3:]
    constitutions = _display_constitutions(product.constitution_types, language)
    lines: list[str] = []

    if focus == "ingredients":
        if language == "en":
            first_line = f"If you want to read {name} ingredient by ingredient, here is the blend in plain language."
            if ingredient_text:
                first_line += f" The core build is {ingredient_text}"
                if extra_ingredients:
                    first_line += f", with {', '.join(extra_ingredients)} rounding it out"
                first_line += "."
            lines.append(first_line)
        else:
            first_line = f"如果你想按原料一项项看，{name} 可以这样理解。"
            if ingredient_text:
                first_line += f" 它的核心组合是 {ingredient_text}"
                if extra_ingredients:
                    first_line += f"，另外还有 {'、'.join(extra_ingredients)}"
                first_line += "。"
            lines.append(first_line)

        for ingredient_slug in product.ingredients:
            ingredient = bundle.ingredients_by_slug.get(ingredient_slug)
            if ingredient is None:
                lines.append(_missing_ingredient_block(ingredient_slug, language))
                continue
            lines.append(_ingredient_detail_block(ingredient, bundle, language))

        if language == "en":
            lines.append(f"Overall, it leans toward {tagline.lower()} and tastes more {taste}.")
        else:
            lines.append(f"整体上它更偏 {tagline}，口感会是 {taste}。")
    elif focus == "taste":
        if language == "en":
            lines.append(f"{name} tastes more {taste}.")
            lines.append(f"The overall feel is {tagline.lower()}, so it lands gently rather than aggressively.")
        else:
            lines.append(f"{name} 喝起来更偏 {taste}。")
            lines.append(f"整体调性是 {tagline}，所以会偏顺口，不会太冲。")
    elif focus == "brew":
        if language == "en":
            lines.append(f"A clean way to brew {name} is: {brew}.")
            lines.append("It works best as a calm daily cup rather than something you rush through.")
        else:
            lines.append(f"{name} 可以这样泡：{brew}。")
            lines.append("更适合慢慢喝，不是那种越浓越好的路线。")
    elif focus == "gift":
        premium = _premium_feel(product, language)
        if language == "en":
            lines.append(f"Yes, {name} can work well as a gift.")
            lines.append(f"It tastes {taste}, and the overall feel {premium}.")
        else:
            lines.append(f"{name} 用来送人是可以的。")
            lines.append(f"它的口味是 {taste}，整体 {premium}。")
    elif focus == "suitability":
        if language == "en":
            if constitutions:
                lines.append(f"Within this tea catalog, {name} is positioned closer to {constitutions}.")
            else:
                lines.append(f"{name} is positioned more around {tagline.lower()}.")
            lines.append(f"The ingredient build is {ingredient_text}, so it feels more like {summary.lower()}.")
        else:
            if constitutions:
                lines.append(f"按这套产品设定，{name} 会更偏向 {constitutions} 这类日常选茶方向。")
            else:
                lines.append(f"{name} 整体更偏 {tagline}。")
            lines.append(f"它的原料结构是 {ingredient_text}，所以会更像 {summary}。")
    else:
        if language == "en":
            lines.append(f"{name} is more about {tagline.lower()}.")
            lines.append(f"The core ingredient build is {ingredient_text}, and the taste leans {taste}.")
        else:
            lines.append(f"{name} 更偏 {tagline}。")
            lines.append(f"核心原料是 {ingredient_text}，口感会是 {taste}。")

    safety_line = _render_safety_note(language, safety_notes)
    if safety_line:
        lines.append(safety_line)

    link_limit = 1 if focus in {"ingredients", "gift", "overview", "suitability"} else 0
    link_block = _format_links(language, links, limit=link_limit)
    if link_block:
        lines.append(link_block)
    return "\n\n".join(line for line in lines if line.strip())


def _ingredient_pair_reply(
    *,
    language: str,
    first,
    second,
    safety_notes: tuple[str, ...],
    links: tuple[LinkEntry, ...],
) -> str:
    first_name = first.name["en"] if language == "en" else first.name["zh"]
    second_name = second.name["en"] if language == "en" else second.name["zh"]
    first_flavor = resolve_localized_text(first.flavor_profile, language)
    second_flavor = resolve_localized_text(second.flavor_profile, language)

    if language == "en":
        lines = [
            f"{first_name} and {second_name} are often paired because the aroma profile becomes more layered and easier to enjoy.",
            f"{first_name} brings more of a {first_flavor} direction, while {second_name} adds a {second_flavor} finish, so the blend feels clearer rather than flat.",
            "In a wellness-tea context, that pairing is usually about balance and drinkability, not about making a treatment claim.",
        ]
    else:
        lines = [
            f"{first_name} 和 {second_name} 常一起出现，一个原因是香气层次会更完整，也更容易做出顺口感。",
            f"{first_name} 会带来更明显的 {first_flavor}，{second_name} 则把尾调拉向 {second_flavor}，所以整体不容易显得闷或单薄。",
            "放在草本茶语境里，这类搭配更多是在讲风味和平衡感，不是在网上直接下医疗结论。",
        ]

    safety_line = _render_safety_note(language, safety_notes)
    if safety_line:
        lines.append(safety_line)
    link_block = _format_links(language, links, limit=1)
    if link_block:
        lines.append(link_block)
    return "\n\n".join(lines)


def _compose_ingredient_reply(
    *,
    language: str,
    route: IntentRoute,
    query_text: str,
    bundle: CatalogBundle,
    links: tuple[LinkEntry, ...],
    safety_notes: tuple[str, ...],
) -> str:
    mentioned = [bundle.ingredients_by_slug.get(slug) for slug in route.mentioned_ingredients if bundle.ingredients_by_slug.get(slug)]
    if len(mentioned) >= 2:
        return _ingredient_pair_reply(
            language=language,
            first=mentioned[0],
            second=mentioned[1],
            safety_notes=safety_notes,
            links=links,
        )

    ingredient = mentioned[0] if mentioned else None
    if ingredient is None:
        return (
            "你告诉我具体是哪味原料，我就能直接从口感、搭配和适合场景来讲。"
            if language == "zh"
            else "If you tell me the ingredient, I can explain it directly through taste, pairings, and tea-use context."
        )

    name = _display_ingredient(ingredient, language)

    if language == "en":
        lines = [
            f"If you want a cleaner read on {name} in a tea context, here it is.",
            _ingredient_detail_block(ingredient, bundle, language),
        ]
    else:
        lines = [
            f"如果你想单看 {name} 这味原料，可以先这样理解。",
            _ingredient_detail_block(ingredient, bundle, language),
        ]

    safety_line = _render_safety_note(language, safety_notes)
    if safety_line:
        lines.append(safety_line)
    link_block = _format_links(language, links, limit=1)
    if link_block:
        lines.append(link_block)
    return "\n\n".join(line for line in lines if line.strip())


def _compose_compare_reply(
    *,
    language: str,
    query_text: str,
    route: IntentRoute,
    bundle: CatalogBundle,
    safety_notes: tuple[str, ...],
    links: tuple[LinkEntry, ...],
) -> str:
    products = [bundle.products_by_slug.get(slug) for slug in route.mentioned_products[:2]]
    products = [item for item in products if item is not None]
    if len(products) < 2:
        return (
            "你给我两款具体产品名，我可以直接按口感、适合场景和礼感帮你对比。"
            if language == "zh"
            else "Give me the two product names and I can compare them directly by taste, use case, and premium feel."
        )

    first, second = products[0], products[1]
    first_name = _display_product(first, language)
    second_name = _display_product(second, language)
    first_taste = resolve_localized_text(first.flavor_notes, language)
    second_taste = resolve_localized_text(second.flavor_notes, language)
    normalized = _normalized_text(query_text)

    if language == "en":
        lines = [
            f"If you are comparing {first_name} and {second_name}, the cleanest difference is this:",
            f"{first_name} is more about {first.tagline['en'].lower()}, and it tastes {first_taste}.",
            f"{second_name} is more about {second.tagline['en'].lower()}, and it tastes {second_taste}.",
        ]
    else:
        lines = [
            f"如果你在比 {first_name} 和 {second_name}，重点差别大致在这里：",
            f"{first_name} 更偏 {first.tagline['zh']}，口感是 {first_taste}。",
            f"{second_name} 更偏 {second.tagline['zh']}，口感是 {second_taste}。",
        ]

    if _contains_any(normalized, ("口干", "熬夜", "dryness", "late night")):
        preferred = first if {"dryness-relief", "night-recovery", "yin-support"} & (set(first.benefit_tags) | set(first.extra_tags)) else second
        other = second if preferred is first else first
        if language == "en":
            lines.append(f"For dryness after late nights, I would lean more toward {_display_product(preferred, language)}. If you want something easier for everyday use, {_display_product(other, language)} feels simpler.")
        else:
            lines.append(f"如果是熬夜后口干这一路，我会更偏向 {_display_product(preferred, language)}；如果你想要更日常、入口更轻松一点，{_display_product(other, language)} 会更稳。")
    else:
        premium_choice = first if first.price >= second.price else second
        everyday_choice = second if premium_choice is first else first
        if language == "en":
            lines.append(f"If premium presentation matters more, {_display_product(premium_choice, language)} has the stronger gifting feel. If easy everyday drinkability matters more, {_display_product(everyday_choice, language)} is the gentler pick.")
        else:
            lines.append(f"如果你更看重礼感和高级感，{_display_product(premium_choice, language)} 会更占优；如果你更在意日常好入口，{_display_product(everyday_choice, language)} 会更轻松。")

    safety_line = _render_safety_note(language, safety_notes)
    if safety_line:
        lines.append(safety_line)
    link_block = _format_links(language, links, limit=2)
    if link_block:
        lines.append(link_block)
    return "\n\n".join(lines)


def _match_constitution_topic(query_text: str) -> tuple[str, dict[str, object]] | tuple[None, None]:
    normalized = _normalized_text(query_text)
    config = load_constitution_config()
    for key, item in config.constitutions.items():
        label = item.get("label", {})
        terms = [
            str(label.get("zh", "")).strip(),
            str(label.get("en", "")).strip().lower(),
            key.replace("_", " "),
        ]
        if any(term and term.lower() in normalized for term in terms):
            return key, item
    return None, None


def _compose_wellness_education_reply(
    *,
    language: str,
    query_text: str,
) -> str:
    _, constitution = _match_constitution_topic(query_text)
    symptom_phrase = _primary_symptom_phrase(query_text, language)

    if constitution is None:
        if language == "en":
            return (
                "Within a tea-and-wellness context, these terms are best treated as directional language for everyday choices, not as a diagnosis. "
                "If you want, I can translate the idea into a simpler product direction."
            )
        return "放在茶饮和日常调养语境里，这类词更适合当作方向性的参考，不等同于诊断。如果你愿意，我也可以把它翻成更好懂的选茶方向。"

    label = constitution.get("label", {})
    description = constitution.get("description", {})
    zh_label = str(label.get("zh", "")).strip()
    en_label = str(label.get("en", "")).strip()
    zh_desc = str(description.get("zh", "")).strip().rstrip("。")
    en_desc = str(description.get("en", "")).strip().rstrip(".")
    if zh_desc.startswith("更常见于"):
        zh_desc = "更容易出现" + zh_desc.removeprefix("更常见于")
    if en_desc.lower().startswith("more often associated with "):
        en_desc = "more often associated with " + en_desc[27:]

    if language == "en":
        return (
            f"In an everyday wellness-tea context, {en_label} is usually used to describe a pattern that is {en_desc.lower()}. "
            f"That is why {symptom_phrase} is often treated as a directional signal in tea selection. "
            "It is still just a tea-selection reference here, not a diagnosis."
        )

    return (
        f"在日常草本茶语境里，{zh_label} 常用来形容那种 {zh_desc} 的状态。"
        f"所以像“{symptom_phrase}”这种感觉，常会被拿来当作选茶时的方向性信号。"
        "不过这里更适合把它当成日常参考，不等同于诊断。"
    )


def _compose_article_reply(
    *,
    language: str,
    query_text: str,
    bundle: CatalogBundle,
    links: tuple[LinkEntry, ...],
) -> str:
    article_link = next((entry for entry in links if entry.type == "article"), None)
    if article_link and article_link.slug in bundle.articles_by_slug:
        article = bundle.articles_by_slug[article_link.slug]
        title = article.title["en"] if language == "en" else article.title["zh"]
        excerpt = article.excerpt["en"] if language == "en" else article.excerpt["zh"]
        if language == "en":
            text = f"If you want to read first, I would start with “{title}”. It is the most useful next piece for this topic: {excerpt}"
        else:
            text = f"如果你想先看内容，我会先从《{title}》开始。它跟这个问题最接近，读起来也更顺手：{excerpt}"
        link_block = _format_links(language, (article_link,), limit=1)
        return f"{text}\n\n{link_block}" if link_block else text

    if language == "en":
        return "I can help you find the most useful product, ingredient, or gifting article next. Tell me which direction you want to read first."
    return "我可以继续帮你缩到更适合的产品、原料或送礼文章方向。你告诉我想先看哪一边就行。"


def _compose_casual_chat_reply(language: str, query_text: str) -> str:
    normalized = _normalized_text(query_text)
    if language == "en":
        if _contains_any(normalized, ("hi", "hello", "hey")):
            return "Hi, I'm here. We can keep this casual, or if you want, I can help with teas, ingredients, gifting, or light wellness questions."
        if _contains_any(normalized, ("thanks", "thank you")):
            return "You're welcome. If you want to keep going, just send the next question."
        if _contains_any(normalized, ("how are you", "how's your day")):
            return "I'm doing well and ready to help. If you want, we can chat casually or jump straight into tea, ingredients, or gifting."
        if _contains_any(normalized, ("who are you", "what are you")):
            return "I'm the brand's bilingual wellness helper. I'm best at product guidance, ingredient explanations, gifting ideas, and tea-related questions."
        return ""

    if _contains_any(normalized, ("你好", "嗨", "哈喽", "hello", "hi")):
        return "你好，我在。你可以和我轻松聊，也可以直接问茶、原料、送礼或轻量养生问题。"
    if _contains_any(normalized, ("谢谢", "多谢")):
        return "不客气，你继续问就行。"
    if _contains_any(normalized, ("你怎么样", "你在吗", "忙吗")):
        return "我在，也准备好继续帮你。你要是想轻松聊两句可以，要是想直接看茶或原料也可以。"
    if _contains_any(normalized, ("你是谁", "你是做什么的")):
        return "我是这个品牌里的双语 wellness helper，主要帮你做产品挑选、原料解释、送礼建议和茶相关问答。"
    return ""


def _apply_loop_recovery_prefix(text: str, language: str, loop_detected: bool) -> str:
    normalized = str(text or "").strip()
    if not loop_detected or not normalized:
        return normalized
    prefix = "Let me answer that more directly from your latest message." if language == "en" else "我直接接你这句最新问题来回答。"
    if normalized.startswith(prefix):
        return normalized
    return f"{prefix}\n\n{normalized}"


def _compose_brand_scope_reply(language: str, query_text: str, links: tuple[LinkEntry, ...]) -> str:
    normalized = _normalized_text(query_text)
    casual_reply = _compose_casual_chat_reply(language, query_text)
    if casual_reply:
        return casual_reply
    if language == "en":
        if _contains_any(normalized, ("what can you do", "how can you help", "help with")):
            base = "I can help with product selection, gifting, ingredient explanations, light wellness education, and the most useful articles across the brand site."
        else:
            base = "We can keep this practical and natural around tea products, ingredients, gifting, and the most relevant brand content."
    else:
        if _contains_any(normalized, ("你能做什么", "能帮我什么", "怎么帮")):
            base = "我可以帮你做产品挑选、送礼建议、原料解释、轻量的养生知识说明，以及站内最相关的内容导览。"
        else:
            base = "这类问题我会尽量用自然一点的方式，收在产品、原料、送礼和站内内容这几个更有用的方向里。"
    link_block = _format_links(language, links, limit=1)
    return f"{base}\n\n{link_block}" if link_block else base


def compose_reply(
    *,
    language: str,
    intent: str,
    route: IntentRoute,
    query_text: str,
    bundle: CatalogBundle,
    constitution_assessment: ConstitutionAssessment | None,
    recommendations: tuple[ProductRecommendation, ...],
    links: tuple[LinkEntry, ...],
    safety_notes: tuple[str, ...],
    previous_assistant_text: str = "",
    loop_detected: bool = False,
) -> str:
    lang = normalize_language(language)

    if intent == "product_detail":
        target_slug = route.mentioned_products[0] if route.mentioned_products else ""
        target = bundle.products_by_slug.get(target_slug)
        if target is None:
            return (
                "你提到具体产品名，我就能直接按原料、口感或适合场景来拆。"
                if lang == "zh"
                else "If you name the product, I can answer directly through ingredients, taste, or fit."
            )
        reply = _compose_product_detail_reply(
            language=lang,
            query_text=query_text,
            product=target,
            bundle=bundle,
            safety_notes=safety_notes,
            links=links,
        )
        return _apply_loop_recovery_prefix(reply, lang, loop_detected)

    if intent in {"symptom_or_discomfort_guidance", "constitution_guidance", "product_recommendation_direct", "gifting_recommendation"}:
        reply = _compose_recommendation_reply(
            language=lang,
            intent=intent,
            query_text=query_text,
            recommendations=recommendations,
            safety_notes=safety_notes,
            links=links,
        )
        return _apply_loop_recovery_prefix(reply, lang, loop_detected)

    if intent == "compare_products":
        reply = _compose_compare_reply(
            language=lang,
            query_text=query_text,
            route=route,
            bundle=bundle,
            safety_notes=safety_notes,
            links=links,
        )
        return _apply_loop_recovery_prefix(reply, lang, loop_detected)

    if intent == "ingredient_explanation":
        reply = _compose_ingredient_reply(
            language=lang,
            route=route,
            query_text=query_text,
            bundle=bundle,
            links=links,
            safety_notes=safety_notes,
        )
        return _apply_loop_recovery_prefix(reply, lang, loop_detected)

    if intent == "product_catalog_request":
        reply = (
            "如果你想先看茶单，我更建议按场景来读：元气恢复、清润熬夜后、轻清平衡、饭后轻负担、以及送礼方向。你告诉我想先看哪一路，我可以直接缩到 1-2 款。"
            if lang == "zh"
            else "If you want the full lineup first, the cleanest way to read it is by use case: energy, dryness after late nights, lighter balance, after-meal ease, or gifting. Tell me the direction and I can narrow it to one or two teas."
        ) + (f"\n\n{_format_links(lang, links, limit=1)}" if links else "")
        return _apply_loop_recovery_prefix(reply, lang, loop_detected)

    if intent == "article_request":
        reply = _compose_article_reply(language=lang, query_text=query_text, bundle=bundle, links=links)
        return _apply_loop_recovery_prefix(reply, lang, loop_detected)

    if intent == "wellness_education_in_scope":
        reply = _compose_wellness_education_reply(language=lang, query_text=query_text)
        return _apply_loop_recovery_prefix(reply, lang, loop_detected)

    if intent == "brewing_or_usage_question":
        reply = (
            "这类问题我可以直接按饮用场景、口感和冲泡方式来讲。如果你告诉我是哪一款茶，我可以回答得更具体。"
            if lang == "zh"
            else "I can answer that directly through taste, use case, and brewing style. If you tell me which tea you mean, I can make it more specific."
        )
        return _apply_loop_recovery_prefix(reply, lang, loop_detected)

    reply = _compose_brand_scope_reply(lang, query_text, links)
    return _apply_loop_recovery_prefix(reply, lang, loop_detected)
