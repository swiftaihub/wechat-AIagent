from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.i18n import normalize_localized_text
from app.product_helper.catalog_links import normalize_catalog_links
from app.product_helper.models import Article, CatalogBundle, Ingredient, Product


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_product_repo_root() -> Path:
    return (_repo_root().parent.parent / "herbal_advice_product_demo").resolve()


def _bundled_catalog_root() -> Path:
    return (_repo_root() / "brand_catalog").resolve()


def _resolve_external_path(env_key: str, relative_default: str, bundled_relative: str) -> Path:
    configured = os.getenv(env_key, "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return candidate
        return (_repo_root() / candidate).resolve()

    sibling_path = (_default_product_repo_root() / relative_default).resolve()
    if sibling_path.exists():
        return sibling_path

    bundled_path = (_bundled_catalog_root() / bundled_relative).resolve()
    return bundled_path


def _load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _localized_list(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        zh_items = value.get("zh", [])
        en_items = value.get("en", [])
        zh = "、".join(str(item).strip() for item in zh_items if str(item).strip())
        en = ", ".join(str(item).strip() for item in en_items if str(item).strip())
        return {"zh": zh, "en": en or zh}
    if isinstance(value, list):
        text = "、".join(str(item).strip() for item in value if str(item).strip())
        return {"zh": text, "en": text}
    return normalize_localized_text(value)


def _tuple_from(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _product_extra_tags(raw_product: dict[str, Any]) -> tuple[str, ...]:
    tags: list[str] = []
    price = float(raw_product.get("price", 0) or 0)
    if price >= 39:
        tags.append("premium")
    if price >= 35:
        tags.append("gift-worthy")
    if "premium" in _tuple_from(raw_product.get("benefit_tags", [])):
        tags.append("gift-worthy")
    return tuple(dict.fromkeys(tags))


@lru_cache(maxsize=1)
def load_catalog_bundle() -> CatalogBundle:
    product_path = _resolve_external_path("PRODUCT_CATALOG_PATH", "products.json", "products.json")
    ingredient_path = _resolve_external_path("INGREDIENT_CATALOG_PATH", "ingredients.json", "ingredients.json")
    article_root = _resolve_external_path("ARTICLE_CONTENT_ROOT", "content/articles", "articles")

    product_rows = _load_json_file(product_path)
    ingredient_rows = _load_json_file(ingredient_path)

    if not isinstance(product_rows, list) or not isinstance(ingredient_rows, list):
        raise ValueError("Product and ingredient catalogs must be arrays.")

    products: list[Product] = []
    for row in product_rows:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug", "")).strip()
        product_links = normalize_catalog_links(
            row.get("links"),
            kind="product",
            slug=slug,
            warn_scope="catalog_product",
        )
        product = Product(
            slug=slug,
            name=normalize_localized_text(row.get("name", "")),
            tagline=normalize_localized_text(row.get("tagline", "")),
            summary=normalize_localized_text(row.get("summary", "")),
            category=str(row.get("category", "")).strip(),
            price=float(row.get("price", 0) or 0),
            currency=str(row.get("currency", "USD")).strip() or "USD",
            size=str(row.get("size", "")).strip(),
            ingredients=_tuple_from(row.get("ingredients", [])),
            benefit_tags=_tuple_from(row.get("benefit_tags", [])),
            flavor_notes=_localized_list(row.get("flavor_notes", "")),
            brew_guide=normalize_localized_text(row.get("brew_guide", "")),
            constitution_types=_tuple_from(row.get("constitution_types", [])),
            recent_discomforts=_tuple_from(row.get("recent_discomforts", [])),
            target_users=_localized_list(row.get("target_users", "")),
            cautions=normalize_localized_text(row.get("cautions", "")),
            disclaimer=normalize_localized_text(row.get("disclaimer", "")),
            links=product_links,
            buy_link=product_links["en"],
            status=str(row.get("status", "active")).strip() or "active",
            images=_tuple_from(row.get("images", [])),
            extra_tags=_product_extra_tags(row),
        )
        if product.slug:
            products.append(product)

    ingredients: list[Ingredient] = []
    for row in ingredient_rows:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug", "")).strip()
        ingredient_links = normalize_catalog_links(
            row.get("links"),
            kind="ingredient",
            slug=slug,
            warn_scope="catalog_ingredient",
        )
        ingredient = Ingredient(
            slug=slug,
            name=normalize_localized_text(row.get("name", "")),
            aliases=_localized_list(row.get("aliases", "")),
            summary=normalize_localized_text(row.get("summary", "")),
            nutrition_focus=_localized_list(row.get("nutrition_focus", "")),
            traditional_use=normalize_localized_text(row.get("traditional_use", "")),
            flavor_profile=_localized_list(row.get("flavor_profile", "")),
            pairings=_tuple_from(row.get("pairings", [])),
            cautions=normalize_localized_text(row.get("cautions", "")),
            links=ingredient_links,
            images=_tuple_from(row.get("images", [])),
        )
        if ingredient.slug:
            ingredients.append(ingredient)

    articles: list[Article] = []
    if article_root.exists():
        for meta_path in sorted(article_root.glob("*/meta.json")):
            meta = _load_json_file(meta_path)
            if not isinstance(meta, dict):
                continue
            slug = str(meta.get("slug", meta_path.parent.name)).strip()
            article_links = normalize_catalog_links(
                meta.get("links"),
                kind="article",
                slug=slug,
                warn_scope="catalog_article",
            )
            article = Article(
                slug=slug,
                title=normalize_localized_text(meta.get("title", "")),
                excerpt=normalize_localized_text(meta.get("excerpt", "")),
                category=normalize_localized_text(meta.get("category", "")),
                tags=_localized_list(meta.get("tags", "")),
                links=article_links,
                cover_image=str(meta.get("coverImage", "")).strip(),
                featured=bool(meta.get("featured", False)),
                published_at=str(meta.get("publishedAt", "")).strip(),
                reading_theme=str(meta.get("readingTheme", "serif")).strip() or "serif",
                related_products=_tuple_from(meta.get("relatedProducts", [])),
                related_ingredients=_tuple_from(meta.get("relatedIngredients", [])),
                source_dir=meta_path.parent,
            )
            if article.slug:
                articles.append(article)

    products_by_slug = {product.slug: product for product in products}
    ingredients_by_slug = {ingredient.slug: ingredient for ingredient in ingredients}
    articles_by_slug = {article.slug: article for article in articles}
    return CatalogBundle(
        products=tuple(products),
        ingredients=tuple(ingredients),
        articles=tuple(articles),
        products_by_slug=products_by_slug,
        ingredients_by_slug=ingredients_by_slug,
        articles_by_slug=articles_by_slug,
    )


def reload_catalog_bundle() -> CatalogBundle:
    load_catalog_bundle.cache_clear()
    return load_catalog_bundle()
