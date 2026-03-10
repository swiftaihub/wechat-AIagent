import asyncio
import html
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.llm_core import generate_reply
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
    "config/questionaire.private.yaml",
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
    default_zh="欢迎使用健康咨询助手。请告诉我您的情况，我将提供实用的健康指导。",
    default_en="Welcome. Tell me your situation and I will provide practical wellness guidance.",
)
WEBUI_TITLE = _load_localized_runtime_text(
    "WEBUI_TITLE",
    default_zh="健康咨询助手",
    default_en="Health Guidance Assistant",
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


def _default_intake_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "auto_collapse_on_submit": True,
        "title": {"zh": "基础信息快速采集", "en": "Quick intake"},
        "description": {
            "zh": "可快速点选并提交，系统会将信息结构化发送给 AI。",
            "en": "Quickly select baseline information and submit to AI.",
        },
        "submit_button": {"zh": "提交", "en": "Submit"},
        "reset_button": {"zh": "重置", "en": "Reset"},
        "submit_notice": {
            "zh": "基础信息已提交，正在生成个性化健康评估，请稍候。",
            "en": "Information submitted. Generating your personalized wellness assessment...",
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
    if source != "herbal_advice_symptoms":
        logger.warning("Unsupported intake options_from source '%s' for field '%s'", source, field.get("name", ""))
        return []

    try:
        dynamic_options = extract_recent_discomfort_options()
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

    intake = loaded_data.get("constitution_scoring_intake", loaded_data)
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
    payload: dict[str, Any] = {
        "age": "",
        "gender": "",
        "sleep": [],
        "diet": [],
        "bowel": [],
        "emotion": [],
        "exercise": "",
        "recent_discomfort": "",
        "recent_discomfort_choice": "",
        "recent_discomfort_text": "",
    }
    field_names = {
        str(field.get("name", "")).strip()
        for field in intake_fields
        if isinstance(field, dict) and str(field.get("name", "")).strip()
    }

    for key in tuple(payload):
        value = intake_state.get(key)
        if isinstance(value, list):
            payload[key] = value
        elif isinstance(value, str):
            payload[key] = value.strip()

    recent_parts: list[str] = []
    for key in ("recent_discomfort", "recent_discomfort_choice", "recent_discomfort_text"):
        value = str(payload.get(key, "")).strip()
        if value and value not in recent_parts:
            recent_parts.append(value)
    payload["recent_discomfort"] = "\n".join(recent_parts)

    if "recent_discomfort_choice" not in field_names and not payload["recent_discomfort_choice"]:
        payload.pop("recent_discomfort_choice")
    if "recent_discomfort_text" not in field_names and not payload["recent_discomfort_text"]:
        payload.pop("recent_discomfort_text")

    return payload


INTAKE_CONFIG = _load_intake_config()

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    user_id: str | None = None
    language: str | None = Field(default=None, max_length=16)


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
        reply = await asyncio.wait_for(
            generate_reply(user_id=user_id, text=message, preferred_language=payload.language),
            timeout=DEFAULT_REPLY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        timed_out = True
        reply = WEB_UI_TIMEOUT_TEXT
        logger.warning("Web UI reply timeout user=%s", user_id)
    except Exception as exc:
        reply = WEB_UI_ERROR_TEXT
        logger.warning("Web UI reply generation failed user=%s error=%s", user_id, exc)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "ok": True,
        "user_id": user_id,
        "reply": reply,
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
    title_json = json.dumps(localized_title, ensure_ascii=False)
    welcome_json = json.dumps(localized_welcome, ensure_ascii=False)
    api_json = json.dumps(api_base_url)
    intake_json = json.dumps(intake_config or _default_intake_config(), ensure_ascii=False)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>{safe_title}</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --panel: #ffffff;
      --text: #0f172a;
      --muted: #52607a;
      --border: #d7e0ee;
      --accent: #0b6bcb;
      --assistant-bg: #eef4ff;
      --user-bg: #0b6bcb;
      --user-text: #ffffff;
      --error: #b42318;
      --shadow: 0 12px 36px rgba(11, 46, 94, 0.08);
    }}

    body.theme-dark {{
      --bg: #0b1220;
      --panel: #111b2e;
      --text: #e6edf8;
      --muted: #97a9c5;
      --border: #253550;
      --accent: #3d8cff;
      --assistant-bg: #17243a;
      --user-bg: #2d6fd1;
      --user-text: #ffffff;
      --error: #ff8f8f;
      --shadow: 0 16px 40px rgba(0, 0, 0, 0.35);
    }}

    * {{ box-sizing: border-box; }}

    html, body {{
      margin: 0;
      height: 100%;
      font-family: "Segoe UI", "SF Pro Text", "Noto Sans", sans-serif;
      background: radial-gradient(circle at top right, #dfeafb 0%, var(--bg) 46%);
      color: var(--text);
    }}

    body.theme-dark {{
      background: radial-gradient(circle at top right, #1e2b42 0%, var(--bg) 48%);
    }}

    .app {{
      height: 100%;
      display: grid;
      grid-template-rows: auto 1fr auto;
      max-width: 980px;
      margin: 0 auto;
      background: transparent;
    }}

    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 14px;
      position: sticky;
      top: 0;
      z-index: 9;
      backdrop-filter: blur(6px);
      background: color-mix(in srgb, var(--bg) 85%, transparent);
      border-bottom: 1px solid var(--border);
    }}

    .top-left {{
      display: flex;
      align-items: center;
      gap: 8px;
      flex-shrink: 0;
    }}

    .menu-btn {{
      width: 40px;
      height: 40px;
      border: 1px solid var(--border);
      background: var(--panel);
      border-radius: 10px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      box-shadow: var(--shadow);
    }}

    .lang-btn {{
      height: 40px;
      border: 1px solid var(--border);
      background: var(--panel);
      border-radius: 10px;
      padding: 0 10px;
      cursor: pointer;
      color: var(--text);
      font-size: 12px;
      font-weight: 600;
      box-shadow: var(--shadow);
      white-space: nowrap;
    }}

    .menu-icon,
    .menu-icon::before,
    .menu-icon::after {{
      width: 18px;
      height: 2px;
      background: var(--text);
      border-radius: 99px;
      display: block;
      content: "";
      position: relative;
      transition: transform 0.2s ease;
    }}
    .menu-icon::before {{ position: absolute; top: -6px; }}
    .menu-icon::after {{ position: absolute; top: 6px; }}

    .brand {{
      font-size: 15px;
      font-weight: 700;
      letter-spacing: 0.2px;
      flex: 1;
      text-align: center;
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}

    .status {{
      font-size: 12px;
      color: var(--muted);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 6px 10px;
      background: var(--panel);
      max-width: 42vw;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      flex-shrink: 0;
    }}

    #messages {{
      overflow-y: auto;
      padding: 16px 12px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      scroll-behavior: smooth;
    }}

    .row {{
      display: flex;
      flex-direction: column;
      gap: 5px;
    }}
    .row.user {{ align-items: flex-end; }}
    .row.assistant {{ align-items: flex-start; }}

    .bubble {{
      width: fit-content;
      max-width: min(88vw, 760px);
      border-radius: 14px;
      line-height: 1.52;
      padding: 10px 12px;
      white-space: pre-wrap;
      word-break: break-word;
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
      font-size: 14px;
    }}

    .row.assistant .bubble {{
      background: var(--assistant-bg);
      color: var(--text);
    }}

    .row.user .bubble {{
      background: var(--user-bg);
      color: var(--user-text);
      border-color: transparent;
    }}

    .bubble.error {{ border-color: var(--error); }}

    .ts {{
      font-size: 11px;
      color: var(--muted);
      padding: 0 2px;
      display: none;
    }}

    .show-timestamp .ts {{ display: block; }}

    .composer {{
      border-top: 1px solid var(--border);
      padding: 12px;
      background: color-mix(in srgb, var(--bg) 84%, transparent);
    }}

    .composer-inner {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
      align-items: end;
      max-width: 980px;
      margin: 0 auto;
    }}

    #messageInput {{
      width: 100%;
      min-height: 50px;
      max-height: 200px;
      resize: vertical;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: var(--panel);
      color: var(--text);
      padding: 12px;
      font-size: 14px;
      outline: none;
    }}

    #messageInput:focus {{ border-color: var(--accent); }}

    #sendBtn {{
      border: none;
      border-radius: 10px;
      min-width: 84px;
      height: 44px;
      padding: 0 14px;
      color: #fff;
      background: linear-gradient(180deg, var(--accent), color-mix(in srgb, var(--accent) 80%, #001f4d));
      cursor: pointer;
      font-weight: 600;
      font-size: 13px;
    }}

    #sendBtn:disabled {{ opacity: 0.6; cursor: default; }}

    .hint {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
      text-align: left;
      max-width: 980px;
      margin-left: auto;
      margin-right: auto;
    }}

    .overlay {{
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.3);
      display: none;
      z-index: 10;
    }}

    .drawer {{
      position: fixed;
      inset: 0 auto 0 0;
      width: min(86vw, 320px);
      background: var(--panel);
      border-right: 1px solid var(--border);
      transform: translateX(-101%);
      transition: transform 0.2s ease;
      z-index: 11;
      padding: 14px;
      overflow-y: auto;
      box-shadow: var(--shadow);
    }}

    .drawer.open {{ transform: translateX(0); }}
    .overlay.open {{ display: block; }}

    .drawer h2 {{ margin: 0; font-size: 16px; }}
    .drawer-sub {{ color: var(--muted); font-size: 12px; margin: 4px 0 12px; }}

    .menu-section {{ margin-bottom: 16px; }}
    .menu-section-title {{
      color: var(--muted);
      font-size: 11px;
      letter-spacing: 0.4px;
      text-transform: uppercase;
      margin-bottom: 8px;
    }}

    .menu-btn-item {{
      width: 100%;
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--text);
      border-radius: 10px;
      min-height: 40px;
      text-align: left;
      padding: 10px 12px;
      cursor: pointer;
      margin-bottom: 8px;
      font-size: 13px;
    }}

    .menu-btn-item:last-child {{ margin-bottom: 0; }}

    .toggle {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--bg);
      padding: 10px 12px;
      font-size: 13px;
      margin-bottom: 8px;
    }}

    .toggle input {{ width: 18px; height: 18px; }}

    .intake-card-row {{
      width: 100%;
    }}

    .intake-card {{
      width: min(95vw, 700px);
      background: color-mix(in srgb, var(--assistant-bg) 88%, white 12%);
      border-color: color-mix(in srgb, var(--border) 72%, var(--accent) 28%);
      padding: 10px 11px;
    }}

    .intake-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 6px;
    }}

    .intake-title {{
      font-size: 13px;
      font-weight: 700;
    }}

    .intake-toggle {{
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
      color: var(--text);
      font-size: 11px;
      padding: 3px 7px;
      cursor: pointer;
    }}

    .intake-desc {{
      font-size: 11px;
      color: var(--muted);
      margin-bottom: 8px;
    }}

    .intake-body {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }}

    .intake-field {{
      border: 1px solid var(--border);
      background: color-mix(in srgb, var(--panel) 90%, var(--assistant-bg) 10%);
      border-radius: 9px;
      padding: 8px;
    }}

    .intake-field-full {{
      grid-column: 1 / -1;
    }}

    .intake-field-label {{
      font-size: 11px;
      font-weight: 600;
      margin-bottom: 6px;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 4px;
    }}

    .required-mark {{
      color: var(--error);
      font-weight: 700;
    }}

    .intake-options {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}

    .intake-chip {{
      border: 1px solid var(--border);
      background: var(--panel);
      color: var(--text);
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 11px;
      line-height: 1.25;
      cursor: pointer;
    }}

    .intake-chip.selected {{
      background: color-mix(in srgb, var(--accent) 20%, var(--panel) 80%);
      border-color: color-mix(in srgb, var(--accent) 70%, var(--border) 30%);
    }}

    .intake-textarea {{
      width: 100%;
      min-height: 54px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
      color: var(--text);
      padding: 7px 9px;
      font-size: 12px;
      resize: vertical;
      outline: none;
    }}

    .intake-textarea:focus {{
      border-color: var(--accent);
    }}

    .intake-actions {{
      display: flex;
      align-items: center;
      gap: 6px;
      margin-top: 0;
      flex-wrap: wrap;
      grid-column: 1 / -1;
    }}

    .intake-submit,
    .intake-reset {{
      border: 1px solid var(--border);
      border-radius: 8px;
      min-height: 32px;
      padding: 0 10px;
      font-size: 11px;
      cursor: pointer;
      color: var(--text);
      background: var(--panel);
    }}

    .intake-submit {{
      border-color: color-mix(in srgb, var(--accent) 70%, var(--border) 30%);
      color: #fff;
      background: linear-gradient(180deg, var(--accent), color-mix(in srgb, var(--accent) 78%, #001f4d));
      font-weight: 600;
    }}

    .intake-submit:disabled,
    .intake-reset:disabled {{
      opacity: 0.55;
      cursor: default;
    }}

    .intake-error {{
      font-size: 11px;
      color: var(--error);
      min-height: 16px;
      grid-column: 1 / -1;
    }}

    @media (max-width: 560px) {{
      .topbar {{ padding: 10px 10px; }}
      #messages {{ padding: 12px 10px; }}
      .composer {{ padding: 10px; }}
      .bubble {{ max-width: 92vw; }}
      .status {{ max-width: 38vw; }}
      .brand {{ font-size: 13px; }}
      .lang-btn {{ font-size: 11px; padding: 0 8px; }}
      .intake-body {{ grid-template-columns: 1fr; gap: 7px; }}
      .intake-field {{ padding: 7px; }}
      .intake-chip {{ font-size: 10px; padding: 4px 7px; }}
    }}
  </style>
</head>
<body>
  <div class="overlay" id="overlay"></div>

  <aside class="drawer" id="drawer">
    <h2 id="drawerTitle">菜单</h2>
    <div class="drawer-sub" id="drawerDesc">会话工具和显示设置</div>

    <div class="menu-section">
      <div class="menu-section-title" id="sectionConversation">会话</div>
      <button class="menu-btn-item" id="newChatBtn">新建对话</button>
      <button class="menu-btn-item" id="clearChatBtn">清空消息</button>
      <button class="menu-btn-item" id="exportTxtBtn">导出 TXT</button>
      <button class="menu-btn-item" id="exportJsonBtn">导出 JSON</button>
    </div>

    <div class="menu-section">
      <div class="menu-section-title" id="sectionDisplay">显示</div>
      <label class="toggle">
        <span id="toggleTimestampLabel">显示时间戳</span>
        <input type="checkbox" id="toggleTimestamp" />
      </label>
      <button class="menu-btn-item" id="toggleThemeBtn">切换浅色/深色主题</button>
    </div>

    <div class="menu-section">
      <div class="menu-section-title" id="sectionRuntime">运行信息</div>
      <div class="drawer-sub"><span id="runtimeApiLabel">API 地址</span>: <code id="apiBaseLabel"></code></div>
      <div class="drawer-sub"><span id="runtimeUserLabel">用户 ID</span>: <code id="userIdLabel"></code></div>
    </div>
  </aside>

  <div class="app" id="appRoot">
    <header class="topbar">
      <div class="top-left">
        <button class="menu-btn" id="menuBtn" aria-label="Open menu">
          <span class="menu-icon"></span>
        </button>
        <button class="lang-btn" id="langToggleBtn" type="button">中文/English</button>
      </div>
      <div class="brand" id="uiTitle"></div>
      <div class="status" id="statusPill">就绪</div>
    </header>

    <section id="messages" aria-live="polite"></section>

    <section class="composer">
      <form id="chatForm">
        <div class="composer-inner">
          <textarea id="messageInput" placeholder="请输入你的问题..." required></textarea>
          <button id="sendBtn" type="submit">发送</button>
        </div>
      </form>
      <div class="hint" id="hintText">按 Enter 发送，Shift+Enter 换行。</div>
    </section>
  </div>

  <script>
    const CONFIG = {{
      title: {title_json},
      welcomeMessage: {welcome_json},
      apiBaseUrl: {api_json},
    }};

    const INTAKE_CONFIG = {intake_json};

    const I18N = {{
      zh: {{
        drawer_title: "菜单",
        drawer_desc: "会话工具和显示设置",
        section_conversation: "会话",
        section_display: "显示",
        section_runtime: "运行信息",
        new_chat: "新建对话",
        clear_chat: "清空消息",
        export_txt: "导出 TXT",
        export_json: "导出 JSON",
        show_timestamp: "显示时间戳",
        toggle_theme: "切换浅色/深色主题",
        runtime_api: "API 地址",
        runtime_user: "用户 ID",
        send: "发送",
        input_placeholder: "请输入你的问题...",
        hint: "按 Enter 发送，Shift+Enter 换行。",
        status_ready: "就绪",
        status_thinking: "助手思考中...",
        status_cleared: "消息已清空",
        status_txt_exported: "TXT 已导出",
        status_json_exported: "JSON 已导出",
        status_timeout: "超时回退（{{elapsed}} ms）",
        status_done: "完成（{{elapsed}} ms）",
        status_failed: "请求失败",
        request_failed: "请求失败，请检查服务状态后重试。",
        intake_title: "基础信息快速采集",
        intake_description: "可快速点选并提交，系统会将信息结构化发送给 AI。",
        intake_submit: "提交",
        intake_submitting: "提交中...",
        intake_reset: "重置",
        intake_expand: "展开",
        intake_collapse: "收起",
        intake_required_missing: "请先完成必填项：{{fields}}",
        intake_payload_prefix: "用户基础信息（constitution_scoring intake）：",
        intake_submit_notice: "基础信息已提交，正在生成个性化健康评估，请稍候。",
      }},
      en: {{
        drawer_title: "Menu",
        drawer_desc: "Session tools and display settings",
        section_conversation: "Conversation",
        section_display: "Display",
        section_runtime: "Runtime",
        new_chat: "New chat",
        clear_chat: "Clear messages",
        export_txt: "Export TXT",
        export_json: "Export JSON",
        show_timestamp: "Show timestamps",
        toggle_theme: "Toggle light/dark theme",
        runtime_api: "API",
        runtime_user: "User ID",
        send: "Send",
        input_placeholder: "Type your message...",
        hint: "Press Enter to send, Shift+Enter for newline.",
        status_ready: "Ready",
        status_thinking: "Assistant is thinking...",
        status_cleared: "Messages cleared",
        status_txt_exported: "TXT exported",
        status_json_exported: "JSON exported",
        status_timeout: "Timeout fallback ({{elapsed}} ms)",
        status_done: "Done ({{elapsed}} ms)",
        status_failed: "Request failed",
        request_failed: "Request failed. Please check service status and try again.",
        intake_title: "Quick Health Intake",
        intake_description: "Select baseline information and submit to the assistant.",
        intake_submit: "Submit",
        intake_submitting: "Submitting...",
        intake_reset: "Reset",
        intake_expand: "Expand",
        intake_collapse: "Collapse",
        intake_required_missing: "Please complete required fields: {{fields}}",
        intake_payload_prefix: "User basic information (constitution_scoring intake):",
        intake_submit_notice: "Information submitted. Generating your personalized wellness assessment...",
      }},
    }};

    const USER_KEY = "openclaw_ui_user_id";
    const MESSAGES_KEY = "openclaw_ui_messages";
    const TIMESTAMP_KEY = "openclaw_ui_show_timestamp";
    const THEME_KEY = "openclaw_ui_theme";
    const LANGUAGE_KEY = "openclaw_ui_language";
    const LANGUAGE_EXPLICIT_KEY = "openclaw_ui_language_explicit";
    const PAYLOAD_FIELDS = [
      "age",
      "gender",
      "sleep",
      "diet",
      "bowel",
      "emotion",
      "exercise",
      "recent_discomfort",
      "recent_discomfort_choice",
      "recent_discomfort_text",
    ];

    const menuBtn = document.getElementById("menuBtn");
    const langToggleBtn = document.getElementById("langToggleBtn");
    const drawer = document.getElementById("drawer");
    const overlay = document.getElementById("overlay");
    const newChatBtn = document.getElementById("newChatBtn");
    const clearChatBtn = document.getElementById("clearChatBtn");
    const exportTxtBtn = document.getElementById("exportTxtBtn");
    const exportJsonBtn = document.getElementById("exportJsonBtn");
    const toggleTimestamp = document.getElementById("toggleTimestamp");
    const toggleThemeBtn = document.getElementById("toggleThemeBtn");

    const drawerTitle = document.getElementById("drawerTitle");
    const drawerDesc = document.getElementById("drawerDesc");
    const sectionConversation = document.getElementById("sectionConversation");
    const sectionDisplay = document.getElementById("sectionDisplay");
    const sectionRuntime = document.getElementById("sectionRuntime");
    const toggleTimestampLabel = document.getElementById("toggleTimestampLabel");
    const runtimeApiLabel = document.getElementById("runtimeApiLabel");
    const runtimeUserLabel = document.getElementById("runtimeUserLabel");

    const form = document.getElementById("chatForm");
    const input = document.getElementById("messageInput");
    const sendBtn = document.getElementById("sendBtn");
    const messages = document.getElementById("messages");
    const statusPill = document.getElementById("statusPill");
    const hintText = document.getElementById("hintText");
    const userIdLabel = document.getElementById("userIdLabel");
    const apiBaseLabel = document.getElementById("apiBaseLabel");
    const uiTitle = document.getElementById("uiTitle");
    const appRoot = document.getElementById("appRoot");

    let currentLanguage = localStorage.getItem(LANGUAGE_KEY) || "zh";
    if (!I18N[currentLanguage]) {{
      currentLanguage = "zh";
    }}
    let languageExplicit = localStorage.getItem(LANGUAGE_EXPLICIT_KEY) === "1";

    let statusState = {{ key: "status_ready", elapsed: 0 }};
    let isSending = false;

    apiBaseLabel.textContent = CONFIG.apiBaseUrl;

    let userId = localStorage.getItem(USER_KEY);
    if (!userId) {{
      userId = "web-" + Math.random().toString(16).slice(2, 12);
      localStorage.setItem(USER_KEY, userId);
    }}
    userIdLabel.textContent = userId;

    let sessionMessages = [];

    function getIntakeFields() {{
      if (!INTAKE_CONFIG || !Array.isArray(INTAKE_CONFIG.fields)) {{
        return [];
      }}
      return INTAKE_CONFIG.fields;
    }}

    function hasIntakeField(fieldName) {{
      return getIntakeFields().some((field) => String((field && field.name) || "") === fieldName);
    }}

    function createInitialIntakeState() {{
      const state = {{}};
      for (const field of getIntakeFields()) {{
        if (field.type === "multi") {{
          state[field.name] = [];
        }} else {{
          state[field.name] = "";
        }}
      }}
      return state;
    }}

    let intakeState = createInitialIntakeState();
    let intakeCollapsed = false;
    let intakeSubmitting = false;
    let intakeError = "";

    function t(key) {{
      const dict = I18N[currentLanguage] || I18N.zh;
      return dict[key] || key;
    }}

    function getLocaleText(value, fallback = "") {{
      if (value && typeof value === "object") {{
        if (currentLanguage === "en") {{
          return value.en || value.zh || fallback;
        }}
        return value.zh || value.en || fallback;
      }}
      if (typeof value === "string") {{
        return value;
      }}
      return fallback;
    }}

    function getLocaleVariants(value) {{
      if (value && typeof value === "object") {{
        return [value.zh, value.en]
          .map((item) => String(item || "").trim())
          .filter((item, index, array) => item && array.indexOf(item) === index);
      }}
      const text = String(value || "").trim();
      return text ? [text] : [];
    }}

    function getWelcomeMessageText() {{
      return getLocaleText(CONFIG.welcomeMessage, "");
    }}

    function formatText(template, values) {{
      let result = template;
      for (const [key, value] of Object.entries(values || {{}})) {{
        result = result.replace(`{{${{key}}}}`, String(value));
      }}
      return result;
    }}

    function escapeHtml(text) {{
      return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\\\"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }}

    function resolveStatusText(state) {{
      const template = t(state.key);
      return formatText(template, {{ elapsed: state.elapsed || 0 }});
    }}

    function setStatusByKey(key, elapsed = 0) {{
      statusState = {{ key, elapsed }};
      statusPill.textContent = resolveStatusText(statusState);
    }}

    function applyLanguage() {{
      document.documentElement.lang = currentLanguage === "zh" ? "zh-CN" : "en";
      const localizedTitle = getLocaleText(CONFIG.title, document.title);
      uiTitle.textContent = localizedTitle;
      document.title = localizedTitle;
      drawerTitle.textContent = t("drawer_title");
      drawerDesc.textContent = t("drawer_desc");
      sectionConversation.textContent = t("section_conversation");
      sectionDisplay.textContent = t("section_display");
      sectionRuntime.textContent = t("section_runtime");
      newChatBtn.textContent = t("new_chat");
      clearChatBtn.textContent = t("clear_chat");
      exportTxtBtn.textContent = t("export_txt");
      exportJsonBtn.textContent = t("export_json");
      toggleTimestampLabel.textContent = t("show_timestamp");
      toggleThemeBtn.textContent = t("toggle_theme");
      runtimeApiLabel.textContent = t("runtime_api");
      runtimeUserLabel.textContent = t("runtime_user");
      sendBtn.textContent = t("send");
      input.placeholder = t("input_placeholder");
      hintText.textContent = t("hint");
      langToggleBtn.textContent = "中文/English";
      statusPill.textContent = resolveStatusText(statusState);
      if (
        sessionMessages.length === 1 &&
        sessionMessages[0] &&
        sessionMessages[0].role === "assistant" &&
        getLocaleVariants(CONFIG.welcomeMessage).includes(String(sessionMessages[0].text || "").trim())
      ) {{
        sessionMessages[0].text = getWelcomeMessageText();
        persistMessages();
      }}
      renderMessages();
    }}

    function setDrawer(open) {{
      drawer.classList.toggle("open", open);
      overlay.classList.toggle("open", open);
    }}

    function nowIso() {{
      return new Date().toISOString();
    }}

    function formatTimestamp(iso) {{
      return new Date(iso).toLocaleString();
    }}

    function persistMessages() {{
      localStorage.setItem(MESSAGES_KEY, JSON.stringify(sessionMessages));
    }}

    function buildIntakeFieldHtml(field) {{
      const fieldName = String(field.name || "");
      const fieldLabel = getLocaleText(field.label, fieldName);
      const requiredMark = field.required ? '<span class="required-mark">*</span>' : "";
      const fullWidth = field.type === "text" || Boolean(field.full_width);
      const fieldClass = fullWidth ? "intake-field intake-field-full" : "intake-field";

      if (field.type === "text") {{
        const value = intakeState[fieldName] || "";
        const placeholder = getLocaleText(field.placeholder, "");
        const maxLength = Number(field.max_length || 280);
        return `
          <div class="${{fieldClass}}">
            <div class="intake-field-label">${{escapeHtml(fieldLabel)}} ${{requiredMark}}</div>
            <textarea
              class="intake-textarea"
              data-field="${{escapeHtml(fieldName)}}"
              placeholder="${{escapeHtml(placeholder)}}"
              maxlength="${{maxLength}}"
            >${{escapeHtml(value)}}</textarea>
          </div>
        `;
      }}

      const options = Array.isArray(field.options) ? field.options : [];
      const currentValue = intakeState[fieldName];
      const selectedSet = new Set(Array.isArray(currentValue) ? currentValue : []);
      const optionsHtml = options
        .map((option) => {{
          const optionValue = String(option.value ?? "");
          const optionLabel = getLocaleText(option.label, optionValue);
          const selected = field.type === "single"
            ? currentValue === optionValue
            : selectedSet.has(optionValue);
          return `
            <button
              type="button"
              class="intake-chip ${{selected ? "selected" : ""}}"
              data-intake-option="1"
              data-field="${{escapeHtml(fieldName)}}"
              data-type="${{escapeHtml(field.type)}}"
              data-value="${{escapeHtml(optionValue)}}"
            >${{escapeHtml(optionLabel)}}</button>
          `;
        }})
        .join("");

      return `
        <div class="${{fieldClass}}">
          <div class="intake-field-label">${{escapeHtml(fieldLabel)}} ${{requiredMark}}</div>
          <div class="intake-options">${{optionsHtml}}</div>
        </div>
      `;
    }}

    function buildIntakeCardHtml() {{
      const titleText = getLocaleText(INTAKE_CONFIG.title, t("intake_title"));
      const descriptionText = getLocaleText(INTAKE_CONFIG.description, t("intake_description"));
      const submitLabel = intakeSubmitting
        ? t("intake_submitting")
        : getLocaleText(INTAKE_CONFIG.submit_button, t("intake_submit"));
      const resetLabel = getLocaleText(INTAKE_CONFIG.reset_button, t("intake_reset"));
      const toggleLabel = intakeCollapsed ? t("intake_expand") : t("intake_collapse");
      const bodyStyle = intakeCollapsed ? "display:none;" : "";

      const fieldsHtml = getIntakeFields()
        .map((field) => buildIntakeFieldHtml(field))
        .join("");

      return `
        <div class="intake-header">
          <div class="intake-title">${{escapeHtml(titleText)}}</div>
          <button type="button" class="intake-toggle" id="intakeToggleBtn">${{escapeHtml(toggleLabel)}}</button>
        </div>
        <div class="intake-desc">${{escapeHtml(descriptionText)}}</div>
        <div class="intake-body" style="${{bodyStyle}}">
          ${{fieldsHtml}}
          <div class="intake-error" id="intakeErrorText">${{escapeHtml(intakeError)}}</div>
          <div class="intake-actions">
            <button type="button" class="intake-submit" id="intakeSubmitBtn" ${{isSending || intakeSubmitting ? "disabled" : ""}}>${{escapeHtml(submitLabel)}}</button>
            <button type="button" class="intake-reset" id="intakeResetBtn" ${{isSending || intakeSubmitting ? "disabled" : ""}}>${{escapeHtml(resetLabel)}}</button>
          </div>
        </div>
      `;
    }}

    function bindIntakeEvents(row) {{
      function updateIntakeOptionButtons(field, fieldType) {{
        const currentValue = intakeState[field];
        const selectedSet = new Set(Array.isArray(currentValue) ? currentValue : []);
        row.querySelectorAll("button[data-intake-option='1']").forEach((optionBtn) => {{
          if (String(optionBtn.dataset.field || "") !== field) {{
            return;
          }}
          const optionValue = String(optionBtn.dataset.value || "");
          const selected = fieldType === "single"
            ? String(currentValue || "") === optionValue
            : selectedSet.has(optionValue);
          optionBtn.classList.toggle("selected", selected);
        }});
      }}

      function clearIntakeErrorInView() {{
        intakeError = "";
        const errorNode = row.querySelector("#intakeErrorText");
        if (errorNode) {{
          errorNode.textContent = "";
        }}
      }}

      const toggleBtn = row.querySelector("#intakeToggleBtn");
      if (toggleBtn) {{
        toggleBtn.addEventListener("click", () => {{
          intakeCollapsed = !intakeCollapsed;
          renderMessages();
        }});
      }}

      const submitBtn = row.querySelector("#intakeSubmitBtn");
      if (submitBtn) {{
        submitBtn.addEventListener("click", async () => {{
          await handleIntakeSubmit();
        }});
      }}

      const resetBtn = row.querySelector("#intakeResetBtn");
      if (resetBtn) {{
        resetBtn.addEventListener("click", () => {{
          intakeState = createInitialIntakeState();
          intakeError = "";
          renderMessages();
        }});
      }}

      row.querySelectorAll("button[data-intake-option='1']").forEach((button) => {{
        button.addEventListener("click", () => {{
          const field = String(button.dataset.field || "");
          const value = String(button.dataset.value || "");
          const fieldType = String(button.dataset.type || "");
          if (!field || !value) {{
            return;
          }}

          if (fieldType === "single") {{
            intakeState[field] = String(intakeState[field] || "") === value ? "" : value;
          }} else if (fieldType === "multi") {{
            const selected = Array.isArray(intakeState[field]) ? [...intakeState[field]] : [];
            const index = selected.indexOf(value);
            if (index >= 0) {{
              selected.splice(index, 1);
            }} else {{
              selected.push(value);
            }}
            intakeState[field] = selected;
          }}

          updateIntakeOptionButtons(field, fieldType);
          clearIntakeErrorInView();
        }});
      }});

      row.querySelectorAll("textarea[data-field]").forEach((textarea) => {{
        textarea.addEventListener("input", () => {{
          const field = String(textarea.dataset.field || "");
          if (!field) {{
            return;
          }}
          intakeState[field] = textarea.value;
        }});
      }});
    }}

    function renderIntakeCard() {{
      const existing = document.getElementById("intakeCardRow");
      if (existing) {{
        existing.remove();
      }}

      if (!INTAKE_CONFIG || !INTAKE_CONFIG.enabled) {{
        return;
      }}

      const intakeRow = document.createElement("div");
      intakeRow.className = "row assistant intake-card-row";
      intakeRow.id = "intakeCardRow";

      const intakeBubble = document.createElement("div");
      intakeBubble.className = "bubble intake-card";
      intakeBubble.innerHTML = buildIntakeCardHtml();
      intakeRow.appendChild(intakeBubble);

      if (messages.children.length > 0) {{
        const secondNode = messages.children[1] || null;
        messages.insertBefore(intakeRow, secondNode);
      }} else {{
        messages.appendChild(intakeRow);
      }}

      bindIntakeEvents(intakeRow);
    }}

    function renderMessages() {{
      messages.innerHTML = "";
      for (const item of sessionMessages) {{
        const row = document.createElement("div");
        row.className = `row ${{item.role}}`;

        const bubble = document.createElement("div");
        bubble.className = "bubble" + (item.error ? " error" : "");
        bubble.textContent = item.text;

        const ts = document.createElement("div");
        ts.className = "ts";
        ts.textContent = formatTimestamp(item.timestamp);

        row.appendChild(bubble);
        row.appendChild(ts);
        messages.appendChild(row);
      }}
      renderIntakeCard();
      messages.scrollTop = messages.scrollHeight;
    }}

    function addMessage(role, text, options = {{}}) {{
      sessionMessages.push({{
        role,
        text,
        timestamp: nowIso(),
        error: Boolean(options.error),
      }});
      persistMessages();
      renderMessages();
    }}

    function resetConversation() {{
      sessionMessages = [
        {{ role: "assistant", text: getWelcomeMessageText(), timestamp: nowIso(), error: false }},
      ];
      persistMessages();
      renderMessages();
      setStatusByKey("status_ready");
    }}

    function loadConversation() {{
      try {{
        const raw = localStorage.getItem(MESSAGES_KEY);
        if (!raw) {{
          resetConversation();
          return;
        }}
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed) || parsed.length === 0) {{
          resetConversation();
          return;
        }}
        sessionMessages = parsed;
        renderMessages();
      }} catch (_err) {{
        resetConversation();
      }}
    }}

    function downloadBlob(filename, content, type) {{
      const blob = new Blob([content], {{ type }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }}

    function exportTxt() {{
      if (sessionMessages.length === 0) return;
      const lines = sessionMessages.map((m) => `[${{formatTimestamp(m.timestamp)}}] ${{m.role}}: ${{m.text}}`);
      downloadBlob(`chat-${{userId}}.txt`, lines.join("\\n\\n"), "text/plain;charset=utf-8");
    }}

    function exportJson() {{
      if (sessionMessages.length === 0) return;
      downloadBlob(
        `chat-${{userId}}.json`,
        JSON.stringify({{ user_id: userId, exported_at: nowIso(), messages: sessionMessages }}, null, 2),
        "application/json;charset=utf-8"
      );
    }}

    function applyTheme(theme) {{
      document.body.classList.toggle("theme-dark", theme === "dark");
      localStorage.setItem(THEME_KEY, theme);
    }}

    function setTimestampEnabled(enabled) {{
      appRoot.classList.toggle("show-timestamp", enabled);
      toggleTimestamp.checked = enabled;
      localStorage.setItem(TIMESTAMP_KEY, enabled ? "1" : "0");
    }}

    function validateIntakePayload() {{
      const requiredSingles = getIntakeFields().filter((field) => field.type === "single" && field.required);
      const missingLabels = [];

      for (const field of requiredSingles) {{
        const value = String(intakeState[field.name] || "").trim();
        if (!value) {{
          missingLabels.push(getLocaleText(field.label, field.name));
        }}
      }}

      if (missingLabels.length > 0) {{
        const separator = currentLanguage === "en" ? ", " : "、";
        intakeError = formatText(t("intake_required_missing"), {{
          fields: missingLabels.join(separator),
        }});
        return false;
      }}

      intakeError = "";
      return true;
    }}

    function buildIntakePayload() {{
      const payload = {{
        age: "",
        gender: "",
        sleep: [],
        diet: [],
        bowel: [],
        emotion: [],
        exercise: "",
        recent_discomfort: "",
        recent_discomfort_choice: "",
        recent_discomfort_text: "",
      }};

      for (const key of PAYLOAD_FIELDS) {{
        const value = intakeState[key];
        if (Array.isArray(value)) {{
          payload[key] = value;
        }} else if (typeof value === "string") {{
          payload[key] = value.trim();
        }}
      }}

      const recentDiscomfortParts = [];
      for (const key of ["recent_discomfort", "recent_discomfort_choice", "recent_discomfort_text"]) {{
        const value = String(payload[key] || "").trim();
        if (!value || recentDiscomfortParts.includes(value)) {{
          continue;
        }}
        recentDiscomfortParts.push(value);
      }}
      payload.recent_discomfort = recentDiscomfortParts.join("\\n");

      if (!hasIntakeField("recent_discomfort_choice") && !payload.recent_discomfort_choice) {{
        delete payload.recent_discomfort_choice;
      }}
      if (!hasIntakeField("recent_discomfort_text") && !payload.recent_discomfort_text) {{
        delete payload.recent_discomfort_text;
      }}

      return payload;
    }}

    async function sendMessageText(text, options = {{ source: "chat" }}) {{
      const normalized = String(text || "").trim();
      if (!normalized || isSending) {{
        return false;
      }}

      const fromIntake = options && options.source === "intake";
      const displayCandidate = options && typeof options.displayText === "string"
        ? options.displayText
        : "";
      const displayText = String(displayCandidate || normalized).trim() || normalized;
      let success = false;

      isSending = true;
      sendBtn.disabled = true;
      if (fromIntake) {{
        intakeSubmitting = true;
        renderMessages();
      }}

      addMessage("user", displayText);
      setStatusByKey("status_thinking");

      const pendingIndex = sessionMessages.length;
      sessionMessages.push({{
        role: "assistant",
        text: "...",
        timestamp: nowIso(),
        error: false,
      }});
      persistMessages();
      renderMessages();

      try {{
        const resp = await fetch(CONFIG.apiBaseUrl, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            message: normalized,
            user_id: userId,
            language: languageExplicit ? currentLanguage : null,
          }}),
        }});

        let data = null;
        try {{
          data = await resp.json();
        }} catch (_jsonErr) {{
          data = null;
        }}

        if (!resp.ok || !data) {{
          throw new Error("server_error");
        }}

        const replyText = data.reply || "No reply returned.";
        sessionMessages[pendingIndex] = {{
          role: "assistant",
          text: replyText,
          timestamp: nowIso(),
          error: Boolean(data.timed_out),
        }};
        persistMessages();
        renderMessages();

        const elapsed = Number(data.elapsed_ms || 0);
        if (data.timed_out) {{
          setStatusByKey("status_timeout", elapsed);
        }} else {{
          setStatusByKey("status_done", elapsed);
        }}
        success = true;
      }} catch (_error) {{
        sessionMessages[pendingIndex] = {{
          role: "assistant",
          text: t("request_failed"),
          timestamp: nowIso(),
          error: true,
        }};
        persistMessages();
        renderMessages();
        setStatusByKey("status_failed");
      }} finally {{
        isSending = false;
        sendBtn.disabled = false;
        if (fromIntake) {{
          intakeSubmitting = false;
          if (success && INTAKE_CONFIG.auto_collapse_on_submit) {{
            intakeCollapsed = true;
          }}
          renderMessages();
        }}
      }}

      return success;
    }}

    async function handleIntakeSubmit() {{
      if (isSending) {{
        return;
      }}

      if (!validateIntakePayload()) {{
        renderMessages();
        return;
      }}

      const payload = buildIntakePayload();
      const prefix = t("intake_payload_prefix");
      const payloadText = `${{prefix}}\\n\\`\\`\\`json\\n${{JSON.stringify(payload, null, 2)}}\\n\\`\\`\\``;
      const intakeSubmitNotice = getLocaleText(INTAKE_CONFIG.submit_notice, t("intake_submit_notice"));
      await sendMessageText(payloadText, {{
        source: "intake",
        displayText: intakeSubmitNotice,
      }});
    }}

    menuBtn.addEventListener("click", () => setDrawer(true));
    overlay.addEventListener("click", () => setDrawer(false));

    langToggleBtn.addEventListener("click", () => {{
      currentLanguage = currentLanguage === "zh" ? "en" : "zh";
      localStorage.setItem(LANGUAGE_KEY, currentLanguage);
      languageExplicit = true;
      localStorage.setItem(LANGUAGE_EXPLICIT_KEY, "1");
      applyLanguage();
    }});

    newChatBtn.addEventListener("click", () => {{
      resetConversation();
      setDrawer(false);
    }});

    clearChatBtn.addEventListener("click", () => {{
      sessionMessages = [];
      persistMessages();
      renderMessages();
      setStatusByKey("status_cleared");
      setDrawer(false);
    }});

    exportTxtBtn.addEventListener("click", () => {{
      exportTxt();
      setStatusByKey("status_txt_exported");
      setDrawer(false);
    }});

    exportJsonBtn.addEventListener("click", () => {{
      exportJson();
      setStatusByKey("status_json_exported");
      setDrawer(false);
    }});

    toggleTimestamp.addEventListener("change", (event) => {{
      setTimestampEnabled(event.target.checked);
    }});

    toggleThemeBtn.addEventListener("click", () => {{
      const isDark = document.body.classList.contains("theme-dark");
      applyTheme(isDark ? "light" : "dark");
      setDrawer(false);
    }});

    input.addEventListener("keydown", (event) => {{
      if (event.key === "Enter" && !event.shiftKey) {{
        event.preventDefault();
        form.requestSubmit();
      }}
    }});

    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      await sendMessageText(text, {{ source: "chat" }});
    }});

    const savedTheme = localStorage.getItem(THEME_KEY) || "light";
    applyTheme(savedTheme);
    const showTs = localStorage.getItem(TIMESTAMP_KEY) === "1";
    setTimestampEnabled(showTs);
    applyLanguage();
    loadConversation();
  </script>
</body>
</html>
"""


__all__ = ["router"]
