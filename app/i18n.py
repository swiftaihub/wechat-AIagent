from typing import Any, Mapping


SUPPORTED_LANGUAGES = ("zh", "en")


def normalize_language(value: str | None, default: str = "zh") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"zh", "zh-cn", "zh_hans", "chinese", "中文"}:
        return "zh"
    if normalized in {"en", "en-us", "english"}:
        return "en"
    return default


def normalize_localized_text(
    value: Any,
    *,
    fallback: str = "",
) -> dict[str, str]:
    if isinstance(value, Mapping):
        zh = str(value.get("zh", "")).strip()
        en = str(value.get("en", "")).strip()
        default = str(value.get("default", "")).strip()
        seed = fallback.strip() or default or zh or en
        return {
            "zh": zh or seed or en,
            "en": en or seed or zh,
        }

    text = str(value or "").strip() or fallback.strip()
    return {"zh": text, "en": text}


def resolve_localized_text(
    value: Any,
    language: str | None = None,
    *,
    fallback: str = "",
) -> str:
    localized = normalize_localized_text(value, fallback=fallback)
    lang = normalize_language(language)
    if lang == "en":
        return localized["en"] or localized["zh"] or fallback
    return localized["zh"] or localized["en"] or fallback


def normalize_localized_list(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool = False,
) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list):
        raise ValueError(f"Field '{field_name}' must be a list.")

    normalized: list[dict[str, str]] = []
    for index, item in enumerate(value):
        localized = normalize_localized_text(item)
        if localized["zh"] or localized["en"]:
            normalized.append(localized)
        elif not allow_empty:
            raise ValueError(f"Field '{field_name}[{index}]' cannot be empty.")

    if not allow_empty and not normalized:
        raise ValueError(f"Field '{field_name}' cannot be empty.")
    return tuple(normalized)


def localized_terms(value: Any) -> tuple[str, ...]:
    localized = normalize_localized_text(value)
    terms: list[str] = []
    for language in SUPPORTED_LANGUAGES:
        text = localized.get(language, "").strip()
        if text and text not in terms:
            terms.append(text)
    return tuple(terms)
