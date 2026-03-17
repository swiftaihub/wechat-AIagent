from __future__ import annotations

import logging
import re
from dataclasses import replace

from app.product_helper.catalog_links import (
    localized_catalog_link,
    validate_catalog_links,
)
from app.product_helper.content import load_catalog_bundle
from app.product_helper.models import HelperResult, LinkEntry, ProductRecommendation


logger = logging.getLogger(__name__)

_MARKDOWN_URL_PATTERN = re.compile(r"\[[^\]]+\]\((https?://[^\s)]+)\)")
_HTML_URL_PATTERN = re.compile(r"""href=["'](https?://[^"']+)["']""", re.IGNORECASE)
_RAW_URL_PATTERN = re.compile(r"https?://[^\s)>\"]+")
_ENGLISH_TEA_NAME_PATTERN = re.compile(r"\b([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+){0,5} Tea)\b")
_PLACEHOLDER_URL_HINTS = ("your-store", "example.com", "example.org", "example.net", "placeholder")
_EMPTY_BULLET_PATTERN = re.compile(r"^\s*[-*]\s*$")
_LINK_STUB_PATTERN = re.compile(
    r"^\s*(?:[-*]\s*)?(?:click here|view product|buy now|shop now|product link|查看产品|查看商品|商品链接|产品链接|点击这里|购买链接)\s*:?\s*$",
    re.IGNORECASE,
)


def _is_http_url(url: str) -> bool:
    normalized = str(url or "").strip().lower()
    return (normalized.startswith("https://") or normalized.startswith("http://")) and not any(
        hint in normalized for hint in _PLACEHOLDER_URL_HINTS
    )


def validate_product_recommendations(
    recommendations: tuple[ProductRecommendation, ...],
) -> tuple[ProductRecommendation, ...]:
    bundle = load_catalog_bundle()
    validated: list[ProductRecommendation] = []
    seen_slugs: set[str] = set()

    for recommendation in recommendations:
        product = bundle.products_by_slug.get(recommendation.product.slug)
        if product is None or product.status != "active" or product.slug in seen_slugs:
            continue
        seen_slugs.add(product.slug)
        validated.append(replace(recommendation, product=product))

    return tuple(validated)


def validate_support_links(links: tuple[LinkEntry, ...]) -> tuple[LinkEntry, ...]:
    bundle = load_catalog_bundle()
    validated: list[LinkEntry] = []
    seen_ids: set[str] = set()

    for link in links:
        normalized: LinkEntry | None = None

        if link.type == "product":
            product = bundle.products_by_slug.get(link.slug)
            valid_links, reason = validate_catalog_links(product.links if product is not None else None, kind="product", slug=link.slug)
            if product is None or not valid_links:
                if product is not None and reason is not None:
                    logger.warning("Skipping product link slug=%s reason=%s", link.slug, reason)
                continue
            product_url = localized_catalog_link(product.links, "zh" if "/zh/" in str(link.url) else "en")
            if not _is_http_url(product_url):
                continue
            normalized = replace(
                link,
                zh_title=product.name["zh"],
                en_title=product.name["en"],
                url=product_url,
                related_products=(product.slug,),
                related_ingredients=product.ingredients,
            )
        elif link.type == "ingredient":
            ingredient = bundle.ingredients_by_slug.get(link.slug)
            valid_links, reason = validate_catalog_links(ingredient.links if ingredient is not None else None, kind="ingredient", slug=link.slug)
            if ingredient is None or not valid_links:
                if ingredient is not None and reason is not None:
                    logger.warning("Skipping ingredient link slug=%s reason=%s", link.slug, reason)
                continue
            ingredient_url = localized_catalog_link(ingredient.links, "zh" if "/zh/" in str(link.url) else "en")
            if not _is_http_url(ingredient_url):
                continue
            normalized = replace(
                link,
                zh_title=ingredient.name["zh"],
                en_title=ingredient.name["en"],
                url=ingredient_url,
                related_ingredients=(ingredient.slug,),
            )
        elif link.type == "article":
            article = bundle.articles_by_slug.get(link.slug)
            valid_links, reason = validate_catalog_links(article.links if article is not None else None, kind="article", slug=link.slug)
            if article is None or not valid_links:
                if article is not None and reason is not None:
                    logger.warning("Skipping article link slug=%s reason=%s", link.slug, reason)
                continue
            article_url = localized_catalog_link(article.links, "zh" if "/zh/" in str(link.url) else "en")
            if not _is_http_url(article_url):
                continue
            normalized = replace(
                link,
                zh_title=article.title["zh"],
                en_title=article.title["en"],
                url=article_url,
                related_products=article.related_products,
                related_ingredients=article.related_ingredients,
            )
        elif _is_http_url(link.url):
            normalized = link

        if normalized is None or normalized.id in seen_ids:
            continue

        seen_ids.add(normalized.id)
        validated.append(normalized)

    return tuple(validated)


def sanitize_helper_result(result: HelperResult) -> HelperResult:
    return replace(
        result,
        product_recommendations=validate_product_recommendations(result.product_recommendations),
        support_links=validate_support_links(result.support_links),
    )


def _extract_reply_urls(text: str) -> tuple[str, ...]:
    normalized = str(text or "")
    urls: list[str] = []
    for pattern in (_MARKDOWN_URL_PATTERN, _HTML_URL_PATTERN, _RAW_URL_PATTERN):
        for match in pattern.findall(normalized):
            if match not in urls:
                urls.append(match)
    return tuple(urls)


def _allowed_reply_urls(result: HelperResult) -> set[str]:
    bundle = load_catalog_bundle()
    allowed = {
        str(link.url).strip()
        for link in result.support_links
        if _is_http_url(link.url)
    }

    for recommendation in result.product_recommendations:
        product = bundle.products_by_slug.get(recommendation.product.slug)
        if product is None:
            continue
        for language in ("zh", "en"):
            product_link = localized_catalog_link(product.links, language)
            if _is_http_url(product_link):
                allowed.add(product_link)

    route = result.metadata.get("route")
    mentioned_products = getattr(route, "mentioned_products", ()) if route is not None else ()
    for slug in mentioned_products:
        product = bundle.products_by_slug.get(str(slug).strip())
        if product is None:
            continue
        for language in ("zh", "en"):
            product_link = localized_catalog_link(product.links, language)
            if _is_http_url(product_link):
                allowed.add(product_link)

    return allowed


def _allowed_product_names(result: HelperResult) -> set[str]:
    bundle = load_catalog_bundle()
    allowed: set[str] = set()

    for recommendation in result.product_recommendations:
        allowed.update(
            {
                recommendation.product.name["zh"],
                recommendation.product.name["en"],
            }
        )

    route = result.metadata.get("route")
    mentioned_products = getattr(route, "mentioned_products", ()) if route is not None else ()
    for slug in mentioned_products:
        product = bundle.products_by_slug.get(str(slug).strip())
        if product is None:
            continue
        allowed.update({product.name["zh"], product.name["en"]})

    return {name for name in allowed if str(name).strip()}


def validate_final_reply(reply: str, result: HelperResult) -> tuple[bool, str | None]:
    normalized = str(reply or "").strip()
    if not normalized:
        return False, "empty_reply"

    reply_urls = _extract_reply_urls(normalized)
    allowed_urls = _allowed_reply_urls(result)
    for url in reply_urls:
        if url not in allowed_urls:
            return False, "unknown_url"

    if not bool(result.metadata.get("grounding_required", False)):
        return True, None

    bundle = load_catalog_bundle()
    allowed_names = _allowed_product_names(result)
    if allowed_names:
        for product in bundle.products:
            names = {product.name["zh"], product.name["en"]}
            if names & allowed_names:
                continue
            if any(name and name in normalized for name in names):
                return False, "unexpected_catalog_product"

    for candidate in _ENGLISH_TEA_NAME_PATTERN.findall(normalized):
        if candidate not in allowed_names:
            return False, "unknown_product_name"

    return True, None


def sanitize_reply_links(reply: str, result: HelperResult) -> str:
    normalized = str(reply or "").strip()
    if not normalized:
        return normalized

    allowed_urls = _allowed_reply_urls(result)

    def replace_markdown(match: re.Match[str]) -> str:
        url = match.group(1)
        return match.group(0) if url in allowed_urls else ""

    def replace_html(match: re.Match[str]) -> str:
        return match.group(0) if match.group(1) in allowed_urls else ""

    def replace_raw(match: re.Match[str]) -> str:
        return match.group(0) if match.group(0) in allowed_urls else ""

    sanitized = _MARKDOWN_URL_PATTERN.sub(replace_markdown, normalized)
    sanitized = _HTML_URL_PATTERN.sub(replace_html, sanitized)
    sanitized = _RAW_URL_PATTERN.sub(replace_raw, sanitized)
    cleaned_lines: list[str] = []
    for line in sanitized.splitlines():
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append("")
            continue
        if _EMPTY_BULLET_PATTERN.match(stripped) or _LINK_STUB_PATTERN.match(stripped):
            continue
        cleaned_lines.append(re.sub(r"\s{2,}", " ", line).rstrip())
    sanitized = "\n".join(cleaned_lines)
    sanitized = re.sub(r"[ \t]+\n", "\n", sanitized)
    sanitized = re.sub(r"\n\s*[:：]\s*$", "\n", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    return sanitized.strip()
