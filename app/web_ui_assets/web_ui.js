(function () {
  const boot = window.__WEBUI_BOOT__ || {};
  const CONFIG = boot.CONFIG || {
    title: { zh: "健康咨询助手", en: "Health Guidance Assistant" },
    welcomeMessage: {
      zh: "欢迎使用健康咨询助手。请告诉我您的情况，我将提供实用的健康指导。",
      en: "Welcome. Tell me your situation and I will provide practical wellness guidance.",
    },
    apiBaseUrl: "/ui/api/chat",
  };
  const INTAKE_CONFIG = boot.INTAKE_CONFIG || {
    enabled: false,
    auto_collapse_on_submit: true,
    fields: [],
  };

  const I18N = {
    zh: {
      drawer_eyebrow: "工作台设置",
      drawer_title: "菜单",
      drawer_desc: "会话工具与显示设置",
      section_conversation: "会话",
      section_display: "显示",
      section_runtime: "运行信息",
      new_chat: "新建对话",
      clear_chat: "清空消息",
      export_txt: "导出 TXT",
      export_json: "导出 JSON",
      show_timestamp: "显示时间戳",
      toggle_theme: "切换浅色 / 深色主题",
      runtime_api: "API 地址",
      runtime_api_inline: "接口地址",
      runtime_user: "用户 ID",
      runtime_user_inline: "会话 ID",
      send: "发送",
      input_placeholder: "请输入你的需求、体感、日常节奏，或想了解的茶饮方向...",
      message_input_label: "消息输入框",
      hint: "按 Enter 发送，Shift + Enter 换行。",
      topbar_label: "品牌签名级 Helper AI",
      hero_badge: "引导式草本助手",
      hero_kicker: "与 herbal wellness 品牌体验无缝融合",
      hero_strip_eyebrow: "AI 推荐流程",
      hero_strip_text: "先进行简短信息采集，或直接开始对话。助手会结合你的描述，给出更清晰的茶饮与成分探索方向。",
      hero_reset: "重新开始",
      composer_eyebrow: "对话区",
      composer_title: "告诉我你现在的需求、日常状态或送礼意图",
      feature_1_eyebrow: "发现",
      feature_1_title: "中英双语自然切换",
      feature_1_body: "无论以中文还是 English 沟通，都会保持同样平静、清晰、可信赖的推荐体验。",
      feature_2_eyebrow: "信息采集",
      feature_2_title: "结构化的 wellness 问答",
      feature_2_body: "把近期不适、作息与日常感受整理成更适合 AI 理解的输入，让建议更聚焦。",
      feature_3_eyebrow: "体验",
      feature_3_title: "延续主站的高级品牌气质",
      feature_3_body: "相同的纸张底色、茶感配色、衬线标题与精致留白，让 AI 体验真正融入品牌。",
      ritual_eyebrow: "使用说明",
      ritual_title: "把推荐过程变成一段安静的探索仪式",
      ritual_body: "此助手用于 wellness 教育内容与产品发现，不用于诊断、治疗或替代专业医疗建议。",
      status_ready: "准备就绪",
      status_thinking: "助手正在整理建议...",
      status_cleared: "消息已清空",
      status_txt_exported: "TXT 已导出",
      status_json_exported: "JSON 已导出",
      status_timeout: "超时回退（{{elapsed}} ms）",
      status_done: "完成（{{elapsed}} ms）",
      status_failed: "请求失败",
      request_failed: "请求失败，请检查服务状态后重试。",
      empty_state_title: "从一个问题开始",
      empty_state_body: "可以描述你最近的体感、作息、想改善的饮茶体验，或你正在为谁寻找一款更合适的草本茶。",
      intake_title: "基础信息快速采集",
      intake_description: "可快速点选并提交，系统会将信息结构化发送给 AI。",
      intake_submit: "提交",
      intake_submitting: "提交中...",
      intake_reset: "重置",
      intake_expand: "展开",
      intake_collapse: "收起",
      intake_required_missing: "请先完成必填项：{{fields}}",
      intake_payload_prefix: "用户基础信息（constitution_scoring intake）：",
      intake_submit_notice: "基础信息已提交，正在生成更贴近你的草本建议，请稍候。",
      aria_open_menu: "打开菜单",
      aria_close_menu: "关闭菜单",
    },
    en: {
      drawer_eyebrow: "Studio controls",
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
      toggle_theme: "Toggle light / dark theme",
      runtime_api: "API",
      runtime_api_inline: "API endpoint",
      runtime_user: "User ID",
      runtime_user_inline: "Session ID",
      send: "Send",
      input_placeholder: "Describe your needs, current feeling, ritual goals, or the tea direction you want to explore...",
      message_input_label: "Message input",
      hint: "Press Enter to send, Shift + Enter for newline.",
      topbar_label: "Signature Helper AI",
      hero_badge: "Guided herbal companion",
      hero_kicker: "Seamlessly aligned with the herbal wellness storefront",
      hero_strip_eyebrow: "AI recommendation flow",
      hero_strip_text: "Complete a quick intake or start chatting right away. The assistant uses your context to guide tea and ingredient discovery more clearly.",
      hero_reset: "Start fresh",
      composer_eyebrow: "Conversation",
      composer_title: "Share your needs, current rhythms, or gifting intent",
      feature_1_eyebrow: "Discovery",
      feature_1_title: "Natural bilingual guidance",
      feature_1_body: "Move between Chinese and English without losing the same calm, clear, and trustworthy recommendation flow.",
      feature_2_eyebrow: "Intake",
      feature_2_title: "Structured wellness prompts",
      feature_2_body: "Capture recent discomforts, lifestyle patterns, and day-to-day context in a format the assistant can use effectively.",
      feature_3_eyebrow: "Experience",
      feature_3_title: "Aligned with the premium storefront",
      feature_3_body: "The same paper-toned palette, tea warmth, editorial serif type, and refined spacing now extend into the AI experience.",
      ritual_eyebrow: "Care note",
      ritual_title: "A calm discovery ritual",
      ritual_body: "This assistant is for educational wellness guidance and product discovery. It does not diagnose, treat, or replace care from a licensed clinician.",
      status_ready: "Ready",
      status_thinking: "Assistant is shaping your guidance...",
      status_cleared: "Messages cleared",
      status_txt_exported: "TXT exported",
      status_json_exported: "JSON exported",
      status_timeout: "Timeout fallback ({{elapsed}} ms)",
      status_done: "Done ({{elapsed}} ms)",
      status_failed: "Request failed",
      request_failed: "Request failed. Please check service status and try again.",
      empty_state_title: "Begin with one question",
      empty_state_body: "Describe how you've been feeling lately, your daily rhythm, the tea experience you want to improve, or who you are shopping for.",
      intake_title: "Quick wellness intake",
      intake_description: "Select baseline information and submit it in a structured format for the assistant.",
      intake_submit: "Submit",
      intake_submitting: "Submitting...",
      intake_reset: "Reset",
      intake_expand: "Expand",
      intake_collapse: "Collapse",
      intake_required_missing: "Please complete required fields: {{fields}}",
      intake_payload_prefix: "User basic information (constitution_scoring intake):",
      intake_submit_notice: "Information submitted. Generating a more tailored herbal guidance path now...",
      aria_open_menu: "Open menu",
      aria_close_menu: "Close menu",
    },
  };

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
  const drawerCloseBtn = document.getElementById("drawerCloseBtn");
  const langToggleBtn = document.getElementById("langToggleBtn");
  const drawer = document.getElementById("drawer");
  const overlay = document.getElementById("overlay");
  const newChatBtn = document.getElementById("newChatBtn");
  const newChatInlineBtn = document.getElementById("newChatInlineBtn");
  const heroResetBtn = document.getElementById("heroResetBtn");
  const clearChatBtn = document.getElementById("clearChatBtn");
  const exportTxtBtn = document.getElementById("exportTxtBtn");
  const exportJsonBtn = document.getElementById("exportJsonBtn");
  const toggleTimestamp = document.getElementById("toggleTimestamp");
  const toggleThemeBtn = document.getElementById("toggleThemeBtn");

  const form = document.getElementById("chatForm");
  const input = document.getElementById("messageInput");
  const sendBtn = document.getElementById("sendBtn");
  const messages = document.getElementById("messages");
  const statusPill = document.getElementById("statusPill");
  const hintText = document.getElementById("hintText");
  const uiTitle = document.getElementById("uiTitle");
  const heroLead = document.getElementById("heroLead");
  const heroStripText = document.getElementById("heroStripText");
  const appRoot = document.getElementById("appRoot");
  const apiBaseLabels = document.querySelectorAll("[data-api-base-label='1']");
  const userIdLabels = document.querySelectorAll("[data-user-id-label='1']");
  const i18nNodes = document.querySelectorAll("[data-i18n]");
  const placeholderNodes = document.querySelectorAll("[data-i18n-placeholder]");

  let currentLanguage = localStorage.getItem(LANGUAGE_KEY) || "zh";
  if (!I18N[currentLanguage]) {
    currentLanguage = "zh";
  }

  let languageExplicit = localStorage.getItem(LANGUAGE_EXPLICIT_KEY) === "1";
  let statusState = { key: "status_ready", elapsed: 0 };
  let isSending = false;
  let sessionMessages = [];

  apiBaseLabels.forEach((node) => {
    node.textContent = CONFIG.apiBaseUrl;
  });

  let userId = localStorage.getItem(USER_KEY);
  if (!userId) {
    userId = "web-" + Math.random().toString(16).slice(2, 12);
    localStorage.setItem(USER_KEY, userId);
  }
  userIdLabels.forEach((node) => {
    node.textContent = userId;
  });

  function getIntakeFields() {
    if (!INTAKE_CONFIG || !Array.isArray(INTAKE_CONFIG.fields)) {
      return [];
    }
    return INTAKE_CONFIG.fields;
  }

  function hasIntakeField(fieldName) {
    return getIntakeFields().some((field) => String((field && field.name) || "") === fieldName);
  }

  function createInitialIntakeState() {
    const state = {};
    for (const field of getIntakeFields()) {
      if (field.type === "multi") {
        state[field.name] = [];
      } else {
        state[field.name] = "";
      }
    }
    return state;
  }

  let intakeState = createInitialIntakeState();
  let intakeCollapsed = false;
  let intakeSubmitting = false;
  let intakeError = "";

  function t(key) {
    const dict = I18N[currentLanguage] || I18N.zh;
    return dict[key] || key;
  }

  function getLocaleText(value, fallback = "") {
    if (value && typeof value === "object") {
      if (currentLanguage === "en") {
        return value.en || value.zh || fallback;
      }
      return value.zh || value.en || fallback;
    }
    if (typeof value === "string") {
      return value;
    }
    return fallback;
  }

  function getLocaleVariants(value) {
    if (value && typeof value === "object") {
      return [value.zh, value.en]
        .map((item) => String(item || "").trim())
        .filter((item, index, array) => item && array.indexOf(item) === index);
    }
    const text = String(value || "").trim();
    return text ? [text] : [];
  }

  function getWelcomeMessageText() {
    return getLocaleText(CONFIG.welcomeMessage, "");
  }

  function formatText(template, values) {
    let result = template;
    for (const [key, value] of Object.entries(values || {})) {
      result = result.replace(`{{${key}}}`, String(value));
    }
    return result;
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function resolveStatusText(state) {
    const template = t(state.key);
    return formatText(template, { elapsed: state.elapsed || 0 });
  }

  function setStatusByKey(key, elapsed = 0) {
    statusState = { key, elapsed };
    statusPill.textContent = resolveStatusText(statusState);
  }

  function autoResizeTextarea(textarea) {
    if (!textarea) {
      return;
    }
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 260)}px`;
  }

  function applyLanguage() {
    document.documentElement.lang = currentLanguage === "zh" ? "zh-CN" : "en";
    const localizedTitle = getLocaleText(CONFIG.title, document.title);
    uiTitle.textContent = localizedTitle;
    heroLead.textContent = getWelcomeMessageText();
    heroStripText.textContent = t("hero_strip_text");
    document.title = localizedTitle;

    i18nNodes.forEach((node) => {
      const key = node.getAttribute("data-i18n");
      if (key) {
        node.textContent = t(key);
      }
    });

    placeholderNodes.forEach((node) => {
      const key = node.getAttribute("data-i18n-placeholder");
      if (key && "placeholder" in node) {
        node.placeholder = t(key);
      }
    });

    input.placeholder = t("input_placeholder");
    hintText.textContent = t("hint");
    sendBtn.textContent = t("send");
    langToggleBtn.textContent = "中文 / English";
    statusPill.textContent = resolveStatusText(statusState);
    menuBtn.setAttribute("aria-label", t("aria_open_menu"));
    drawerCloseBtn.setAttribute("aria-label", t("aria_close_menu"));

    if (
      sessionMessages.length === 1 &&
      sessionMessages[0] &&
      sessionMessages[0].role === "assistant" &&
      getLocaleVariants(CONFIG.welcomeMessage).includes(String(sessionMessages[0].text || "").trim())
    ) {
      sessionMessages[0].text = getWelcomeMessageText();
      persistMessages();
    }

    renderMessages();
  }

  function setDrawer(open) {
    drawer.classList.toggle("open", open);
    overlay.classList.toggle("open", open);
    drawer.setAttribute("aria-hidden", open ? "false" : "true");
  }

  function nowIso() {
    return new Date().toISOString();
  }

  function formatTimestamp(iso) {
    return new Date(iso).toLocaleString();
  }

  function persistMessages() {
    localStorage.setItem(MESSAGES_KEY, JSON.stringify(sessionMessages));
  }

  function renderEmptyState() {
    const row = document.createElement("div");
    row.className = "row assistant";

    const emptyState = document.createElement("div");
    emptyState.className = "empty-state";
    emptyState.innerHTML = `
      <h2 class="empty-state__title">${escapeHtml(t("empty_state_title"))}</h2>
      <p class="empty-state__body">${escapeHtml(t("empty_state_body"))}</p>
    `;

    row.appendChild(emptyState);
    messages.appendChild(row);
  }

  function buildIntakeFieldHtml(field) {
    const fieldName = String(field.name || "");
    const fieldLabel = getLocaleText(field.label, fieldName);
    const requiredMark = field.required ? '<span class="required-mark">*</span>' : "";
    const fullWidth = field.type === "text" || Boolean(field.full_width);
    const fieldClass = fullWidth ? "intake-field intake-field-full" : "intake-field";

    if (field.type === "text") {
      const value = intakeState[fieldName] || "";
      const placeholder = getLocaleText(field.placeholder, "");
      const maxLength = Number(field.max_length || 280);
      return `
        <div class="${fieldClass}">
          <div class="intake-field-label">${escapeHtml(fieldLabel)} ${requiredMark}</div>
          <textarea
            class="intake-textarea"
            data-field="${escapeHtml(fieldName)}"
            placeholder="${escapeHtml(placeholder)}"
            maxlength="${maxLength}"
          >${escapeHtml(value)}</textarea>
        </div>
      `;
    }

    const options = Array.isArray(field.options) ? field.options : [];
    const currentValue = intakeState[fieldName];
    const selectedSet = new Set(Array.isArray(currentValue) ? currentValue : []);
    const optionsHtml = options
      .map((option) => {
        const optionValue = String(option.value ?? "");
        const optionLabel = getLocaleText(option.label, optionValue);
        const selected = field.type === "single" ? currentValue === optionValue : selectedSet.has(optionValue);
        return `
          <button
            type="button"
            class="intake-chip ${selected ? "selected" : ""}"
            data-intake-option="1"
            data-field="${escapeHtml(fieldName)}"
            data-type="${escapeHtml(field.type)}"
            data-value="${escapeHtml(optionValue)}"
          >${escapeHtml(optionLabel)}</button>
        `;
      })
      .join("");

    return `
      <div class="${fieldClass}">
        <div class="intake-field-label">${escapeHtml(fieldLabel)} ${requiredMark}</div>
        <div class="intake-options">${optionsHtml}</div>
      </div>
    `;
  }

  function buildIntakeCardHtml() {
    const titleText = getLocaleText(INTAKE_CONFIG.title, t("intake_title"));
    const descriptionText = getLocaleText(INTAKE_CONFIG.description, t("intake_description"));
    const submitLabel = intakeSubmitting ? t("intake_submitting") : getLocaleText(INTAKE_CONFIG.submit_button, t("intake_submit"));
    const resetLabel = getLocaleText(INTAKE_CONFIG.reset_button, t("intake_reset"));
    const toggleLabel = intakeCollapsed ? t("intake_expand") : t("intake_collapse");
    const bodyStyle = intakeCollapsed ? "display:none;" : "";

    const fieldsHtml = getIntakeFields()
      .map((field) => buildIntakeFieldHtml(field))
      .join("");

    return `
      <div class="intake-header">
        <div>
          <div class="intake-title">${escapeHtml(titleText)}</div>
          <div class="intake-desc">${escapeHtml(descriptionText)}</div>
        </div>
        <button type="button" class="intake-toggle" id="intakeToggleBtn">${escapeHtml(toggleLabel)}</button>
      </div>
      <div class="intake-body" style="${bodyStyle}">
        ${fieldsHtml}
        <div class="intake-error" id="intakeErrorText">${escapeHtml(intakeError)}</div>
        <div class="intake-actions">
          <button type="button" class="intake-submit" id="intakeSubmitBtn" ${isSending || intakeSubmitting ? "disabled" : ""}>${escapeHtml(submitLabel)}</button>
          <button type="button" class="intake-reset" id="intakeResetBtn" ${isSending || intakeSubmitting ? "disabled" : ""}>${escapeHtml(resetLabel)}</button>
        </div>
      </div>
    `;
  }

  function bindIntakeEvents(row) {
    function updateIntakeOptionButtons(field, fieldType) {
      const currentValue = intakeState[field];
      const selectedSet = new Set(Array.isArray(currentValue) ? currentValue : []);
      row.querySelectorAll("button[data-intake-option='1']").forEach((optionBtn) => {
        if (String(optionBtn.dataset.field || "") !== field) {
          return;
        }
        const optionValue = String(optionBtn.dataset.value || "");
        const selected = fieldType === "single" ? String(currentValue || "") === optionValue : selectedSet.has(optionValue);
        optionBtn.classList.toggle("selected", selected);
      });
    }

    function clearIntakeErrorInView() {
      intakeError = "";
      const errorNode = row.querySelector("#intakeErrorText");
      if (errorNode) {
        errorNode.textContent = "";
      }
    }

    const toggleBtn = row.querySelector("#intakeToggleBtn");
    if (toggleBtn) {
      toggleBtn.addEventListener("click", () => {
        intakeCollapsed = !intakeCollapsed;
        renderMessages();
      });
    }

    const submitBtn = row.querySelector("#intakeSubmitBtn");
    if (submitBtn) {
      submitBtn.addEventListener("click", async () => {
        await handleIntakeSubmit();
      });
    }

    const resetBtn = row.querySelector("#intakeResetBtn");
    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        intakeState = createInitialIntakeState();
        intakeError = "";
        renderMessages();
      });
    }

    row.querySelectorAll("button[data-intake-option='1']").forEach((button) => {
      button.addEventListener("click", () => {
        const field = String(button.dataset.field || "");
        const value = String(button.dataset.value || "");
        const fieldType = String(button.dataset.type || "");
        if (!field || !value) {
          return;
        }

        if (fieldType === "single") {
          intakeState[field] = String(intakeState[field] || "") === value ? "" : value;
        } else if (fieldType === "multi") {
          const selected = Array.isArray(intakeState[field]) ? [...intakeState[field]] : [];
          const index = selected.indexOf(value);
          if (index >= 0) {
            selected.splice(index, 1);
          } else {
            selected.push(value);
          }
          intakeState[field] = selected;
        }

        updateIntakeOptionButtons(field, fieldType);
        clearIntakeErrorInView();
      });
    });

    row.querySelectorAll("textarea[data-field]").forEach((textarea) => {
      autoResizeTextarea(textarea);
      textarea.addEventListener("input", () => {
        const field = String(textarea.dataset.field || "");
        if (!field) {
          return;
        }
        intakeState[field] = textarea.value;
        autoResizeTextarea(textarea);
      });
    });
  }

  function renderIntakeCard() {
    const existing = document.getElementById("intakeCardRow");
    if (existing) {
      existing.remove();
    }

    if (!INTAKE_CONFIG || !INTAKE_CONFIG.enabled) {
      return;
    }

    const intakeRow = document.createElement("div");
    intakeRow.className = "row assistant intake-card-row";
    intakeRow.id = "intakeCardRow";

    const intakeBubble = document.createElement("div");
    intakeBubble.className = "bubble intake-card";
    intakeBubble.innerHTML = buildIntakeCardHtml();
    intakeRow.appendChild(intakeBubble);

    if (messages.children.length > 0) {
      const secondNode = messages.children[1] || null;
      messages.insertBefore(intakeRow, secondNode);
    } else {
      messages.appendChild(intakeRow);
    }

    bindIntakeEvents(intakeRow);
  }

  function renderMessages() {
    messages.innerHTML = "";

    if (sessionMessages.length === 0) {
      renderEmptyState();
    } else {
      for (const item of sessionMessages) {
        const row = document.createElement("div");
        row.className = `row ${item.role}`;

        const bubble = document.createElement("div");
        bubble.className = "bubble" + (item.error ? " error" : "");
        bubble.textContent = item.text;

        const ts = document.createElement("div");
        ts.className = "ts";
        ts.textContent = formatTimestamp(item.timestamp);

        row.appendChild(bubble);
        row.appendChild(ts);
        messages.appendChild(row);
      }
    }

    renderIntakeCard();
    messages.scrollTop = messages.scrollHeight;
  }

  function addMessage(role, text, options = {}) {
    sessionMessages.push({
      role,
      text,
      timestamp: nowIso(),
      error: Boolean(options.error),
    });
    persistMessages();
    renderMessages();
  }

  function resetConversation() {
    sessionMessages = [{ role: "assistant", text: getWelcomeMessageText(), timestamp: nowIso(), error: false }];
    persistMessages();
    renderMessages();
    setStatusByKey("status_ready");
  }

  function loadConversation() {
    try {
      const raw = localStorage.getItem(MESSAGES_KEY);
      if (!raw) {
        resetConversation();
        return;
      }
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) {
        resetConversation();
        return;
      }
      sessionMessages = parsed;
      renderMessages();
    } catch (_err) {
      resetConversation();
    }
  }

  function downloadBlob(filename, content, type) {
    const blob = new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function exportTxt() {
    if (sessionMessages.length === 0) {
      return;
    }
    const lines = sessionMessages.map((message) => `[${formatTimestamp(message.timestamp)}] ${message.role}: ${message.text}`);
    downloadBlob(`chat-${userId}.txt`, lines.join("\n\n"), "text/plain;charset=utf-8");
  }

  function exportJson() {
    if (sessionMessages.length === 0) {
      return;
    }
    downloadBlob(
      `chat-${userId}.json`,
      JSON.stringify({ user_id: userId, exported_at: nowIso(), messages: sessionMessages }, null, 2),
      "application/json;charset=utf-8"
    );
  }

  function applyTheme(theme) {
    document.body.classList.toggle("theme-dark", theme === "dark");
    localStorage.setItem(THEME_KEY, theme);
  }

  function setTimestampEnabled(enabled) {
    appRoot.classList.toggle("show-timestamp", enabled);
    toggleTimestamp.checked = enabled;
    localStorage.setItem(TIMESTAMP_KEY, enabled ? "1" : "0");
  }

  function validateIntakePayload() {
    const requiredSingles = getIntakeFields().filter((field) => field.type === "single" && field.required);
    const missingLabels = [];

    for (const field of requiredSingles) {
      const value = String(intakeState[field.name] || "").trim();
      if (!value) {
        missingLabels.push(getLocaleText(field.label, field.name));
      }
    }

    if (missingLabels.length > 0) {
      const separator = currentLanguage === "en" ? ", " : "、";
      intakeError = formatText(t("intake_required_missing"), {
        fields: missingLabels.join(separator),
      });
      return false;
    }

    intakeError = "";
    return true;
  }

  function buildIntakePayload() {
    const payload = {
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
    };

    for (const key of PAYLOAD_FIELDS) {
      const value = intakeState[key];
      if (Array.isArray(value)) {
        payload[key] = value;
      } else if (typeof value === "string") {
        payload[key] = value.trim();
      }
    }

    const recentDiscomfortParts = [];
    for (const key of ["recent_discomfort", "recent_discomfort_choice", "recent_discomfort_text"]) {
      const value = String(payload[key] || "").trim();
      if (!value || recentDiscomfortParts.includes(value)) {
        continue;
      }
      recentDiscomfortParts.push(value);
    }
    payload.recent_discomfort = recentDiscomfortParts.join("\n");

    if (!hasIntakeField("recent_discomfort_choice") && !payload.recent_discomfort_choice) {
      delete payload.recent_discomfort_choice;
    }
    if (!hasIntakeField("recent_discomfort_text") && !payload.recent_discomfort_text) {
      delete payload.recent_discomfort_text;
    }

    return payload;
  }

  async function sendMessageText(text, options = { source: "chat" }) {
    const normalized = String(text || "").trim();
    if (!normalized || isSending) {
      return false;
    }

    const fromIntake = options && options.source === "intake";
    const displayCandidate = options && typeof options.displayText === "string" ? options.displayText : "";
    const displayText = String(displayCandidate || normalized).trim() || normalized;
    let success = false;

    isSending = true;
    sendBtn.disabled = true;
    if (fromIntake) {
      intakeSubmitting = true;
      renderMessages();
    }

    addMessage("user", displayText);
    setStatusByKey("status_thinking");

    const pendingIndex = sessionMessages.length;
    sessionMessages.push({
      role: "assistant",
      text: "...",
      timestamp: nowIso(),
      error: false,
    });
    persistMessages();
    renderMessages();

    try {
      const resp = await fetch(CONFIG.apiBaseUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: normalized,
          user_id: userId,
          language: languageExplicit ? currentLanguage : null,
        }),
      });

      let data = null;
      try {
        data = await resp.json();
      } catch (_jsonErr) {
        data = null;
      }

      if (!resp.ok || !data) {
        throw new Error("server_error");
      }

      const replyText = data.reply || "No reply returned.";
      sessionMessages[pendingIndex] = {
        role: "assistant",
        text: replyText,
        timestamp: nowIso(),
        error: Boolean(data.timed_out),
      };
      persistMessages();
      renderMessages();

      const elapsed = Number(data.elapsed_ms || 0);
      if (data.timed_out) {
        setStatusByKey("status_timeout", elapsed);
      } else {
        setStatusByKey("status_done", elapsed);
      }
      success = true;
    } catch (_error) {
      sessionMessages[pendingIndex] = {
        role: "assistant",
        text: t("request_failed"),
        timestamp: nowIso(),
        error: true,
      };
      persistMessages();
      renderMessages();
      setStatusByKey("status_failed");
    } finally {
      isSending = false;
      sendBtn.disabled = false;
      if (fromIntake) {
        intakeSubmitting = false;
        if (success && INTAKE_CONFIG.auto_collapse_on_submit) {
          intakeCollapsed = true;
        }
        renderMessages();
      }
    }

    return success;
  }

  async function handleIntakeSubmit() {
    if (isSending) {
      return;
    }

    if (!validateIntakePayload()) {
      renderMessages();
      return;
    }

    const payload = buildIntakePayload();
    const prefix = t("intake_payload_prefix");
    const payloadText = `${prefix}\n\`\`\`json\n${JSON.stringify(payload, null, 2)}\n\`\`\``;
    const intakeSubmitNotice = getLocaleText(INTAKE_CONFIG.submit_notice, t("intake_submit_notice"));
    await sendMessageText(payloadText, {
      source: "intake",
      displayText: intakeSubmitNotice,
    });
  }

  menuBtn.addEventListener("click", () => setDrawer(true));
  drawerCloseBtn.addEventListener("click", () => setDrawer(false));
  overlay.addEventListener("click", () => setDrawer(false));

  langToggleBtn.addEventListener("click", () => {
    currentLanguage = currentLanguage === "zh" ? "en" : "zh";
    localStorage.setItem(LANGUAGE_KEY, currentLanguage);
    languageExplicit = true;
    localStorage.setItem(LANGUAGE_EXPLICIT_KEY, "1");
    applyLanguage();
  });

  function startNewChat() {
    resetConversation();
    setDrawer(false);
    input.focus();
  }

  newChatBtn.addEventListener("click", startNewChat);
  newChatInlineBtn.addEventListener("click", startNewChat);
  heroResetBtn.addEventListener("click", startNewChat);

  clearChatBtn.addEventListener("click", () => {
    sessionMessages = [];
    persistMessages();
    renderMessages();
    setStatusByKey("status_cleared");
    setDrawer(false);
  });

  exportTxtBtn.addEventListener("click", () => {
    exportTxt();
    setStatusByKey("status_txt_exported");
    setDrawer(false);
  });

  exportJsonBtn.addEventListener("click", () => {
    exportJson();
    setStatusByKey("status_json_exported");
    setDrawer(false);
  });

  toggleTimestamp.addEventListener("change", (event) => {
    setTimestampEnabled(event.target.checked);
  });

  toggleThemeBtn.addEventListener("click", () => {
    const isDark = document.body.classList.contains("theme-dark");
    applyTheme(isDark ? "light" : "dark");
    setDrawer(false);
  });

  input.addEventListener("input", () => {
    autoResizeTextarea(input);
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
    if (!text) {
      return;
    }
    input.value = "";
    autoResizeTextarea(input);
    await sendMessageText(text, { source: "chat" });
  });

  const savedTheme = localStorage.getItem(THEME_KEY) || "light";
  applyTheme(savedTheme);
  const showTs = localStorage.getItem(TIMESTAMP_KEY) === "1";
  setTimestampEnabled(showTs);
  applyLanguage();
  loadConversation();
  autoResizeTextarea(input);
})();
