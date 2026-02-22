import asyncio
import html
import json
import logging
import os
import time
import uuid

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.llm_core import generate_reply

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
WEBUI_WELCOME_MESSAGE = os.getenv(
    "WEBUI_WELCOME_MESSAGE",
    "欢迎使用健康咨询助手。请告诉我你的情况，我会提供实用建议。",
).strip()
WEBUI_TITLE = os.getenv("WEBUI_TITLE", "健康咨询助手").strip() or "健康咨询助手"


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

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    user_id: str | None = None


@router.get("", response_class=HTMLResponse)
async def ui_page() -> HTMLResponse:
    html_page = _build_html_page(
        title=WEBUI_TITLE,
        welcome_message=WEBUI_WELCOME_MESSAGE,
        api_base_url=WEBUI_API_BASE_URL,
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
            generate_reply(user_id=user_id, text=message),
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


def _build_html_page(*, title: str, welcome_message: str, api_base_url: str) -> str:
    safe_title = html.escape(title)
    title_json = json.dumps(title, ensure_ascii=False)
    welcome_json = json.dumps(welcome_message, ensure_ascii=False)
    api_json = json.dumps(api_base_url)

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

    @media (max-width: 560px) {{
      .topbar {{ padding: 10px 10px; }}
      #messages {{ padding: 12px 10px; }}
      .composer {{ padding: 10px; }}
      .bubble {{ max-width: 92vw; }}
      .status {{ max-width: 38vw; }}
      .brand {{ font-size: 13px; }}
      .lang-btn {{ font-size: 11px; padding: 0 8px; }}
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
      }},
    }};

    const USER_KEY = "openclaw_ui_user_id";
    const MESSAGES_KEY = "openclaw_ui_messages";
    const TIMESTAMP_KEY = "openclaw_ui_show_timestamp";
    const THEME_KEY = "openclaw_ui_theme";
    const LANGUAGE_KEY = "openclaw_ui_language";

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

    let statusState = {{ key: "status_ready", elapsed: 0 }};

    uiTitle.textContent = CONFIG.title;
    apiBaseLabel.textContent = CONFIG.apiBaseUrl;

    let userId = localStorage.getItem(USER_KEY);
    if (!userId) {{
      userId = "web-" + Math.random().toString(16).slice(2, 12);
      localStorage.setItem(USER_KEY, userId);
    }}
    userIdLabel.textContent = userId;

    let sessionMessages = [];

    function t(key) {{
      const dict = I18N[currentLanguage] || I18N.zh;
      return dict[key] || key;
    }}

    function resolveStatusText(state) {{
      const template = t(state.key);
      return template.replace("{{elapsed}}", String(state.elapsed || 0));
    }}

    function setStatusByKey(key, elapsed = 0) {{
      statusState = {{ key, elapsed }};
      statusPill.textContent = resolveStatusText(statusState);
    }}

    function applyLanguage() {{
      document.documentElement.lang = currentLanguage === "zh" ? "zh-CN" : "en";
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
        {{ role: "assistant", text: CONFIG.welcomeMessage, timestamp: nowIso(), error: false }},
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

    menuBtn.addEventListener("click", () => setDrawer(true));
    overlay.addEventListener("click", () => setDrawer(false));

    langToggleBtn.addEventListener("click", () => {{
      currentLanguage = currentLanguage === "zh" ? "en" : "zh";
      localStorage.setItem(LANGUAGE_KEY, currentLanguage);
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

      addMessage("user", text);
      input.value = "";
      setStatusByKey("status_thinking");
      sendBtn.disabled = true;

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
          body: JSON.stringify({{ message: text, user_id: userId }}),
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
        sendBtn.disabled = false;
      }}
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
