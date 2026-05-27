const apiBase = window.location.protocol === "file:" ? "http://127.0.0.1:8000" : "";

let sessionId = localStorage.getItem("vestatrade_session_id") || crypto.randomUUID();
localStorage.setItem("vestatrade_session_id", sessionId);

const messages = document.querySelector("#messages");
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const statusBox = document.querySelector("#status");
const feedInfo = document.querySelector("#feedInfo");
const debugBox = document.querySelector("#debugBox");
const reloadFeed = document.querySelector("#reloadFeed");
const resetChat = document.querySelector("#resetChat");
const statusCard = document.querySelector(".status-card");

const greeting =
  "Здравствуйте! Я AI-консультант Vesta Trading. Помогу найти подходящее решение по ассортименту: подберу товар по запросу, уточню цену, наличие и основные характеристики, а также направлю на карточку товара. Опишите задачу своими словами — я подскажу оптимальный вариант.";

function apiUrl(path) {
  return `${apiBase}${path}`;
}

function appendMessage(role, text, products = []) {
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = `avatar ${role === "bot" ? "bot-avatar" : "user-avatar"}`;
  if (role === "user") {
    avatar.textContent = "Вы";
  } else {
    const avatarImage = document.createElement("img");
    avatarImage.src = "/static/vesta_iconka.png";
    avatarImage.alt = "";
    avatar.appendChild(avatarImage);
  }

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;

  if (products.length) {
    const grid = document.createElement("div");
    grid.className = "products";
    products.forEach((product) => grid.appendChild(renderProduct(product)));
    bubble.appendChild(grid);
  }

  article.appendChild(avatar);
  article.appendChild(bubble);
  messages.appendChild(article);
  scrollMessages();
  return article;
}

function appendTyping() {
  const article = document.createElement("article");
  article.className = "message bot is-loading";
  article.innerHTML = `
    <div class="avatar bot-avatar"><img src="/static/vesta_iconka.png" alt="" /></div>
    <div class="bubble" aria-label="Ассистент печатает">
      <span class="typing-dot"></span>
      <span class="typing-dot"></span>
      <span class="typing-dot"></span>
    </div>
  `;
  messages.appendChild(article);
  scrollMessages();
  return article;
}

function scrollMessages() {
  messages.scrollTop = messages.scrollHeight;
}

function renderProduct(product) {
  const card = document.createElement("section");
  card.className = "product-card";

  const title = document.createElement("h3");
  title.textContent = product.name;
  card.appendChild(title);

  const meta = document.createElement("div");
  meta.className = "product-meta";
  meta.innerHTML = `
    <span>Артикул: ${escapeHtml(product.sku)}</span>
    <span>Цена: ${Number(product.price).toLocaleString("ru-RU")} ${escapeHtml(product.currency)}</span>
    <span>Наличие: ${escapeHtml(product.stock_status)}</span>
  `;
  card.appendChild(meta);

  const link = document.createElement("a");
  link.href = product.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "Открыть карточку товара";
  card.appendChild(link);

  return card;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function refreshHealth() {
  try {
    const response = await fetch(apiUrl("/health"));
    const data = await response.json();
    statusBox.textContent = "Сервис работает";
    statusCard.classList.remove("bad");
    feedInfo.textContent = `Товаров загружено: ${data.products_loaded}. Источник: ${data.products_loaded_from}.`;
  } catch (error) {
    statusBox.textContent = "Сервис недоступен";
    statusCard.classList.add("bad");
    feedInfo.textContent = "Запустите FastAPI и откройте http://127.0.0.1:8000.";
  }
}

async function sendMessage(text) {
  appendMessage("user", text);
  input.value = "";
  input.disabled = true;
  form.querySelector("button").disabled = true;
  const typing = appendTyping();

  try {
    const response = await fetch(apiUrl("/chat"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Ошибка ответа сервера");
    }
    typing.remove();
    appendMessage("bot", data.answer, data.products || []);
    debugBox.textContent = JSON.stringify(data.debug || {}, null, 2);
  } catch (error) {
    typing.remove();
    appendMessage("bot", `Не удалось получить ответ: ${error.message}`);
  } finally {
    input.disabled = false;
    form.querySelector("button").disabled = false;
    input.focus();
    refreshHealth();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (text) {
    sendMessage(text);
  }
});

document.querySelectorAll(".quick").forEach((button) => {
  button.addEventListener("click", () => {
    sendMessage(button.dataset.message);
  });
});

reloadFeed.addEventListener("click", async () => {
  reloadFeed.disabled = true;
  reloadFeed.textContent = "Обновляю...";
  try {
    const response = await fetch(apiUrl("/reload-feed"), { method: "POST" });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Ошибка перезагрузки фида");
    }
    feedInfo.textContent = `Фид обновлён. Товаров: ${data.products_count}. Источник: ${data.source}.`;
  } catch (error) {
    feedInfo.textContent = `Не удалось обновить фид: ${error.message}`;
  } finally {
    reloadFeed.disabled = false;
    reloadFeed.textContent = "Обновить фид";
    refreshHealth();
  }
});

resetChat.addEventListener("click", () => {
  sessionId = crypto.randomUUID();
  localStorage.setItem("vestatrade_session_id", sessionId);
  messages.innerHTML = "";
  appendMessage("bot", greeting);
  debugBox.textContent = "{}";
  input.value = "";
  input.focus();
});

refreshHealth();
