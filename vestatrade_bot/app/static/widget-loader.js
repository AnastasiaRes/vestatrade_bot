(() => {
  const REGISTRY_KEY = "__vestaTradeChatWidgetInstances";
  const DEFAULT_GREETING =
    "Здравствуйте! Я AI-консультант Vesta Trading. Помогу подобрать товар, уточнить цену, наличие и ссылку на карточку. Опишите задачу своими словами.";
  const DEFAULT_QUICK_MESSAGES = [
    "Подберите циркуляционный насос подешевле",
    "Подберите электрический котёл для дома площадью 100 м²",
    "Дайте ссылку на товар",
  ];

  const ICONS = {
    close: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M18 6 6 18M6 6l12 12" />
      </svg>
    `,
    reset: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M21 12a9 9 0 1 1-2.64-6.36" />
        <path d="M21 4v6h-6" />
      </svg>
    `,
    send: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m22 2-7 20-4-9-9-4Z" />
        <path d="M22 2 11 13" />
      </svg>
    `,
  };

  window[REGISTRY_KEY] = window[REGISTRY_KEY] || {};

  const script = document.currentScript;
  const config = readConfig(script);
  if (window[REGISTRY_KEY][config.instanceId]) {
    return;
  }
  window[REGISTRY_KEY][config.instanceId] = true;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => mount(config), { once: true });
  } else {
    mount(config);
  }

  function readConfig(scriptElement) {
    const scriptUrl = scriptElement?.src
      ? new URL(scriptElement.src, document.baseURI)
      : new URL(document.baseURI);
    const data = scriptElement?.dataset || {};
    const globalConfig = window.VestaChatWidgetConfig || {};
    const apiBase = cleanBase(data.apiBase || globalConfig.apiBase || scriptUrl.origin);
    const assetsBase = cleanBase(
      data.assetsBase || globalConfig.assetsBase || apiBase || scriptUrl.origin,
    );
    const quickMessages = parseQuickMessages(data, globalConfig);

    return {
      instanceId: data.instanceId || globalConfig.instanceId || "default",
      apiBase,
      assetsBase,
      title: data.title || globalConfig.title || "AI-консультант",
      subtitle: data.subtitle || globalConfig.subtitle || "Vesta Trading",
      greeting: data.greeting || globalConfig.greeting || DEFAULT_GREETING,
      placeholder: data.placeholder || globalConfig.placeholder || "Введите сообщение",
      position: normalizePosition(data.position || globalConfig.position),
      accent: sanitizeColor(data.accent || globalConfig.accent, "#0655d9"),
      width: sanitizeLength(data.width || globalConfig.width, "390px"),
      height: sanitizeLength(data.height || globalConfig.height, "640px"),
      zIndex: sanitizeZIndex(data.zIndex || globalConfig.zIndex, 9999),
      iconUrl:
        data.iconUrl ||
        globalConfig.iconUrl ||
        joinUrl(assetsBase, "/static/vesta_iconka.png"),
      startOpen: parseBoolean(data.open || globalConfig.open, false),
      persistSession: parseBoolean(data.persistSession || globalConfig.persistSession, true),
      maxMessageLength: sanitizeMessageLength(
        data.maxMessageLength || globalConfig.maxMessageLength,
      ),
      quickMessages,
    };
  }

  function mount(config) {
    const hostId = `vesta-trade-chat-widget-${config.instanceId}`;
    if (document.getElementById(hostId)) {
      return;
    }

    const host = document.createElement("div");
    host.id = hostId;
    host.setAttribute("data-vesta-trade-chat-widget", config.instanceId);
    const shadow = host.attachShadow({ mode: "open" });
    shadow.appendChild(createStyle(config));
    shadow.appendChild(createTemplate(config));
    document.body.appendChild(host);
    initWidget(shadow, config);
  }

  function createStyle(config) {
    const style = document.createElement("style");
    style.textContent = `
      :host {
        all: initial;
        --vesta-accent: ${config.accent};
        --vesta-accent-dark: #0047bd;
        --vesta-ink: #10202b;
        --vesta-muted: #6e7b86;
        --vesta-line: #dce4ee;
        --vesta-panel: #f3f6fb;
        --vesta-surface: #ffffff;
        --vesta-success: #19a463;
        --vesta-danger: #d93025;
        --vesta-width: ${config.width};
        --vesta-height: ${config.height};
        --vesta-z-index: ${config.zIndex};
        --vesta-font: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-family: var(--vesta-font);
      }

      *, *::before, *::after {
        box-sizing: border-box;
      }

      button, textarea, a {
        font: inherit;
      }

      button {
        border: 0;
      }

      svg {
        display: block;
        width: 20px;
        height: 20px;
        fill: none;
        stroke: currentColor;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
      }

      [hidden] {
        display: none !important;
      }

      .vesta-widget {
        position: fixed;
        right: 22px;
        bottom: 22px;
        z-index: var(--vesta-z-index);
        display: grid;
        justify-items: end;
        color: var(--vesta-ink);
        font-family: var(--vesta-font);
        pointer-events: none;
      }

      .vesta-widget.position-left {
        right: auto;
        left: 22px;
        justify-items: start;
      }

      .launcher,
      .panel {
        pointer-events: auto;
      }

      .launcher {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        min-height: 60px;
        max-width: min(320px, calc(100vw - 32px));
        padding: 8px 16px 8px 8px;
        border-radius: 999px;
        background: var(--vesta-accent);
        color: #ffffff;
        box-shadow: 0 18px 48px rgba(7, 31, 78, 0.24);
        cursor: pointer;
        letter-spacing: 0;
        transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease;
      }

      .launcher:hover {
        background: var(--vesta-accent-dark);
        box-shadow: 0 22px 56px rgba(7, 31, 78, 0.3);
        transform: translateY(-1px);
      }

      .launcher:focus-visible,
      .icon-button:focus-visible,
      .send-button:focus-visible,
      .quick-button:focus-visible,
      .product-link:focus-visible,
      .composer-input:focus-visible {
        outline: 3px solid rgba(6, 85, 217, 0.24);
        outline-offset: 3px;
      }

      .launcher-avatar {
        display: grid;
        place-items: center;
        width: 44px;
        height: 44px;
        flex: 0 0 44px;
        overflow: hidden;
        border: 2px solid rgba(255, 255, 255, 0.86);
        border-radius: 50%;
        background: #ffffff;
      }

      .launcher-avatar img,
      .brand-avatar img,
      .message-avatar img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        object-position: 33% 24%;
        transform: scale(1.55) translateY(6px);
      }

      .launcher-label {
        display: grid;
        gap: 1px;
        min-width: 0;
        text-align: left;
      }

      .launcher-title {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: 15px;
        font-weight: 800;
        line-height: 1.1;
      }

      .launcher-subtitle {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        color: rgba(255, 255, 255, 0.82);
        font-size: 12px;
        font-weight: 700;
        line-height: 1.15;
      }

      .panel {
        display: none;
        grid-template-rows: auto minmax(0, 1fr) auto auto;
        width: min(var(--vesta-width), calc(100vw - 32px));
        height: min(var(--vesta-height), calc(100dvh - 32px));
        overflow: hidden;
        border: 1px solid rgba(16, 32, 43, 0.08);
        border-radius: 20px;
        background: var(--vesta-surface);
        box-shadow: 0 28px 80px rgba(7, 31, 78, 0.28);
      }

      .vesta-widget.is-open .panel {
        display: grid;
      }

      .vesta-widget.is-open .launcher {
        display: none;
      }

      .panel-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        min-height: 76px;
        padding: 14px 16px;
        background: linear-gradient(135deg, var(--vesta-accent-dark), var(--vesta-accent));
        color: #ffffff;
      }

      .brand {
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 0;
      }

      .brand-avatar {
        display: grid;
        place-items: center;
        width: 48px;
        height: 48px;
        flex: 0 0 48px;
        overflow: hidden;
        border: 2px solid rgba(255, 255, 255, 0.85);
        border-radius: 16px;
        background: #ffffff;
      }

      .brand-copy {
        min-width: 0;
      }

      .brand-subtitle,
      .status-text {
        margin: 0;
        color: rgba(255, 255, 255, 0.78);
        font-size: 12px;
        font-weight: 700;
        line-height: 1.25;
        letter-spacing: 0;
      }

      .brand-title {
        margin: 1px 0 2px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: 18px;
        font-weight: 900;
        line-height: 1.16;
        letter-spacing: 0;
      }

      .status-line {
        display: inline-flex;
        align-items: center;
        gap: 6px;
      }

      .status-dot {
        width: 8px;
        height: 8px;
        flex: 0 0 8px;
        border-radius: 50%;
        background: #fbbc04;
      }

      .status-dot.ok {
        background: #4ee299;
      }

      .status-dot.bad {
        background: #ff8a80;
      }

      .header-actions {
        display: flex;
        align-items: center;
        gap: 6px;
        flex: 0 0 auto;
      }

      .icon-button {
        display: grid;
        place-items: center;
        width: 40px;
        height: 40px;
        border-radius: 8px;
        background: rgba(255, 255, 255, 0.1);
        color: #ffffff;
        cursor: pointer;
      }

      .icon-button:hover {
        background: rgba(255, 255, 255, 0.18);
      }

      .messages {
        min-height: 0;
        overflow-y: auto;
        padding: 18px 16px 12px;
        background: var(--vesta-panel);
        scroll-behavior: smooth;
      }

      .message {
        display: flex;
        align-items: flex-end;
        gap: 9px;
        margin-bottom: 12px;
      }

      .message.user {
        justify-content: flex-end;
      }

      .message.user .message-avatar {
        order: 2;
        background: #526078;
        color: #ffffff;
      }

      .message-avatar {
        display: grid;
        place-items: center;
        width: 34px;
        height: 34px;
        flex: 0 0 34px;
        overflow: hidden;
        border: 2px solid #ffffff;
        border-radius: 50%;
        background: #ffffff;
        color: var(--vesta-accent);
        box-shadow: 0 8px 20px rgba(20, 47, 93, 0.12);
        font-size: 10px;
        font-weight: 900;
      }

      .message-avatar img {
        transform: scale(1.62) translateY(5px);
      }

      .bubble {
        max-width: min(292px, 82%);
        padding: 11px 13px;
        border-radius: 8px 18px 18px 18px;
        background: var(--vesta-accent);
        color: #ffffff;
        box-shadow: 0 10px 24px rgba(20, 47, 93, 0.12);
        font-size: 14px;
        line-height: 1.42;
        white-space: pre-line;
        word-break: break-word;
      }

      .message.user .bubble {
        border-radius: 18px 8px 18px 18px;
        background: #ffffff;
        color: #344150;
      }

      .typing-bubble {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        min-width: 58px;
        min-height: 39px;
      }

      .typing-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        background: rgba(255, 255, 255, 0.78);
        animation: vestaTyping 1s infinite ease-in-out;
      }

      .typing-dot:nth-child(2) {
        animation-delay: 120ms;
      }

      .typing-dot:nth-child(3) {
        animation-delay: 240ms;
      }

      @keyframes vestaTyping {
        0%, 80%, 100% {
          transform: translateY(0);
          opacity: 0.55;
        }
        40% {
          transform: translateY(-4px);
          opacity: 1;
        }
      }

      .products {
        display: grid;
        gap: 8px;
        margin-top: 10px;
      }

      .product-card {
        display: grid;
        grid-template-columns: auto minmax(0, 1fr);
        gap: 10px;
        padding: 10px;
        border: 1px solid rgba(255, 255, 255, 0.22);
        border-radius: 8px;
        background: rgba(255, 255, 255, 0.12);
      }

      .product-card.no-image {
        grid-template-columns: minmax(0, 1fr);
      }

      .product-image {
        width: 54px;
        height: 54px;
        overflow: hidden;
        border-radius: 8px;
        background: #ffffff;
      }

      .product-image img {
        width: 100%;
        height: 100%;
        object-fit: contain;
      }

      .product-info {
        display: grid;
        gap: 5px;
        min-width: 0;
      }

      .product-title {
        margin: 0;
        color: #ffffff;
        font-size: 13px;
        font-weight: 800;
        line-height: 1.25;
      }

      .product-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 5px 8px;
        color: rgba(255, 255, 255, 0.84);
        font-size: 12px;
        line-height: 1.25;
      }

      .product-link {
        justify-self: start;
        color: #ffffff;
        font-size: 12px;
        font-weight: 800;
        text-decoration: underline;
        text-underline-offset: 3px;
      }

      .quick-row {
        display: flex;
        gap: 8px;
        overflow-x: auto;
        padding: 10px 12px;
        border-top: 1px solid var(--vesta-line);
        background: #ffffff;
      }

      .quick-button {
        flex: 0 0 auto;
        max-width: 220px;
        padding: 8px 10px;
        overflow: hidden;
        border: 1px solid var(--vesta-line);
        border-radius: 8px;
        background: #ffffff;
        color: #344150;
        cursor: pointer;
        font-size: 13px;
        font-weight: 700;
        text-overflow: ellipsis;
        white-space: nowrap;
      }

      .quick-button:hover {
        border-color: rgba(6, 85, 217, 0.42);
        color: var(--vesta-accent);
      }

      .composer {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 44px;
        gap: 8px;
        align-items: end;
        padding: 12px;
        border-top: 1px solid var(--vesta-line);
        background: #ffffff;
      }

      .composer-input {
        width: 100%;
        max-height: 96px;
        min-height: 44px;
        resize: none;
        overflow-y: auto;
        padding: 12px 13px;
        border: 1px solid var(--vesta-line);
        border-radius: 8px;
        background: #ffffff;
        color: var(--vesta-ink);
        font-size: 14px;
        line-height: 1.35;
      }

      .composer-input::placeholder {
        color: #8c98a4;
      }

      .send-button {
        display: grid;
        place-items: center;
        width: 44px;
        height: 44px;
        border-radius: 8px;
        background: var(--vesta-accent);
        color: #ffffff;
        cursor: pointer;
      }

      .send-button:hover {
        background: var(--vesta-accent-dark);
      }

      .send-button:disabled,
      .composer-input:disabled,
      .quick-button:disabled {
        cursor: not-allowed;
        opacity: 0.58;
      }

      @media (max-width: 520px) {
        .vesta-widget,
        .vesta-widget.position-left {
          right: 12px;
          left: 12px;
          bottom: 12px;
          justify-items: end;
        }

        .panel {
          width: 100%;
          height: min(660px, calc(100dvh - 24px));
          border-radius: 18px;
        }

        .launcher {
          min-height: 58px;
          padding-right: 8px;
        }

        .launcher-label {
          display: none;
        }

        .bubble {
          max-width: 84%;
        }

        .brand-title {
          max-width: 180px;
        }
      }
    `;
    return style;
  }

  function createTemplate(config) {
    const template = document.createElement("template");
    template.innerHTML = `
      <div class="vesta-widget position-${config.position}">
        <button class="launcher" type="button" aria-label="Открыть чат" aria-expanded="false">
          <span class="launcher-avatar" aria-hidden="true">
            <img src="${escapeAttr(config.iconUrl)}" alt="" />
          </span>
          <span class="launcher-label">
            <span class="launcher-title">${escapeHtml(config.title)}</span>
            <span class="launcher-subtitle">${escapeHtml(config.subtitle)}</span>
          </span>
        </button>

        <section class="panel" role="dialog" aria-modal="false" aria-label="${escapeAttr(
          `${config.subtitle} ${config.title}`,
        )}">
          <header class="panel-header">
            <div class="brand">
              <span class="brand-avatar" aria-hidden="true">
                <img src="${escapeAttr(config.iconUrl)}" alt="" />
              </span>
              <div class="brand-copy">
                <p class="brand-subtitle">${escapeHtml(config.subtitle)}</p>
                <h2 class="brand-title">${escapeHtml(config.title)}</h2>
                <span class="status-line">
                  <span class="status-dot" aria-hidden="true"></span>
                  <span class="status-text">проверяю</span>
                </span>
              </div>
            </div>
            <div class="header-actions">
              <button class="icon-button reset-button" type="button" aria-label="Новый диалог" title="Новый диалог">
                ${ICONS.reset}
              </button>
              <button class="icon-button close-button" type="button" aria-label="Закрыть чат" title="Закрыть чат">
                ${ICONS.close}
              </button>
            </div>
          </header>

          <div class="messages" aria-live="polite"></div>
          <div class="quick-row"></div>

          <form class="composer">
            <textarea
              class="composer-input"
              rows="1"
              maxlength="${config.maxMessageLength}"
              autocomplete="off"
              placeholder="${escapeAttr(config.placeholder)}"
            ></textarea>
            <button class="send-button" type="submit" aria-label="Отправить">
              ${ICONS.send}
            </button>
          </form>
        </section>
      </div>
    `;
    return template.content.cloneNode(true);
  }

  function initWidget(root, config) {
    const widget = root.querySelector(".vesta-widget");
    const launcher = root.querySelector(".launcher");
    const panel = root.querySelector(".panel");
    const messages = root.querySelector(".messages");
    const form = root.querySelector(".composer");
    const input = root.querySelector(".composer-input");
    const sendButton = root.querySelector(".send-button");
    const closeButton = root.querySelector(".close-button");
    const resetButton = root.querySelector(".reset-button");
    const quickRow = root.querySelector(".quick-row");
    const statusDot = root.querySelector(".status-dot");
    const statusText = root.querySelector(".status-text");
    let sessionId = loadSessionId(config);
    let busy = false;

    appendMessage(messages, config, "bot", config.greeting);
    renderQuickMessages(quickRow, config, (text) => sendMessage(text));
    setOpen(config.startOpen);
    setSendState();
    checkHealth();

    launcher.addEventListener("click", () => setOpen(true));
    closeButton.addEventListener("click", () => setOpen(false));
    resetButton.addEventListener("click", resetChat);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      sendMessage(input.value);
    });
    input.addEventListener("input", () => {
      autoResize(input);
      setSendState();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
      if (event.key === "Escape") {
        setOpen(false);
      }
    });
    panel.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    });

    function setOpen(nextOpen) {
      widget.classList.toggle("is-open", nextOpen);
      launcher.setAttribute("aria-expanded", String(nextOpen));
      if (nextOpen) {
        requestAnimationFrame(() => input.focus());
      }
    }

    function resetChat() {
      sessionId = createSessionId();
      saveSessionId(config, sessionId);
      messages.textContent = "";
      appendMessage(messages, config, "bot", config.greeting);
      renderQuickMessages(quickRow, config, (text) => sendMessage(text));
      input.value = "";
      autoResize(input);
      setSendState();
      input.focus();
    }

    async function sendMessage(rawText) {
      const text = String(rawText || "").trim();
      if (!text || busy) {
        return;
      }

      busy = true;
      setBusy(true);
      quickRow.hidden = true;
      appendMessage(messages, config, "user", text);
      input.value = "";
      autoResize(input);
      const typing = appendTyping(messages, config);

      try {
        const response = await fetch(joinUrl(config.apiBase, "/chat"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "omit",
          body: JSON.stringify({ session_id: sessionId, message: text }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.detail || "сервер не принял запрос");
        }
        typing.remove();
        appendMessage(messages, config, "bot", payload.answer || "Не удалось сформировать ответ.", payload.products || []);
        setHealthStatus("ok");
      } catch (error) {
        typing.remove();
        appendMessage(
          messages,
          config,
          "bot",
          `Не удалось получить ответ: ${error.message}. Попробуйте ещё раз или обратитесь к менеджеру.`,
        );
        setHealthStatus("bad");
      } finally {
        busy = false;
        setBusy(false);
        input.focus();
      }
    }

    async function checkHealth() {
      try {
        const response = await fetch(joinUrl(config.apiBase, "/health"), {
          credentials: "omit",
          cache: "no-store",
        });
        if (!response.ok) {
          throw new Error("health check failed");
        }
        setHealthStatus("ok");
      } catch {
        setHealthStatus("bad");
      }
    }

    function setHealthStatus(status) {
      statusDot.classList.toggle("ok", status === "ok");
      statusDot.classList.toggle("bad", status === "bad");
      statusText.textContent = status === "ok" ? "на связи" : "нет связи";
    }

    function setBusy(nextBusy) {
      input.disabled = nextBusy;
      sendButton.disabled = nextBusy || !input.value.trim();
      quickRow.querySelectorAll("button").forEach((button) => {
        button.disabled = nextBusy;
      });
    }

    function setSendState() {
      sendButton.disabled = busy || !input.value.trim();
    }
  }

  function appendMessage(container, config, role, text, products = []) {
    const article = document.createElement("article");
    article.className = `message ${role}`;

    const avatar = document.createElement("span");
    avatar.className = "message-avatar";
    if (role === "bot") {
      const img = document.createElement("img");
      img.src = config.iconUrl;
      img.alt = "";
      avatar.appendChild(img);
    } else {
      avatar.textContent = "Вы";
    }

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const textNode = document.createElement("span");
    textNode.textContent = text;
    bubble.appendChild(textNode);

    if (products.length) {
      const grid = document.createElement("div");
      grid.className = "products";
      products.forEach((product) => grid.appendChild(renderProduct(product)));
      bubble.appendChild(grid);
    }

    article.appendChild(avatar);
    article.appendChild(bubble);
    container.appendChild(article);
    scrollMessages(container);
    return article;
  }

  function appendTyping(container, config) {
    const article = document.createElement("article");
    article.className = "message bot is-loading";

    const avatar = document.createElement("span");
    avatar.className = "message-avatar";
    const img = document.createElement("img");
    img.src = config.iconUrl;
    img.alt = "";
    avatar.appendChild(img);

    const bubble = document.createElement("div");
    bubble.className = "bubble typing-bubble";
    bubble.setAttribute("aria-label", "Ассистент печатает");
    for (let i = 0; i < 3; i += 1) {
      const dot = document.createElement("span");
      dot.className = "typing-dot";
      bubble.appendChild(dot);
    }

    article.appendChild(avatar);
    article.appendChild(bubble);
    container.appendChild(article);
    scrollMessages(container);
    return article;
  }

  function renderProduct(product) {
    const card = document.createElement("section");
    card.className = "product-card";

    const imageUrl = safeHttpUrl(product.image_url);
    if (imageUrl) {
      const imageBox = document.createElement("span");
      imageBox.className = "product-image";
      const image = document.createElement("img");
      image.src = imageUrl;
      image.alt = "";
      image.loading = "lazy";
      image.addEventListener("error", () => {
        imageBox.remove();
        card.classList.add("no-image");
      });
      imageBox.appendChild(image);
      card.appendChild(imageBox);
    } else {
      card.classList.add("no-image");
    }

    const info = document.createElement("div");
    info.className = "product-info";

    const title = document.createElement("h3");
    title.className = "product-title";
    title.textContent = product.name || "Товар";
    info.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "product-meta";
    [
      product.sku ? `Артикул: ${product.sku}` : "",
      formatPrice(product.price, product.currency),
      product.stock_status ? `Наличие: ${product.stock_status}` : "",
    ]
      .filter(Boolean)
      .forEach((item) => {
        const span = document.createElement("span");
        span.textContent = item;
        meta.appendChild(span);
      });
    info.appendChild(meta);

    const productUrl = safeHttpUrl(product.url);
    if (productUrl) {
      const link = document.createElement("a");
      link.className = "product-link";
      link.href = productUrl;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Открыть карточку";
      info.appendChild(link);
    }

    card.appendChild(info);
    return card;
  }

  function renderQuickMessages(container, config, onPick) {
    container.textContent = "";
    if (!config.quickMessages.length) {
      container.hidden = true;
      return;
    }
    config.quickMessages.forEach((message) => {
      const button = document.createElement("button");
      button.className = "quick-button";
      button.type = "button";
      button.textContent = message;
      button.addEventListener("click", () => onPick(message));
      container.appendChild(button);
    });
    container.hidden = false;
  }

  function autoResize(input) {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 96)}px`;
  }

  function scrollMessages(container) {
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  }

  function loadSessionId(config) {
    if (!config.persistSession) {
      return createSessionId();
    }
    const key = sessionStorageKey(config);
    const stored = safeLocalStorageGet(key);
    if (stored) {
      return stored;
    }
    const next = createSessionId();
    safeLocalStorageSet(key, next);
    return next;
  }

  function saveSessionId(config, sessionId) {
    if (config.persistSession) {
      safeLocalStorageSet(sessionStorageKey(config), sessionId);
    }
  }

  function sessionStorageKey(config) {
    return `vesta-trade-chat-session:${config.instanceId}:${config.apiBase}`;
  }

  function createSessionId() {
    if (window.crypto?.randomUUID) {
      return window.crypto.randomUUID();
    }
    return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }

  function parseQuickMessages(data, globalConfig) {
    const showQuick = parseBoolean(data.showQuick || globalConfig.showQuick, true);
    if (!showQuick) {
      return [];
    }
    const raw = data.quick || globalConfig.quick;
    if (Array.isArray(raw)) {
      return raw.map((item) => String(item).trim()).filter(Boolean).slice(0, 5);
    }
    if (typeof raw === "string" && raw.trim()) {
      return raw.split("|").map((item) => item.trim()).filter(Boolean).slice(0, 5);
    }
    return DEFAULT_QUICK_MESSAGES;
  }

  function formatPrice(price, currency) {
    const number = Number(price);
    if (!Number.isFinite(number)) {
      return "";
    }
    const label = currency === "RUB" ? "₽" : currency || "";
    return `Цена: ${new Intl.NumberFormat("ru-RU").format(number)} ${label}`.trim();
  }

  function cleanBase(value) {
    return String(value || "").replace(/\/+$/, "");
  }

  function joinUrl(base, path) {
    return `${cleanBase(base)}${path.startsWith("/") ? path : `/${path}`}`;
  }

  function normalizePosition(value) {
    return value === "left" ? "left" : "right";
  }

  function parseBoolean(value, fallback) {
    if (value === undefined || value === null || value === "") {
      return fallback;
    }
    if (typeof value === "boolean") {
      return value;
    }
    return ["1", "true", "yes", "on"].includes(String(value).toLowerCase());
  }

  function sanitizeColor(value, fallback) {
    if (!value) {
      return fallback;
    }
    const probe = document.createElement("span");
    probe.style.color = String(value);
    return probe.style.color ? String(value) : fallback;
  }

  function sanitizeLength(value, fallback) {
    const text = String(value || "");
    return /^(\d+|\d+\.\d+)(px|rem|em|vh|vw|%)$/.test(text) ? text : fallback;
  }

  function sanitizeZIndex(value, fallback) {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed)) {
      return fallback;
    }
    return Math.max(1, Math.min(parsed, 2147483647));
  }

  function sanitizeMessageLength(value) {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed)) {
      return 1000;
    }
    return Math.max(200, Math.min(parsed, 3000));
  }

  function safeHttpUrl(value) {
    if (!value) {
      return "";
    }
    try {
      const url = new URL(String(value), window.location.href);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch {
      return "";
    }
  }

  function safeLocalStorageGet(key) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function safeLocalStorageSet(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // Storage can be disabled in private mode; a per-page session still works.
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replaceAll("'", "&#39;");
  }
})();
