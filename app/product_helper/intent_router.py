from __future__ import annotations

import re
from dataclasses import dataclass

from app.product_helper.config import KnowledgeBaseConfig
from app.product_helper.content import load_catalog_bundle


OUT_OF_SCOPE_PATTERNS = (
    "python",
    "javascript",
    "typescript",
    "docker",
    "sql",
    "coding",
    "code",
    "stocks",
    "investing",
    "tax",
    "legal",
    "算法",
    "代码",
    "编程",
    "股票",
    "税务",
    "法律",
)
MATH_HINTS = (
    "percent markdown",
    "percent off",
    "markdown percentage",
    "discount percentage",
    "discount rate",
    "original price",
    "sale price",
    "what percent",
    "solve this math",
    "math problem",
    "原价",
    "现价",
    "折扣",
    "打几折",
    "百分比",
    "数学题",
    "算一下",
)
GENERAL_TRIVIA_HINTS = (
    "capital of",
    "president",
    "prime minister",
    "weather",
    "nba",
    "nfl",
    "movie review",
    "who won",
    "translation",
    "translate this",
    "首都",
    "总统",
    "总理",
    "天气",
    "比赛",
    "翻译",
)
SYMPTOM_HINTS = (
    "疲劳",
    "累",
    "恢复慢",
    "tired",
    "low energy",
    "dry",
    "dryness",
    "staying up late",
    "口干",
    "怕冷",
    "heavy after meals",
    "胀",
    "困重",
    "上火",
    "sleep",
    "失眠",
)
GIFT_HINTS = (
    "gift",
    "gifting",
    "gift tea",
    "tea gift",
    "tea gift box",
    "送礼",
    "礼物",
    "茶礼",
    "茶礼盒",
    "礼盒",
    "送给",
    "送妈妈",
    "送长辈",
    "送女生",
    "送女朋友",
    "送闺蜜",
    "给妈妈买",
    "给女生买",
    "给女朋友买",
    "体面",
    "妈妈",
    "爸爸",
    "长辈",
    "女朋友",
    "闺蜜",
    "recipient",
    "for mom",
    "for my mom",
    "for her",
    "girlfriend",
)
COMPARE_HINTS = ("区别", "compare", "comparison", "difference", "vs", "哪个好", "差别", "不同")
ARTICLE_HINTS = ("article", "read", "learn more", "文章", "内容", "科普", "想了解更多", "看看关于")
BREWING_HINTS = ("brew", "brewing", "怎么泡", "什么时候喝", "ritual")
INGREDIENT_HINTS = (
    "ingredient",
    "herb",
    "原料",
    "成分",
    "是什么",
    "为什么",
    "搭配",
    "口感会不会",
    "适合什么样的人",
)
PRODUCT_DETAIL_HINTS = (
    "原材料",
    "原料",
    "成分",
    "配方",
    "里面都有什么",
    "里面有什么",
    "ingredients",
    "what is in",
    "what ingredients are in",
    "what's in",
    "each ingredient",
    "是什么口感",
    "什么口感",
    "喝起来",
    "taste",
    "flavor",
    "口感",
    "为什么适合",
    "why it fits",
    "适合气虚",
    "适合吗",
    "送给妈妈吗",
    "送妈妈合适吗",
    "什么时候喝",
    "怎么泡",
    "brew",
)
CONSTITUTION_HINTS = ("体质", "constitution", "偏什么体质", "qi deficiency", "yin deficiency")
WELLNESS_EDUCATION_TOPICS = (
    "气虚",
    "阳虚",
    "阴虚",
    "痰湿",
    "湿热",
    "气滞",
    "血瘀",
    "气血两虚",
    "constitution",
    "qi deficiency",
    "yang deficiency",
    "yin deficiency",
    "phlegm-dampness",
    "damp-heat",
    "dryness",
)
WELLNESS_EDUCATION_VERBS = (
    "什么关系",
    "为什么",
    "是什么",
    "是什么意思",
    "why",
    "what is",
    "how does",
    "how is",
    "relation",
    "区别",
)
PRODUCT_HINTS = ("tea", "product", "recommend", "推荐", "买哪款", "选哪款", "什么茶", "哪种茶", "哪款茶", "喝什么茶", "fit me best")
PRODUCT_LIST_HINTS = (
    "产品列表",
    "产品清单",
    "茶单",
    "有哪些茶",
    "全部产品",
    "所有产品",
    "都有什么茶",
    "show me the lineup",
    "tea lineup",
    "product list",
    "all products",
    "full lineup",
)
CASUAL_HINTS = (
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank you",
    "how are you",
    "who are you",
    "what can you do",
    "help",
    "你好",
    "嗨",
    "哈喽",
    "谢谢",
    "多谢",
    "你在吗",
    "你是谁",
    "你能做什么",
)
BRAND_SCOPE_HINTS = (
    "brand",
    "site",
    "catalog",
    "article",
    "products",
    "ingredients",
    "tea",
    "wellness",
    "草本茶",
    "产品",
    "原料",
    "内容",
    "品牌",
    "养生",
)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


@dataclass(frozen=True)
class IntentRoute:
    intent: str
    mode: str
    use_case: str
    mentioned_products: tuple[str, ...]
    mentioned_ingredients: tuple[str, ...]


@dataclass(frozen=True)
class DomainClassification:
    label: str
    allowed: bool
    reason: str


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _looks_like_math_problem(normalized: str) -> bool:
    if _contains_any(normalized, MATH_HINTS):
        return True
    return bool(
        re.search(r"\b\d+(?:\.\d+)?\s*[%+\-*/x×÷]\s*\d+(?:\.\d+)?\b", normalized)
        or re.search(r"\bwhat is \d+(?:\.\d+)?\s*[+\-*/x×÷]\s*\d+(?:\.\d+)?\b", normalized)
    )


def _looks_like_casual_conversation(normalized: str) -> bool:
    return _contains_any(normalized, CASUAL_HINTS)


def _looks_like_brand_scope_chat(normalized: str) -> bool:
    if _looks_like_casual_conversation(normalized):
        return True
    return _contains_any(normalized, BRAND_SCOPE_HINTS)


def _looks_like_wellness_education(
    normalized: str,
    *,
    has_products: bool,
    has_ingredients: bool,
) -> bool:
    if has_products or has_ingredients:
        return False
    if not _contains_any(normalized, WELLNESS_EDUCATION_TOPICS):
        return False
    return _contains_any(normalized, WELLNESS_EDUCATION_VERBS)


def _alias_variants(value: str) -> set[str]:
    text = str(value or "").strip().lower()
    if not text:
        return set()
    pieces = {
        item.strip()
        for item in re.split(r"[,/;、]+", text)
        if item.strip()
    }
    pieces.add(text)
    return {item for item in pieces if len(item) >= 2}


def _mentioned_slugs(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    catalog = load_catalog_bundle()
    normalized = _normalize_text(text)
    product_hits: list[tuple[int, str]] = []
    ingredient_hits: list[tuple[int, str]] = []

    def first_position(aliases: set[str]) -> int | None:
        positions = [normalized.find(alias) for alias in aliases if alias and normalized.find(alias) >= 0]
        return min(positions) if positions else None

    for product in catalog.products:
        aliases = {product.slug.lower(), product.name["zh"].lower(), product.name["en"].lower()}
        zh_name = product.name["zh"].strip().lower()
        en_name = product.name["en"].strip().lower()
        if len(zh_name) >= 4:
            aliases.update({zh_name[:4], zh_name[-4:], zh_name[1:]})
        if len(en_name) >= 8:
            aliases.update({en_name.split(" tea")[0], en_name.replace(" tea", "")})
        position = first_position(aliases)
        if position is not None:
            product_hits.append((position, product.slug))

    for ingredient in catalog.ingredients:
        aliases = {ingredient.slug.lower(), ingredient.name["zh"].lower(), ingredient.name["en"].lower()}
        aliases.update(_alias_variants(ingredient.aliases.get("zh", "")))
        aliases.update(_alias_variants(ingredient.aliases.get("en", "")))
        zh_name = ingredient.name["zh"].strip().lower()
        en_name = ingredient.name["en"].strip().lower()
        if len(zh_name) >= 3:
            aliases.add(zh_name[:-1])
        for suffix in (" root", " berry", " bud", " peel", " citrus"):
            if en_name.endswith(suffix):
                aliases.add(en_name[: -len(suffix)])
        position = first_position(aliases)
        if position is not None:
            ingredient_hits.append((position, ingredient.slug))

    product_hits.sort(key=lambda item: item[0])
    ingredient_hits.sort(key=lambda item: item[0])
    return tuple(dict.fromkeys(slug for _, slug in product_hits)), tuple(dict.fromkeys(slug for _, slug in ingredient_hits))


def route_intent(
    *,
    text: str,
    language: str,
    knowledge_base: KnowledgeBaseConfig,
) -> IntentRoute:
    normalized = _normalize_text(text)
    products, ingredients = _mentioned_slugs(text)

    if any(keyword in normalized for keyword in OUT_OF_SCOPE_PATTERNS) or (
        _looks_like_math_problem(normalized) and not (products or ingredients)
    ):
        return IntentRoute("out_of_scope", "fallback_safe", "", products, ingredients)

    if len(products) >= 2 or (products and _contains_any(normalized, COMPARE_HINTS)):
        return IntentRoute("compare_products", "compare_products", "product_comparison", products, ingredients)

    if _contains_any(normalized, ARTICLE_HINTS):
        return IntentRoute("article_request", "article_navigator", "article_recommendation", products, ingredients)

    if products and (_contains_any(normalized, PRODUCT_DETAIL_HINTS) or _contains_any(normalized, BREWING_HINTS)):
        return IntentRoute("product_detail", "product_detail", "daily_wellness", products, ingredients)

    if _contains_any(normalized, PRODUCT_LIST_HINTS):
        use_case = "gifting" if _contains_any(normalized, GIFT_HINTS) else "daily_wellness"
        return IntentRoute("product_catalog_request", "catalog_guide", use_case, products, ingredients)

    if products and _contains_any(normalized, GIFT_HINTS):
        return IntentRoute("product_detail", "product_detail", "gifting", products, ingredients)

    if _contains_any(normalized, GIFT_HINTS):
        return IntentRoute("gifting_recommendation", "gifting_guide", "gifting", products, ingredients)

    if ingredients and (_contains_any(normalized, INGREDIENT_HINTS) or not products):
        return IntentRoute("ingredient_explanation", "ingredient_explainer", "ingredient_learning", products, ingredients)

    if _contains_any(normalized, BREWING_HINTS):
        return IntentRoute("brewing_or_usage_question", "brand_scope_faq", "daily_wellness", products, ingredients)

    if _looks_like_wellness_education(
        normalized,
        has_products=bool(products),
        has_ingredients=bool(ingredients),
    ):
        return IntentRoute("wellness_education_in_scope", "wellness_education", "daily_wellness", products, ingredients)

    if _contains_any(normalized, CONSTITUTION_HINTS):
        return IntentRoute("constitution_guidance", "deep_guided_intake", "recent_discomfort_guidance", products, ingredients)

    if products or _contains_any(normalized, PRODUCT_HINTS):
        if _contains_any(normalized, SYMPTOM_HINTS):
            return IntentRoute("symptom_or_discomfort_guidance", "quick_recommendation", "recent_discomfort_guidance", products, ingredients)
        return IntentRoute("product_recommendation_direct", "quick_recommendation", "daily_wellness", products, ingredients)

    if _contains_any(normalized, SYMPTOM_HINTS):
        return IntentRoute("symptom_or_discomfort_guidance", "quick_recommendation", "recent_discomfort_guidance", products, ingredients)

    for intent_entry in knowledge_base.intents:
        zh_triggers = [str(item).strip().lower() for item in intent_entry.get("trigger_keywords_zh", [])]
        en_triggers = [str(item).strip().lower() for item in intent_entry.get("trigger_keywords_en", [])]
        if any(trigger and trigger in normalized for trigger in zh_triggers + en_triggers):
            return IntentRoute(
                str(intent_entry.get("id", "general_brand_scope_qna")).strip(),
                str(intent_entry.get("default_mode", "brand_scope_faq")).strip(),
                str(intent_entry.get("default_use_case", "daily_wellness")).strip(),
                products,
                ingredients,
            )

    if _looks_like_brand_scope_chat(normalized):
        return IntentRoute("general_brand_scope_qna", "brand_scope_faq", "daily_wellness", products, ingredients)

    return IntentRoute("out_of_scope", "fallback_safe", "", products, ingredients)


def classify_domain_intent(
    *,
    text: str,
    route: IntentRoute,
) -> DomainClassification:
    normalized = _normalize_text(text)

    if _looks_like_math_problem(normalized):
        return DomainClassification(label="OUT_OF_SCOPE", allowed=False, reason="math_or_discount")

    if _contains_any(normalized, OUT_OF_SCOPE_PATTERNS):
        return DomainClassification(label="OUT_OF_SCOPE", allowed=False, reason="non_domain_task")

    if _contains_any(normalized, GENERAL_TRIVIA_HINTS):
        return DomainClassification(label="OUT_OF_SCOPE", allowed=False, reason="general_trivia")

    if _looks_like_casual_conversation(normalized):
        return DomainClassification(label="CASUAL_CONVERSATION", allowed=True, reason="casual_chat")

    if route.intent == "out_of_scope":
        return DomainClassification(label="OUT_OF_SCOPE", allowed=False, reason="unsupported_topic")

    if route.intent in {"ingredient_explanation"} or (
        route.intent == "product_detail"
        and _contains_any(normalized, ("ingredient", "ingredients", "原料", "原材料", "成分", "配方", "每个原料", "what is in", "what ingredients are in"))
    ):
        return DomainClassification(label="INGREDIENT_QUERY", allowed=True, reason="ingredient_grounded")

    if route.intent in {
        "product_recommendation_direct",
        "gifting_recommendation",
        "compare_products",
        "product_catalog_request",
        "product_detail",
        "symptom_or_discomfort_guidance",
    }:
        return DomainClassification(label="PRODUCT_RECOMMENDATION", allowed=True, reason="product_or_purchase_guidance")

    if route.intent in {
        "constitution_guidance",
        "wellness_education_in_scope",
        "brewing_or_usage_question",
        "article_request",
    }:
        return DomainClassification(label="WELLNESS_QUESTION", allowed=True, reason="wellness_domain")

    if route.intent == "general_brand_scope_qna" and _looks_like_brand_scope_chat(normalized):
        return DomainClassification(label="CASUAL_CONVERSATION", allowed=True, reason="brand_scope_chat")

    return DomainClassification(label="OUT_OF_SCOPE", allowed=False, reason="strict_domain_fallback")
