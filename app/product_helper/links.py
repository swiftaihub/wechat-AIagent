from __future__ import annotations

from app.product_helper.config import LinkRoutingConfig
from app.product_helper.models import Article, CatalogBundle, Ingredient, LinkEntry, Product, ProductRecommendation


def _route_url(base_url: str, pattern: str, language: str, slug: str = "") -> str:
    resolved = pattern.replace("{locale}", language).replace("{slug}", slug)
    if not resolved.startswith("/"):
        resolved = f"/{resolved}"
    return f"{base_url}{resolved}"


def _entry_from_product(product: Product, language: str, base_url: str, routes: dict[str, str], override: dict[str, object]) -> LinkEntry:
    pattern = str(routes.get("product", "/{locale}/products/{slug}"))
    tags = tuple(
        dict.fromkeys(list(product.benefit_tags) + list(product.extra_tags) + [str(item).strip() for item in override.get("tags", []) if str(item).strip()])
    )
    return LinkEntry(
        id=f"product:{product.slug}",
        type="product",
        slug=product.slug,
        zh_title=product.name["zh"],
        en_title=product.name["en"],
        url=_route_url(base_url, pattern, language, product.slug),
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
    pattern = str(routes.get("ingredient", "/{locale}/ingredients/{slug}"))
    return LinkEntry(
        id=f"ingredient:{ingredient.slug}",
        type="ingredient",
        slug=ingredient.slug,
        zh_title=ingredient.name["zh"],
        en_title=ingredient.name["en"],
        url=_route_url(base_url, pattern, language, ingredient.slug),
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
    pattern = str(routes.get("article", "/{locale}/articles/{slug}"))
    tag_source = article.tags["en"] if language == "en" else article.tags["zh"]
    return LinkEntry(
        id=f"article:{article.slug}",
        type="article",
        slug=article.slug,
        zh_title=article.title["zh"],
        en_title=article.title["en"],
        url=_route_url(base_url, pattern, language, article.slug),
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
        entries.append(
            _entry_from_product(product, language, config.base_url, config.routes, config.product_overrides.get(product.slug, {}))
        )
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
    max_links = int(config.selection_rules.get("max_links_default", 2) or 2)
    selected: list[LinkEntry] = []
    recommendation_slugs = [item.product.slug for item in product_recommendations]

    if intent in {"product_recommendation_direct", "symptom_or_discomfort_guidance", "gifting_recommendation", "compare_products"}:
        for entry in entries:
            if entry.type == "product" and entry.slug in (tuple(recommendation_slugs) + tuple(mentioned_products)):
                selected.append(entry)

    if intent == "product_catalog_request":
        preferred_collection = "gifting" if use_case == "gifting" else "all"
        for entry in entries:
            if entry.type == "collection" and entry.slug == preferred_collection:
                selected.append(entry)
                break
        if not selected:
            for entry in entries:
                if entry.type == "collection" and entry.slug == "all":
                    selected.append(entry)
                    break

    if intent == "ingredient_explanation":
        for entry in entries:
            if entry.type == "ingredient" and entry.slug in mentioned_ingredients:
                selected.append(entry)
                break
        if not selected and product_recommendations:
            lead = product_recommendations[0].product
            if lead.ingredients:
                target_slug = lead.ingredients[0]
                for entry in entries:
                    if entry.type == "ingredient" and entry.slug == target_slug:
                        selected.append(entry)
                        break

    if intent in {"article_request", "ingredient_explanation", "brewing_or_usage_question", "general_brand_scope_qna"}:
        article_candidates = [
            entry
            for entry in entries
            if entry.type == "article"
            and (
                set(entry.related_products) & set(recommendation_slugs + list(mentioned_products))
                or set(entry.related_ingredients) & set(mentioned_ingredients)
                or use_case in entry.use_cases
            )
        ]
        article_candidates.sort(key=lambda item: item.priority, reverse=True)
        if article_candidates:
            selected.append(article_candidates[0])

    deduped: list[LinkEntry] = []
    seen_ids: set[str] = set()
    for entry in sorted(selected, key=lambda item: item.priority, reverse=True):
        if entry.id in seen_ids:
            continue
        seen_ids.add(entry.id)
        deduped.append(entry)
        if len(deduped) >= max_links:
            break
    return tuple(deduped)
