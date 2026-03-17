from __future__ import annotations

import logging
from typing import Mapping

from app.i18n import normalize_language


logger = logging.getLogger(__name__)

CATALOG_BASE_URL = "https://tea.swiftaihub.com"
_SEGMENTS = {
    "product": "products",
    "ingredient": "ingredients",
    "article": "articles",
}


def expected_catalog_links(*, kind: str, slug: str) -> dict[str, str]:
    segment = _SEGMENTS[kind]
    normalized_slug = str(slug or "").strip()
    if not normalized_slug:
        return {"zh": "", "en": ""}
    return {
        "zh": f"{CATALOG_BASE_URL}/zh/{segment}/{normalized_slug}",
        "en": f"{CATALOG_BASE_URL}/en/{segment}/{normalized_slug}",
    }


def expected_product_links(slug: str) -> dict[str, str]:
    return expected_catalog_links(kind="product", slug=slug)


def expected_ingredient_links(slug: str) -> dict[str, str]:
    return expected_catalog_links(kind="ingredient", slug=slug)


def expected_article_links(slug: str) -> dict[str, str]:
    return expected_catalog_links(kind="article", slug=slug)


def normalize_catalog_links(
    raw_links: Mapping[str, object] | None,
    *,
    kind: str,
    slug: str,
    warn_scope: str,
) -> dict[str, str]:
    expected = expected_catalog_links(kind=kind, slug=slug)
    if not slug:
        return expected

    normalized = {
        "zh": str((raw_links or {}).get("zh", "")).strip() if isinstance(raw_links, Mapping) else "",
        "en": str((raw_links or {}).get("en", "")).strip() if isinstance(raw_links, Mapping) else "",
    }
    for language, expected_url in expected.items():
        configured = normalized.get(language, "")
        if configured != expected_url:
            logger.warning(
                "%s link mismatch for %s slug=%s lang=%s configured=%s expected=%s",
                warn_scope,
                kind,
                slug,
                language,
                configured or "<missing>",
                expected_url,
            )
    return expected


def validate_catalog_links(links: Mapping[str, object] | None, *, kind: str, slug: str) -> tuple[bool, str | None]:
    if not str(slug or "").strip():
        return False, "missing_slug"
    expected = expected_catalog_links(kind=kind, slug=slug)
    if not isinstance(links, Mapping):
        return False, "missing_links"
    for language in ("zh", "en"):
        configured = str(links.get(language, "")).strip()
        if not configured:
            return False, f"missing_{language}_link"
        if configured != expected[language]:
            return False, f"{language}_link_mismatch"
    return True, None


def localized_catalog_link(links: Mapping[str, object] | None, language: str) -> str:
    if not isinstance(links, Mapping):
        return ""
    preferred = normalize_language(language, default="zh")
    fallback = "en" if preferred == "zh" else "zh"
    return str(links.get(preferred) or links.get(fallback) or "").strip()
