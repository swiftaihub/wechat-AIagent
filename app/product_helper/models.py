from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


LocalizedText = dict[str, str]


@dataclass(frozen=True)
class Product:
    slug: str
    name: LocalizedText
    tagline: LocalizedText
    summary: LocalizedText
    category: str
    price: float
    currency: str
    size: str
    ingredients: tuple[str, ...]
    benefit_tags: tuple[str, ...]
    flavor_notes: LocalizedText
    brew_guide: LocalizedText
    constitution_types: tuple[str, ...]
    recent_discomforts: tuple[str, ...]
    target_users: LocalizedText
    cautions: LocalizedText
    disclaimer: LocalizedText
    buy_link: str
    status: str
    images: tuple[str, ...] = ()
    extra_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Ingredient:
    slug: str
    name: LocalizedText
    aliases: LocalizedText
    summary: LocalizedText
    nutrition_focus: LocalizedText
    traditional_use: LocalizedText
    flavor_profile: LocalizedText
    pairings: tuple[str, ...]
    cautions: LocalizedText
    images: tuple[str, ...] = ()


@dataclass(frozen=True)
class Article:
    slug: str
    title: LocalizedText
    excerpt: LocalizedText
    category: LocalizedText
    tags: LocalizedText
    cover_image: str
    featured: bool
    published_at: str
    reading_theme: str
    related_products: tuple[str, ...]
    related_ingredients: tuple[str, ...]
    source_dir: Path


@dataclass(frozen=True)
class CatalogBundle:
    products: tuple[Product, ...]
    ingredients: tuple[Ingredient, ...]
    articles: tuple[Article, ...]
    products_by_slug: dict[str, Product]
    ingredients_by_slug: dict[str, Ingredient]
    articles_by_slug: dict[str, Article]


@dataclass(frozen=True)
class ConstitutionCandidate:
    constitution: str
    label: LocalizedText
    score: float
    confidence: str
    evidence: tuple[str, ...]
    description: LocalizedText


@dataclass(frozen=True)
class ConstitutionAssessment:
    candidates: tuple[ConstitutionCandidate, ...]
    summary: LocalizedText
    signal_summary: tuple[str, ...]
    confidence: str
    conservative_note: LocalizedText


@dataclass(frozen=True)
class ProductRecommendation:
    product: Product
    score: float
    why: tuple[str, ...]
    taste: str
    when_to_drink: str
    caution: str
    alternative: str = ""


@dataclass(frozen=True)
class LinkEntry:
    id: str
    type: str
    slug: str
    zh_title: str
    en_title: str
    url: str
    tags: tuple[str, ...]
    related_constitutions: tuple[str, ...]
    related_discomforts: tuple[str, ...]
    related_ingredients: tuple[str, ...]
    related_products: tuple[str, ...]
    use_cases: tuple[str, ...]
    funnel_stage: str
    priority: int = 0


@dataclass
class SessionState:
    language: str = "zh"
    intake: dict[str, Any] = field(default_factory=dict)
    current_use_case: str = ""
    current_intent: str = ""
    shortlisted_products: list[str] = field(default_factory=list)
    shortlisted_ingredients: list[str] = field(default_factory=list)
    last_constitutions: list[str] = field(default_factory=list)
    last_question: str = ""
    updated_at: float = 0.0


@dataclass(frozen=True)
class HelperResult:
    language: str
    intent: str
    mode: str
    reply: str
    needs_followup: bool
    followup_questions: tuple[str, ...]
    intake_state: dict[str, Any]
    constitution_assessment: ConstitutionAssessment | None
    product_recommendations: tuple[ProductRecommendation, ...]
    support_links: tuple[LinkEntry, ...]
    safety_notes: tuple[str, ...]
    metadata: dict[str, Any]
