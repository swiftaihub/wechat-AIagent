import asyncio
import html
import json
import logging
import os
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from app.llm_core import generate_reply_result
from app.logging_utils import hash_identifier
from app.product_helper.content import load_catalog_bundle
from app.product_helper.intake import build_visible_intake_payload
from app.runtime_config import get_runtime_config
from app.tools.constitution_advice import (
    extract_recent_discomfort_options,
)

logger = logging.getLogger(__name__)

DEFAULT_REPLY_TIMEOUT_SECONDS = float(os.getenv("OPENCLAW_REPLY_TIMEOUT_SECONDS", "10"))
WEB_UI_TIMEOUT_TEXT = os.getenv(
    "WECHAT_SYNC_TIMEOUT_TEXT",
    "系统服务器正忙，请稍后再试。",
)
WEB_UI_ERROR_TEXT = os.getenv(
    "WECHAT_SYNC_ERROR_TEXT",
    "系统服务异常，请稍后再试。",
)
WEBUI_INTAKE_CONFIG_PATH = os.getenv(
    "WEBUI_INTAKE_CONFIG_PATH",
    "config/questionnaire.private.yaml",
).strip()


def _looks_ascii_text(value: str) -> bool:
    return bool(value) and value.isascii()


def _load_localized_runtime_text(
    env_key: str,
    *,
    default_zh: str,
    default_en: str,
) -> dict[str, str]:
    direct_value = os.getenv(env_key, "").strip()
    zh_value = os.getenv(f"{env_key}_ZH", "").strip()
    en_value = os.getenv(f"{env_key}_EN", "").strip()

    if not zh_value:
        zh_value = direct_value if direct_value and not _looks_ascii_text(direct_value) else default_zh
    if not en_value:
        en_value = direct_value if direct_value and _looks_ascii_text(direct_value) else default_en

    return {"zh": zh_value, "en": en_value}


WEBUI_WELCOME_MESSAGE = _load_localized_runtime_text(
    "WEBUI_WELCOME_MESSAGE",
    default_zh="欢迎来到品牌 AI Helper。你可以告诉我最近的状态、送礼方向，或想先了解哪类草本茶。",
    default_en="Welcome to the brand AI helper. Share how you have been feeling, what you might want to gift, or the tea direction you want to explore.",
)
WEBUI_TITLE = _load_localized_runtime_text(
    "WEBUI_TITLE",
    default_zh="草本茶推荐助手",
    default_en="Herbal Tea Recommendation Helper",
)


def _normalize_base_path(raw: str | None, default: str = "/ui") -> str:
    value = (raw or "").strip()
    if not value:
        return default
    if not value.startswith("/"):
        value = f"/{value}"
    value = value.rstrip("/")
    return value or default


WEBUI_BASE_PATH = _normalize_base_path(os.getenv("WEBUI_BASE_PATH", "/ui"))
WEBUI_API_BASE_URL = (
    os.getenv("WEBUI_API_BASE_URL", "").strip()
    or f"{WEBUI_BASE_PATH}/api/chat"
)
WEBUI_MAX_INPUT_CHARS = get_runtime_config().protection.max_input_chars
WEB_UI_ASSET_DIR = Path(__file__).with_name("web_ui_assets")
WEB_UI_TEMPLATE_PATH = WEB_UI_ASSET_DIR / "web_ui.html"
WEB_UI_ASSET_MAP: dict[str, tuple[Path, str]] = {
    "web_ui.css": (WEB_UI_ASSET_DIR / "web_ui.css", "text/css; charset=utf-8"),
    "web_ui.js": (WEB_UI_ASSET_DIR / "web_ui.js", "application/javascript; charset=utf-8"),
}


def _default_intake_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "auto_collapse_on_submit": True,
        "title": {"zh": "快速了解你的需求", "en": "Quick intake"},
        "description": {
            "zh": "先给 AI 一个大致方向，它会更容易把产品、原料和文章推荐收得更准。",
            "en": "Give the assistant a little context first so it can narrow products, ingredients, and articles more clearly.",
        },
        "submit_button": {"zh": "提交", "en": "Submit"},
        "reset_button": {"zh": "重置", "en": "Reset"},
        "submit_notice": {
            "zh": "基础信息已提交，正在整理更贴近你的推荐方向。",
            "en": "Information submitted. Shaping a more tailored recommendation path now.",
        },
        "fields": [],
    }


def _normalize_locale_label(value: Any, fallback: str = "") -> dict[str, str]:
    if isinstance(value, dict):
        zh = str(value.get("zh", "")).strip()
        en = str(value.get("en", "")).strip()
        if zh or en:
            return {
                "zh": zh or fallback or en,
                "en": en or fallback or zh,
            }

    text = str(value or "").strip()
    if not text:
        text = fallback
    return {"zh": text, "en": text}


def _coerce_localized_text(
    value: Any,
    *,
    default_zh: str = "",
    default_en: str = "",
) -> dict[str, str]:
    if isinstance(value, dict):
        return {
            "zh": str(value.get("zh", "")).strip() or default_zh,
            "en": str(value.get("en", "")).strip() or default_en,
        }

    text = str(value or "").strip()
    if not text:
        return {"zh": default_zh, "en": default_en}
    return {"zh": text, "en": text}


def _resolve_dynamic_field_options(field: dict[str, Any]) -> list[dict[str, Any]] | None:
    options_from = field.get("options_from")
    if not isinstance(options_from, dict):
        return None

    source = str(options_from.get("source", "")).strip().lower()
    try:
        if source == "herbal_advice_symptoms":
            dynamic_options = extract_recent_discomfort_options()
        elif source == "product_catalog":
            bundle = load_catalog_bundle()
            dynamic_options = [
                {
                    "value": product.slug,
                    "label": {
                        "zh": product.name["zh"],
                        "en": product.name["en"],
                    },
                }
                for product in bundle.products
                if product.status == "active"
            ]
        else:
            logger.warning("Unsupported intake options_from source '%s' for field '%s'", source, field.get("name", ""))
            return []
    except Exception as exc:
        logger.warning(
            "Failed to resolve dynamic intake options for field '%s': %s",
            field.get("name", ""),
            exc,
        )
        return []

    overrides_raw = field.get("option_labels", {})
    overrides = overrides_raw if isinstance(overrides_raw, dict) else {}

    options: list[dict[str, Any]] = []
    for option in dynamic_options:
        option_value = str(option.get("value", "")).strip()
        if not option_value:
            continue
        label = _normalize_locale_label(option.get("label"), fallback=option_value)
        override = overrides.get(option_value)
        if override is not None:
            label = _normalize_locale_label(
                {
                    "zh": str(getattr(override, "get", lambda *_: "")("zh", "")).strip() or label["zh"],
                    "en": str(getattr(override, "get", lambda *_: "")("en", "")).strip() or label["en"],
                },
                fallback=option_value,
            )
        options.append({"value": option_value, "label": label})
    return options


def _load_intake_config_from_path(config_path: str | Path | None) -> dict[str, Any]:
    fallback = _default_intake_config()
    raw_config_path = str(config_path or "").strip()
    if not raw_config_path:
        logger.warning("WEBUI_INTAKE_CONFIG_PATH is empty; intake disabled")
        return fallback

    primary = Path(raw_config_path)
    candidates = [primary]
    if primary.suffix.lower() == ".yaml":
        candidates.append(primary.with_suffix(".yml"))
    elif primary.suffix.lower() == ".yml":
        candidates.append(primary.with_suffix(".yaml"))

    loaded_data: dict[str, Any] | None = None
    loaded_path: Path | None = None

    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(parsed, dict):
                loaded_data = parsed
                loaded_path = path
                break
        except Exception as exc:
            logger.warning("Failed to parse intake config %s: %s", path, exc)

    if loaded_data is None:
        logger.warning(
            "No intake config found at %s (or alternate extension); intake disabled",
            raw_config_path,
        )
        return fallback

    intake = loaded_data.get("questionnaire") or loaded_data.get("constitution_scoring_intake", loaded_data)
    if not isinstance(intake, dict):
        logger.warning("Invalid intake config structure in %s; intake disabled", loaded_path)
        return fallback

    fields = intake.get("fields")
    if not isinstance(fields, list):
        logger.warning("Intake config in %s missing fields list; intake disabled", loaded_path)
        return fallback

    normalized_fields: list[dict[str, Any]] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name", "")).strip()
        field_type = str(field.get("type", "")).strip().lower()
        if not name or field_type not in {"single", "multi", "text"}:
            continue
        normalized_field = dict(field)
        resolved_options = _resolve_dynamic_field_options(normalized_field)
        if resolved_options:
            normalized_field["options"] = resolved_options
        normalized_field.pop("options_from", None)
        normalized_field.pop("option_labels", None)
        if field_type in {"single", "multi"}:
            options = normalized_field.get("options")
            if not isinstance(options, list) or not options:
                logger.warning(
                    "Skipping intake field '%s' in %s because no options were resolved",
                    name,
                    loaded_path,
                )
                continue
        normalized_fields.append(normalized_field)

    if not normalized_fields:
        logger.warning("Intake config in %s has no valid fields; intake disabled", loaded_path)
        return fallback

    merged = dict(fallback)
    merged.update(intake)
    merged["fields"] = normalized_fields
    merged["enabled"] = bool(intake.get("enabled", True))
    merged["auto_collapse_on_submit"] = bool(intake.get("auto_collapse_on_submit", True))
    logger.info("Loaded intake config from %s with %d fields", loaded_path, len(normalized_fields))
    return merged


def _load_intake_config() -> dict[str, Any]:
    return _load_intake_config_from_path(WEBUI_INTAKE_CONFIG_PATH)


def _build_intake_payload_from_state(
    intake_state: dict[str, Any],
    intake_fields: list[dict[str, Any]],
) -> dict[str, Any]:
    return build_visible_intake_payload(intake_state, intake_fields)


INTAKE_CONFIG = _load_intake_config()

router = APIRouter()


@lru_cache(maxsize=1)
def _load_web_ui_template() -> str:
    return WEB_UI_TEMPLATE_PATH.read_text(encoding="utf-8")


def _get_web_ui_asset(asset_name: str) -> tuple[Path, str]:
    asset = WEB_UI_ASSET_MAP.get(asset_name)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    asset_path, media_type = asset
    if not asset_path.exists():
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset_path, media_type


def _get_web_ui_asset_url(asset_name: str) -> str:
    asset_path, _ = _get_web_ui_asset(asset_name)
    version = int(asset_path.stat().st_mtime)
    return f"{WEBUI_BASE_PATH}/assets/{asset_name}?v={version}"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=WEBUI_MAX_INPUT_CHARS)
    user_id: str | None = None
    language: str | None = Field(default=None, max_length=16)


@router.get("/assets/{asset_name}")
async def ui_asset(asset_name: str) -> FileResponse:
    asset_path, media_type = _get_web_ui_asset(asset_name)
    return FileResponse(
        asset_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("", response_class=HTMLResponse)
async def ui_page() -> HTMLResponse:
    html_page = _build_html_page(
        title=WEBUI_TITLE,
        welcome_message=WEBUI_WELCOME_MESSAGE,
        api_base_url=WEBUI_API_BASE_URL,
        intake_config=INTAKE_CONFIG,
    )
    return HTMLResponse(content=html_page, status_code=200)


@router.post("/api/chat")
async def ui_chat(payload: ChatRequest) -> dict[str, object]:
    message = payload.message.strip()
    if not message:
        return {
            "ok": False,
            "reply": "请输入消息。",
            "timed_out": False,
            "elapsed_ms": 0,
        }

    user_id = (payload.user_id or f"web-{uuid.uuid4().hex[:12]}").strip()
    started = time.perf_counter()
    timed_out = False

    try:
        outcome = await asyncio.wait_for(
            generate_reply_result(user_id=user_id, text=message, preferred_language=payload.language, channel="web"),
            timeout=DEFAULT_REPLY_TIMEOUT_SECONDS,
        )
        reply = outcome.reply
    except asyncio.TimeoutError:
        timed_out = True
        reply = WEB_UI_TIMEOUT_TEXT
        outcome = None
        logger.warning("Web UI reply timeout user_hash=%s", hash_identifier(user_id))
    except Exception as exc:
        reply = WEB_UI_ERROR_TEXT
        outcome = None
        logger.warning("Web UI reply generation failed user_hash=%s error=%s", hash_identifier(user_id), exc)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "ok": bool(outcome.ok) if outcome else False,
        "blocked": bool(outcome.blocked) if outcome else False,
        "user_id": user_id,
        "reply": reply,
        "error_code": outcome.error_code if outcome else None,
        "retry_after_seconds": outcome.retry_after_seconds if outcome else None,
        "unblock_at": outcome.unblock_at if outcome else None,
        "timed_out": timed_out,
        "elapsed_ms": elapsed_ms,
    }


def _build_html_page(
    *,
    title: str | dict[str, str],
    welcome_message: str | dict[str, str],
    api_base_url: str,
    intake_config: dict[str, Any] | None = None,
) -> str:
    localized_title = _coerce_localized_text(title)
    localized_welcome = _coerce_localized_text(welcome_message)
    safe_title = html.escape(localized_title.get("zh") or localized_title.get("en") or "")
    meta_description = html.escape(
        (localized_welcome.get("en") or localized_welcome.get("zh") or safe_title)[:220]
    )
    config_json = json.dumps(
        {
            "title": localized_title,
            "welcomeMessage": localized_welcome,
            "apiBaseUrl": api_base_url,
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    intake_json = json.dumps(intake_config or _default_intake_config(), ensure_ascii=False).replace("</", "<\\/")
    html_page = _load_web_ui_template()
    replacements = {
        "__WEBUI_PAGE_TITLE__": safe_title,
        "__WEBUI_META_DESCRIPTION__": meta_description,
        "__WEBUI_CSS_URL__": html.escape(_get_web_ui_asset_url("web_ui.css")),
        "__WEBUI_JS_URL__": html.escape(_get_web_ui_asset_url("web_ui.js")),
        "__WEBUI_CONFIG_JSON__": config_json,
        "__WEBUI_INTAKE_CONFIG_JSON__": intake_json,
    }
    for placeholder, value in replacements.items():
        html_page = html_page.replace(placeholder, value)
    return html_page


__all__ = ["router"]
