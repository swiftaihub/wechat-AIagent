import asyncio
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
WEB_UI_TIMEOUT_TEXT = os.getenv("WECHAT_SYNC_TIMEOUT_TEXT", "系统服务器正忙，请稍后再试")
WEB_UI_ERROR_TEXT = os.getenv(
    "WECHAT_SYNC_ERROR_TEXT",
    "系统服务器错误，请稍后再试",
)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    user_id: str | None = None


@router.get("", response_class=HTMLResponse)
async def ui_page() -> HTMLResponse:
    return HTMLResponse(content=_HTML_PAGE, status_code=200)


@router.post("/api/chat")
async def ui_chat(payload: ChatRequest) -> dict[str, object]:
    message = payload.message.strip()
    if not message:
        return {
            "ok": False,
            "reply": "Please enter a message.",
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


_HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>OpenClaw Local UI</title>
  <style>
    :root {
      --bg: #0f1720;
      --panel: #111927;
      --panel-2: #162235;
      --text: #e6edf6;
      --muted: #9db0c7;
      --accent: #2f82ff;
      --accent-2: #1d65d3;
      --danger: #d34a4a;
      --border: #253246;
      --user: #243a59;
      --assistant: #172435;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "SF Pro Text", "Noto Sans", sans-serif;
      background: radial-gradient(circle at top right, #1a2b47 0%, var(--bg) 44%);
      color: var(--text);
      min-height: 100vh;
    }

    .layout {
      display: grid;
      grid-template-columns: 240px 1fr;
      min-height: 100vh;
    }

    .sidebar {
      border-right: 1px solid var(--border);
      background: linear-gradient(180deg, #131d2d 0%, #0f1720 100%);
      padding: 16px 14px;
    }

    .logo {
      font-size: 15px;
      font-weight: 700;
      letter-spacing: 0.2px;
      margin-bottom: 14px;
    }

    .btn {
      width: 100%;
      border: 1px solid var(--border);
      color: var(--text);
      background: var(--panel-2);
      border-radius: 10px;
      padding: 10px 12px;
      cursor: pointer;
      font-size: 13px;
    }
    .btn:hover { border-color: #386399; }

    .meta {
      margin-top: 14px;
      font-size: 12px;
      color: var(--muted);
      line-height: 1.5;
      word-break: break-all;
    }

    .main {
      display: flex;
      flex-direction: column;
      min-height: 100vh;
    }

    .topbar {
      border-bottom: 1px solid var(--border);
      padding: 14px 20px;
      background: rgba(12, 20, 31, 0.74);
      backdrop-filter: blur(7px);
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
    }

    .title {
      font-size: 14px;
      color: var(--muted);
    }

    .status-pill {
      padding: 6px 10px;
      border: 1px solid var(--border);
      border-radius: 999px;
      font-size: 12px;
      color: var(--muted);
      background: var(--panel);
    }

    #messages {
      flex: 1;
      overflow: auto;
      padding: 22px max(16px, calc(50vw - 380px)) 18px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .bubble {
      max-width: min(760px, 92%);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px 14px;
      line-height: 1.5;
      font-size: 14px;
      white-space: pre-wrap;
    }
    .bubble.user {
      align-self: flex-end;
      background: var(--user);
      border-color: #355986;
    }
    .bubble.assistant {
      align-self: flex-start;
      background: var(--assistant);
    }
    .bubble.error {
      border-color: var(--danger);
      color: #ffd3d3;
    }

    .composer {
      border-top: 1px solid var(--border);
      padding: 14px max(16px, calc(50vw - 380px)) 18px;
      background: rgba(15, 23, 32, 0.94);
    }

    .composer-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: end;
    }

    #messageInput {
      width: 100%;
      min-height: 52px;
      max-height: 180px;
      resize: vertical;
      border-radius: 12px;
      border: 1px solid var(--border);
      padding: 12px 14px;
      color: var(--text);
      background: var(--panel);
      outline: none;
      font-size: 14px;
    }
    #messageInput:focus { border-color: #3b72b2; }

    .send {
      border: 1px solid #3b6db0;
      background: linear-gradient(180deg, var(--accent), var(--accent-2));
      color: #fff;
      border-radius: 10px;
      padding: 11px 14px;
      cursor: pointer;
      min-width: 84px;
      font-size: 13px;
      font-weight: 600;
    }
    .send:disabled {
      opacity: 0.55;
      cursor: default;
    }

    .hint {
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }

    @media (max-width: 880px) {
      .layout { grid-template-columns: 1fr; }
      .sidebar { display: none; }
      #messages,
      .composer {
        padding-left: 14px;
        padding-right: 14px;
      }
      .bubble { max-width: 95%; }
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="logo">OpenClaw Local UI</div>
      <button class="btn" id="newChatBtn">New Chat</button>
      <div class="meta">
        <div>Target: <code>/ui/api/chat</code></div>
        <div>User ID: <code id="userIdLabel"></code></div>
      </div>
    </aside>
    <main class="main">
      <header class="topbar">
        <div class="title">Local test UI for WeChat + Ollama pipeline</div>
        <div class="status-pill" id="statusPill">Ready</div>
      </header>
      <section id="messages"></section>
      <section class="composer">
        <form id="chatForm">
          <div class="composer-row">
            <textarea id="messageInput" placeholder="Type your test message..." required></textarea>
            <button id="sendBtn" class="send" type="submit">Send</button>
          </div>
        </form>
        <div class="hint">Enter to send, Shift+Enter for newline.</div>
      </section>
    </main>
  </div>

  <script>
    const form = document.getElementById("chatForm");
    const input = document.getElementById("messageInput");
    const sendBtn = document.getElementById("sendBtn");
    const messages = document.getElementById("messages");
    const statusPill = document.getElementById("statusPill");
    const userIdLabel = document.getElementById("userIdLabel");
    const newChatBtn = document.getElementById("newChatBtn");

    const USER_KEY = "openclaw_ui_user_id";
    let userId = localStorage.getItem(USER_KEY);
    if (!userId) {
      userId = "web-" + Math.random().toString(16).slice(2, 12);
      localStorage.setItem(USER_KEY, userId);
    }
    userIdLabel.textContent = userId;

    function setStatus(text) {
      statusPill.textContent = text;
    }

    function addBubble(role, text, extraClass = "") {
      const div = document.createElement("div");
      div.className = `bubble ${role} ${extraClass}`.trim();
      div.textContent = text;
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
      return div;
    }

    function clearChat() {
      messages.innerHTML = "";
      addBubble("assistant", "Local UI ready. Send a message to test your pipeline.");
      setStatus("Ready");
    }

    clearChat();

    newChatBtn.addEventListener("click", () => {
      clearChat();
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text) return;

      addBubble("user", text);
      input.value = "";
      setStatus("Thinking...");
      sendBtn.disabled = true;

      const pending = addBubble("assistant", "...");
      try {
        const resp = await fetch("/ui/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text, user_id: userId }),
        });
        const data = await resp.json();
        pending.textContent = data.reply || "No reply";
        if (data.timed_out) {
          pending.classList.add("error");
        }
        setStatus(`Done (${data.elapsed_ms || 0} ms)`);
      } catch (error) {
        pending.textContent = "Request failed. Check backend logs.";
        pending.classList.add("error");
        setStatus("Request failed");
      } finally {
        sendBtn.disabled = false;
      }
    });
  </script>
</body>
</html>
"""
