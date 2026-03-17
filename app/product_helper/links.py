from __future__ import annotations

from app.product_helper.catalog_links import localized_catalog_link
from app.product_helper.config import LinkRoutingConfig
from app.product_helper.models import Article, CatalogBundle, Ingredient, LinkEntry, Product, ProductRecommendation


def _route_url(base_url: str, pattern: str, language: str, slug: str = "") -> str:
    resolved = pattern.replace("{locale}", language).replace("{slug}", slug)
    if not resolved.startswith("/"):
        resolved = f"/{resolved}"
    return f"{base_url}{resolved}"


def _is_http_url(url: str) -> bool:
    normalized = str(url or "").strip().lower()
    return (normalized.startswith("https://") or normalized.startswith("http://")) and not any(
        hint in normalized for hint in ("your-store", "example.com", "example.org", "example.net", "placeholder")
    )


def _entry_from_product(product: Product, language: str, base_url: str, routes: dict[str, str], override: dict[str, object]) -> LinkEntry | None:
    product_url = localized_catalog_link(product.links, language) or str(product.buy_link).strip()
    if not _is_http_url(product_url):
        return None
    tags = tuple(
        dict.fromkeys(list(product.benefit_tags) + list(product.extra_tags) + [str(item).strip() for item in override.get("tags", []) if str(item).strip()])
    )
    return LinkEntry(
        id=f"product:{product.slug}",
        type="product",
        slug=product.slug,
        zh_title=product.name["zh"],
        en_title=product.name["en"],
        url=product_url,
        tags=tags,
        related_constitutions=product.constitution_types,
        related_discomforts=product.recent_discomforts,
        related_ingredients=product.ingredients,
        related_products=(product.slug,),
        use_cases=tuple(str(item).strip() for item in override.get("use_cases", []) if str(item).strip()),
        funnel_stage=str(override.get("funnel_stage", "purchase")),
        priority=int(override.get("priority", 0) or 0),
    )


def _entry_from_ingredient(ingredient: Ingredient, language: str, base_url: str, routes: dict[str, str], override: dict[str, object]) -> LinkEntry:
    ingredient_url = localized_catalog_link(ingredient.links, language) or _route_url(
        base_url,
        str(routes.get("ingredient", "/{locale}/ingredients/{slug}")),
        language,
        ingredient.slug,
    )
    return LinkEntry(
        id=f"ingredient:{ingredient.slug}",
        type="ingredient",
        slug=ingredient.slug,
        zh_title=ingredient.name["zh"],
        en_title=ingredient.name["en"],
        url=ingredient_url,
        tags=tuple(str(item).strip() for item in override.get("tags", []) if str(item).strip()),
        related_constitutions=tuple(str(item).strip() for item in override.get("related_constitutions", []) if str(item).strip()),
        related_discomforts=tuple(str(item).strip() for item in override.get("related_discomforts", []) if str(item).strip()),
        related_ingredients=(ingredient.slug,),
        related_products=tuple(str(item).strip() for item in override.get("related_products", []) if str(item).strip()),
        use_cases=tuple(str(item).strip() for item in override.get("use_cases", []) if str(item).strip()),
        funnel_stage=str(override.get("funnel_stage", "education")),
        priority=int(override.get("priority", 0) or 0),
    )


def _entry_from_article(article: Article, language: str, base_url: str, routes: dict[str, str], override: dict[str, object]) -> LinkEntry:
    article_url = localized_catalog_link(article.links, language) or _route_url(
        base_url,
        str(routes.get("article", "/{locale}/articles/{slug}")),
        language,
        article.slug,
    )
    tag_source = article.tags["en"] if language == "en" else article.tags["zh"]
    return LinkEntry(
        id=f"article:{article.slug}",
        type="article",
        slug=article.slug,
        zh_title=article.title["zh"],
        en_title=article.title["en"],
        url=article_url,
        tags=tuple(item.strip() for item in tag_source.replace("、", ",").split(",") if item.strip()),
        related_constitutions=tuple(str(item).strip() for item in override.get("related_constitutions", []) if str(item).strip()),
        related_discomforts=tuple(str(item).strip() for item in override.get("related_discomforts", []) if str(item).strip()),
        related_ingredients=article.related_ingredients,
        related_products=article.related_products,
        use_cases=tuple(str(item).strip() for item in override.get("use_cases", []) if str(item).strip()),
        funnel_stage=str(override.get("funnel_stage", "education")),
        priority=int(override.get("priority", 0) or 0),
    )


def build_link_entries(bundle: CatalogBundle, config: LinkRoutingConfig, language: str) -> tuple[LinkEntry, ...]:
    entries: list[LinkEntry] = []
    for product in bundle.products:
        entry = _entry_from_product(product, language, config.base_url, config.routes, config.product_overrides.get(product.slug, {}))
        if entry is not None:
            entries.append(entry)
    for ingredient in bundle.ingredients:
        entries.append(
            _entry_from_ingredient(ingredient, language, config.base_url, config.routes, config.ingredient_overrides.get(ingredient.slug, {}))
        )
    for article in bundle.articles:
        entries.append(
            _entry_from_article(article, language, config.base_url, config.routes, config.article_overrides.get(article.slug, {}))
        )
    for collection in config.collections:
        slug = str(collection.get("slug", "")).strip()
        if not slug:
            continue
        entries.append(
            LinkEntry(
                id=f"collection:{slug}",
                type="collection",
                slug=slug,
                zh_title=str(collection.get("zh_title", slug)).strip(),
                en_title=str(collection.get("en_title", slug)).strip(),
                url=_route_url(config.base_url, str(config.routes.get("collection", "/{locale}/shop/{slug}")), language, slug),
                tags=tuple(str(item).strip() for item in collection.get("tags", []) if str(item).strip()),
                related_constitutions=tuple(str(item).strip() for item in collection.get("related_constitutions", []) if str(item).strip()),
                related_discomforts=tuple(str(item).strip() for item in collection.get("related_discomforts", []) if str(item).strip()),
                related_ingredients=tuple(str(item).strip() for item in collection.get("related_ingredients", []) if str(item).strip()),
                related_products=tuple(str(item).strip() for item in collection.get("related_products", []) if str(item).strip()),
                use_cases=tuple(str(item).strip() for item in collection.get("use_cases", []) if str(item).strip()),
                funnel_stage=str(collection.get("funnel_stage", "discovery")),
                priority=int(collection.get("priority", 0) or 0),
            )
        )
    return tuple(entries)


def select_supporting_links(
    *,
    bundle: CatalogBundle,
    config: LinkRoutingConfig,
    language: str,
    intent: str,
    use_case: str,
    product_recommendations: tuple[ProductRecommendation, ...],
    mentioned_products: tuple[str, ...],
    mentioned_ingredients: tuple[str, ...],
) -> tuple[LinkEntry, ...]:
    entries = build_link_entries(bundle, config, language)
    default_max_links = int(config.selection_rules.get("max_links_default", 2) or 2)
    max_links = 1 if intent in {"product_detail", "ingredient_explanation", "article_request", "wellness_education_in_scope", "brewing_or_usage_question", "general_brand_scope_qna"} else default_max_links
    selected: list[LinkEntry] = []
    seen_ids: set[str] = set()
    recommendation_slugs = [item.product.slug for item in product_recommendations]
    entry_by_key = {(entry.type, entry.slug): entry for entry in entries}

    def append_entry(entry: LinkEntry | None) -> None:
        if entry is None or entry.id in seen_ids:
            return
        seen_ids.add(entry.id)
        selected.append(entry)

    def append_product_slugs(slugs: tuple[str, ...] | list[str]) -> None:
        for slug in slugs:
            append_entry(entry_by_key.get(("product", slug)))

    def append_ingredient_slugs(slugs: tuple[str, ...] | list[str]) -> None:
        for slug in slugs:
            append_entry(entry_by_key.get(("ingredient", slug)))

    def best_article() -> LinkEntry | None:
        article_candidates: list[tuple[int, LinkEntry]] = []
        mentioned_product_set = set(mentioned_products)
        mentioned_ingredient_set = set(mentioned_ingredients)
        recommendation_set = set(recommendation_slugs)
        for entry in entries:
            if entry.type != "article":
                continue
            score = entry.priority
            if set(entry.related_products) & mentioned_product_set:
                score += 50
            if set(entry.related_ingredients) & mentioned_ingredient_set:
                score += 50
            if set(entry.related_products) & recommendation_set:
                score += 20
            if use_case and use_case in entry.use_cases:
                score += 8
            if score > entry.priority:
                article_candidates.append((score, entry))
        if not article_candidates:
            return None
        article_candidates.sort(key=lambda item: item[0], reverse=True)
        return article_candidates[0][1]

    if intent == "compare_products":
        append_product_slugs(mentioned_products)
        append_product_slugs(recommendation_slugs)

    if intent == "product_detail":
        append_product_slugs(mentioned_products or recommendation_slugs)

    if intent in {"product_recommendation_direct", "symptom_or_discomfort_guidance", "gifting_recommendation", "constitution_guidance"}:
        append_product_slugs(recommendation_slugs)
        append_product_slugs(mentioned_products)

    if intent == "product_catalog_request":
        preferred_collection = "gifting" if use_case == "gifting" else "all"
        append_entry(entry_by_key.get(("collection", preferred_collection)))
        append_entry(entry_by_key.get(("collection", "all")))

    if intent == "ingredient_explanation":
        append_ingredient_slugs(mentioned_ingredients)
        if not selected and product_recommendations:
            lead = product_recommendations[0].product
            if lead.ingredients:
                append_entry(entry_by_key.get(("ingredient", lead.ingredients[0])))

    if intent in {"article_request", "ingredient_explanation", "brewing_or_usage_question", "general_brand_scope_qna", "wellness_education_in_scope"}:
        append_entry(best_article())

    return tuple(selected[:max_links])
