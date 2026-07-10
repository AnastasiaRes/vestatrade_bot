# Vesta Trade AI Consultant MVP

MVP чат-бота для интернет-магазина Vesta Trade. Бот работает как продавец-консультант: понимает живой запрос, задаёт 1-2 уточняющих вопроса, ищет товары в XML-фиде, показывает карточки с ценой, наличием и прямой ссылкой.

LLM-роли вызываются через настраиваемый провайдер (`ollama` по умолчанию) и только после rule-based логики. Ключи API не хранятся в коде, дневной бюджет контролируется локально.

## Архитектура

- `IntentRouterAgent` определяет тип запроса, категорию, SKU, бренд, наличие, “подешевле”, small talk и смену темы.
- `SlotFillingAgent` ведёт короткие сценарии по трубам, насосам, котлам, кранам, канализации и радиаторной арматуре.
- `FeedSearchAgent` загружает нормализованные товары и делает гибридный поиск.
- `RankingAgent` сортирует по SKU, цене, наличию, бренду и мощности котлов.
- `ProductCardAgent` формирует карточки строго из фида.
- `GuardrailsAgent` запрещает выдуманные ссылки, цены, остатки, характеристики и неподтверждённую комплектацию.
- `ResponseComposerAgent` собирает короткий ответ.
- `HandoffAgent` готовит summary для менеджера, если безопасно ответить нельзя.

## Установка

```bash
cd vestatrade_bot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Откройте `.env` и укажите Ollama-сервер:

```bash
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://100.83.233.66:11434
OLLAMA_MODEL=qwen2.5-coder:7b
OLLAMA_MODEL_STRONG=qwen2.5-coder:7b
DAILY_BUDGET_USD=10

# Старый OpenRouter-режим оставлен для отката:
# LLM_PROVIDER=openrouter
# OPENROUTER_API_KEY=your_openrouter_key_here
# OPENROUTER_MODEL=qwen/qwen3-vl-8b-instruct
# OPENROUTER_MODEL_STRONG=qwen/qwen3-vl-8b-instruct
```

Если LLM-провайдер не настроен или дневной бюджет исчерпан, бот не падает: LLM-вызовы отключаются, используется безопасный rule-based fallback.

## Запуск

```bash
uvicorn app.main:app --reload
```

После запуска откройте в браузере:

```text
http://127.0.0.1:8000
```

Там будет визуальный чат: слева переписка с ботом и карточки найденных товаров, справа быстрые тестовые запросы, статус фида, кнопка перезагрузки фида и debug-блок с распознанным интентом, категорией и слотами.

При старте сервер попробует скачать фид:

```text
https://www.vestatrade.ru/index.php?route=extension/feed/unixml/all_products
```

Если фид временно недоступен, используется последний кэш `app/data/products_cache.json`.

## API

Проверка сервиса:

```bash
curl http://127.0.0.1:8000/health
```

Чат:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"demo","message":"циркуляционный насос, подешевле"}'
```

Перезагрузка фида:

```bash
curl -X POST http://127.0.0.1:8000/reload-feed
```

## Бюджет LLM

Файл учёта расходов:

```text
app/data/usage_budget.json
```

Перед каждым LLM-вызовом проверяется дневной лимит `DAILY_BUDGET_USD`. После успешного вызова записываются примерные токены и стоимость. Для локальной Ollama цены по умолчанию нулевые. Если нужен платный провайдер, цены можно переопределить:

```bash
LLM_INPUT_PRICE_PER_1M_TOKENS_USD=0.08
LLM_OUTPUT_PRICE_PER_1M_TOKENS_USD=0.30

# Старые OpenRouter-переменные тоже поддерживаются:
# OPENROUTER_INPUT_PRICE_PER_1M_TOKENS_USD=0.08
# OPENROUTER_OUTPUT_PRICE_PER_1M_TOKENS_USD=0.30
```

## Проверка LLM

Проверить, какая LLM реально используется и отвечает ли endpoint:

```bash
python scripts/check_llm.py
```

Для разовой проверки через удалённый Ollama-сервер:

```bash
OLLAMA_BASE_URL=http://100.83.233.66:11434 python scripts/check_llm.py
```

Если в результате `FAILED` или указан `localhost:11434`, но локальная Ollama не запущена,
бот будет работать через безопасный fallback без LLM-ответа.

## Тесты

```bash
pytest
```

Тесты используют локальные fixtures и не требуют живого фида или LLM API.

## Команды из ТЗ

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

```bash
pytest
```

## Документация товаров (паспорта, PDF)

Чтобы бот отвечал на вопросы о комплектации и характеристиках по официальным
документам, положите файлы в `data/` или `app/data/product_docs/`
(каталог настраивается через `PRODUCT_DOCS_DIR`). Привязка к товарам:

1. Серийные паспорта (один документ на семейство артикулов) описываются в
   `data/product_docs_map.json`:

   ```json
   {
     "VT.033-034-0425.pdf": {"sku_prefixes": ["VT.033", "VT.034"]},
     "газовые котлы ARDERIA.pdf": {"brand": "Arderia", "name_contains_any": ["газовый"]}
   }
   ```

2. Файл, названный точным артикулом, привязывается автоматически:

   ```
   app/data/product_docs/VT.1500.0.0.pdf
   app/data/product_docs/68-2-8.pdf   # слэши в артикуле заменяются дефисами
   ```

3. Имена вида `VT.226-227-228-1248в.pdf` раскрываются в серии по общему префиксу.

Поддерживаются `.pdf`, `.txt`, `.md`. Документы подхватываются при старте и по
кнопке «Обновить фид»; количество привязанных документов видно в `/health`
(`product_docs_loaded`). Текст документа используется:
- для подтверждения комплектации («в котле есть насос и бак?») — бот отвечает
  «да» только если это написано в фиде или документе;
- как контекст для ответов на свободные вопросы о показанном товаре.

## Сплошная проверка по фиду

```bash
.venv/bin/python scripts/check_feed_coverage.py
```

Прогоняет бота по всем карточкам кэша (поиск по артикулу и по полному названию)
и пишет отчёт в `reports/feed_coverage_report.md`.

## Сохранение переписок

Каждый диалог через `/chat` сохраняется в Markdown-файл:

```
app/data/chat_logs/<ГГГГ-ММ-ДД>/<session_id>.md
```

Внутри — реплики с временем, показанные товары и пометки о передаче менеджеру.
Каталог настраивается через `CHAT_LOGS_DIR`. Логи не попадают в git.
