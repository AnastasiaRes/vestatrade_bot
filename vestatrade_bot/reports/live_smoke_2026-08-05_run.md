# Протокол тестовых диалогов Vesta Trading AI-консультанта

Источник сценариев: `Глубокий набор тестовых диалогов для AI-консультанта Vesta Trading.pdf`.
Локальный API: `https://bot-api-vestatrade.ru`.
Начало: `2026-08-05T18:07:34`.
Окончание: `2026-08-05T18:09:25`.
Health перед запуском: `{"status": "ok", "products_loaded": 14035, "products_loaded_from": "file", "product_docs_loaded": 12, "llm_provider": "ollama", "llm_configured": true, "llm_model": "qwen2.5-coder:7b", "llm_request_timeout_seconds": 180.0, "llm_attempt_timeout_seconds": 180.0, "llm_max_retries": 2}`.
Снимок каталога оценщика: `{"path": "/Users/anastasiagorbunova/Documents/chat_bot/vestatrade_bot/app/data/products_cache.json", "raw_count": 14035, "indexed_unique_skus": 14029, "sha256": "f902d780e7ebd82cada4dfc25c5d609ff8f9be59c93d2a1adb3c1b57bce1524f"}`.
LLM telemetry: `{"mode": "live-llm", "turns": 16, "requested": 13, "transport_succeeded": 13, "output_accepted": 12}`.
LLM spent до запуска: `$0.000000`.
LLM spent после запуска: `$0.000000`.
Расход на прогон: `$0.000000`.

## 1. Точный SKU без лишних вопросов

Категория: `ссылка`. Приоритет: `P0`. Вердикт: **PASS**.

### Ход 1

Пользователь: VT.217.N.04

Время ответа: `0.37` сек.

Ответ бота:

```text
Нашёл подходящие варианты:
1. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.
   Артикул: VT.217.N.04
   Бренд: VALTEC
   Цена: 452 RUB
   Наличие: в наличии, 76 шт.
   Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка
   Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/
Могу показать сопоставимые аналоги.
```

Товары:
- `VT.217.N.04` | Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. | 452.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/

Debug:

```json
{
  "intent": "exact_sku",
  "category": "valves",
  "slots": {
    "sku": "VT.217.N.04"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "valves": {
        "category": "valves",
        "scope": null,
        "slots": {},
        "pending": null
      }
    },
    "category_last_goal": {
      "valves": "valves"
    },
    "active_goal": "valves",
    "active_category": "valves",
    "categories": {
      "valves": {}
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [
    "valves"
  ],
  "restored_product_skus": null,
  "agents_used": [
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "FeedSearchAgent",
    "RankingAgent",
    "ProductCardAgent",
    "GuardrailsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": false,
  "llm_requested": false,
  "llm_transport_succeeded": false,
  "llm_output_accepted": false,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": false,
  "engineering_llm_used": false,
  "engineering_llm_output_accepted": false,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": false,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

### Ход 2

Пользователь: скинь ссылку

Время ответа: `3.08` сек.

Ответ бота:

```text
Ссылка на товар VT.217.N.04: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/
```

Товары:
- `VT.217.N.04` | Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. | 452.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/

Debug:

```json
{
  "intent": "link_request",
  "category": "other",
  "slots": {
    "sku": "VT.217.N.04"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "valves": {
        "category": "valves",
        "scope": null,
        "slots": {},
        "pending": null
      }
    },
    "category_last_goal": {
      "valves": "valves"
    },
    "active_goal": "valves",
    "active_category": "valves",
    "categories": {
      "valves": {}
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [
    "valves"
  ],
  "restored_product_skus": null,
  "agents_used": [
    "IntentRouterAgent",
    "ResponseComposerAgent",
    "GuardrailsAgent"
  ],
  "llm_used": false,
  "llm_requested": false,
  "llm_transport_succeeded": false,
  "llm_output_accepted": false,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": false,
  "engineering_llm_used": false,
  "engineering_llm_output_accepted": false,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": false,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

## 6. Простой запрос нужен насос

Категория: `насосы`. Приоритет: `P1`. Вердикт: **PASS**.

### Ход 1

Пользователь: нужен насос

Время ответа: `9.95` сек.

Ответ бота:

```text
Для какой задачи нужен насос: отопление, водоснабжение/полив, повышение давления или откачка воды?
```

Debug:

```json
{
  "intent": "broad_category",
  "category": "pumps",
  "slots": {
    "product_kind": "насос"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "pumps": {
        "category": "pumps",
        "scope": null,
        "slots": {},
        "pending": {
          "question_id": "pumps.pump_use",
          "text": "Для какой задачи нужен насос: отопление, водоснабжение/полив, повышение давления или откачка воды?",
          "expected_slots": [
            "pump_use",
            "pump_type",
            "inlet_pressure_bar",
            "required_pressure_bar"
          ],
          "attempts": 0,
          "category": "pumps",
          "intent_type": "broad_category"
        }
      }
    },
    "category_last_goal": {
      "pumps": "pumps"
    },
    "active_goal": "pumps",
    "active_category": "pumps",
    "categories": {
      "pumps": {}
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [],
  "restored_product_skus": null,
  "agents_used": [
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": true,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": true,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

### Ход 2

Пользователь: для отопления, 130 мм

Время ответа: `6.91` сек.

Ответ бота:

```text
Понял, нужен циркуляционный насос. Это замена старого или новый подбор? Для замены пришлите маркировку, присоединение и монтажную длину. Для нового подбора нужны расчётный расход (м³/ч), напор (м) и схема системы; монтажный размер всё равно нужно сверить.
```

Debug:

```json
{
  "intent": "attribute_request",
  "category": "pumps",
  "slots": {
    "product_kind": "насос",
    "pump_use": "отопление",
    "mounting_length_mm": 130,
    "pump_type": "циркуляционный",
    "pump_selection_mode": "новый подбор"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "pumps": {
        "category": "pumps",
        "scope": null,
        "slots": {},
        "pending": {
          "question_id": "pumps.pump_use",
          "text": "Для какой задачи нужен насос: отопление, водоснабжение/полив, повышение давления или откачка воды?",
          "expected_slots": [
            "pump_use",
            "pump_type",
            "inlet_pressure_bar",
            "required_pressure_bar"
          ],
          "attempts": 0,
          "category": "pumps",
          "intent_type": "broad_category"
        }
      },
      "pumps:heating": {
        "category": "pumps",
        "scope": null,
        "slots": {
          "pump_use": "отопление",
          "mounting_length_mm": 130,
          "pump_type": "циркуляционный",
          "pump_selection_mode": "новый подбор"
        },
        "pending": {
          "question_id": "pumps.head_m",
          "text": "Понял, нужен циркуляционный насос. Это замена старого или новый подбор? Для замены пришлите маркировку, присоединение и монтажную длину. Для нового подбора нужны расчётный расход (м³/ч), напор (м) и схема системы; монтажный размер всё равно нужно сверить.",
          "expected_slots": [
            "head_m",
            "required_head_m",
            "required_flow_m3_h",
            "connection_size"
          ],
          "attempts": 0,
          "category": "pumps",
          "intent_type": "attribute_request"
        }
      }
    },
    "category_last_goal": {
      "pumps": "pumps:heating"
    },
    "active_goal": "pumps:heating",
    "active_category": "pumps",
    "categories": {
      "pumps": {
        "pump_use": "отопление",
        "mounting_length_mm": 130,
        "pump_type": "циркуляционный",
        "pump_selection_mode": "новый подбор"
      }
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [],
  "restored_product_skus": null,
  "agents_used": [
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": true,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": true,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

### Ход 3

Пользователь: 25/6

Время ответа: `7.53` сек.

Ответ бота:

```text
Нашёл подходящие варианты:
1. Насос циркуляционный VALTEC RS 25/6-130 с гайками
   Артикул: VRS.256.13.0
   Бренд: VALTEC
   Цена: 4311 RUB
   Наличие: в наличии, 11 шт.
   Характеристики: тип товара: Насос; тип насоса: Циркуляционный; максимальный напор, м: 6
   Ссылка: https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-valtec/nasos-cirkulyacionnyy-valtec-rs-256-130-s-gaykami/
2. Насос циркуляционный VALTEC RS 25/6-130 EA с гайками
   Артикул: VRS.256EA.13.0
   Бренд: VALTEC
   Цена: 8286 RUB
   Наличие: нет в наличии, 0 шт.
   Характеристики: тип товара: Насос; тип насоса: Циркуляционный; максимальный напор, м: 6
   Ссылка: https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-valtec/nasos-cirkulyacionnyy-valtec-rs-256-130-ea-s-gaykami/
3. Насос циркуляционный Wilo Star RS 25/6-130-RK
   Артикул: 9168934
   Бренд: Wilo
   Цена: 7148 RUB
   Наличие: в наличии, 9 шт.
   Характеристики: тип товара: Насос; тип насоса: Циркуляционный; максимальный напор, м: 6
   Ссылка: https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-wilo/nasos-cirkulyacionnyj-wilo-star-rs-25-6-130-rk/
Могу сравнить эти варианты по главным отличиям для вашей задачи.

Кстати, в контуре отопления по обе стороны насоса часто ставят два запорных крана, чтобы узел можно было снять без слива всей системы. Размер крана нельзя выбирать только по маркировке насоса: нужно сверить паспорт насоса, штатные гайки и резьбу трубопровода со стороны системы. Напишите этот размер — тогда проверю краны в наличии.
```

Товары:
- `VRS.256.13.0` | Насос циркуляционный VALTEC RS 25/6-130 с гайками | 4311.0 RUB | в наличии | https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-valtec/nasos-cirkulyacionnyy-valtec-rs-256-130-s-gaykami/
- `VRS.256EA.13.0` | Насос циркуляционный VALTEC RS 25/6-130 EA с гайками | 8286.0 RUB | нет в наличии | https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-valtec/nasos-cirkulyacionnyy-valtec-rs-256-130-ea-s-gaykami/
- `9168934` | Насос циркуляционный Wilo Star RS 25/6-130-RK | 7148.0 RUB | в наличии | https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-wilo/nasos-cirkulyacionnyj-wilo-star-rs-25-6-130-rk/

Debug:

```json
{
  "intent": "attribute_request",
  "category": "pumps",
  "slots": {
    "product_kind": "насос",
    "pump_use": "отопление",
    "mounting_length_mm": 130,
    "pump_type": "циркуляционный",
    "pump_selection_mode": "по заданным параметрам",
    "connection_size": 25,
    "head_m": 6.0,
    "companion_hint_pumps": true
  },
  "project_context": {
    "version": 2,
    "goals": {
      "pumps": {
        "category": "pumps",
        "scope": null,
        "slots": {},
        "pending": {
          "question_id": "pumps.pump_use",
          "text": "Для какой задачи нужен насос: отопление, водоснабжение/полив, повышение давления или откачка воды?",
          "expected_slots": [
            "pump_use",
            "pump_type",
            "inlet_pressure_bar",
            "required_pressure_bar"
          ],
          "attempts": 0,
          "category": "pumps",
          "intent_type": "broad_category"
        }
      },
      "pumps:heating": {
        "category": "pumps",
        "scope": null,
        "slots": {
          "pump_use": "отопление",
          "mounting_length_mm": 130,
          "pump_type": "циркуляционный",
          "pump_selection_mode": "по заданным параметрам",
          "connection_size": 25,
          "head_m": 6.0
        },
        "pending": null
      }
    },
    "category_last_goal": {
      "pumps": "pumps:heating"
    },
    "active_goal": "pumps:heating",
    "active_category": "pumps",
    "categories": {
      "pumps": {
        "pump_use": "отопление",
        "mounting_length_mm": 130,
        "pump_type": "циркуляционный",
        "pump_selection_mode": "по заданным параметрам",
        "connection_size": 25,
        "head_m": 6.0
      }
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [
    "pumps"
  ],
  "restored_product_skus": null,
  "agents_used": [
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "FeedSearchAgent",
    "RankingAgent",
    "ProductCardAgent",
    "GuardrailsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": true,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": true,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

## 29. Повтори ссылку и карточку ещё раз

Категория: `ссылка`. Приоритет: `P1`. Вердикт: **PASS**.

### Ход 1

Пользователь: покажи шаровый кран 1/2

Время ответа: `6.64` сек.

Ответ бота:

```text
Уточните для чего нужен кран: вода (холодная/горячая), отопление или радиатор.
```

Debug:

```json
{
  "intent": "attribute_request",
  "category": "valves",
  "slots": {
    "size_inch": "1/2",
    "valve_kind": "шаровый кран"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "valves": {
        "category": "valves",
        "scope": null,
        "slots": {
          "size_inch": "1/2",
          "valve_kind": "шаровый кран"
        },
        "pending": {
          "question_id": "valves.application",
          "text": "Уточните для чего нужен кран: вода (холодная/горячая), отопление или радиатор.",
          "expected_slots": [
            "application"
          ],
          "attempts": 0,
          "category": "valves",
          "intent_type": "attribute_request"
        }
      }
    },
    "category_last_goal": {
      "valves": "valves"
    },
    "active_goal": "valves",
    "active_category": "valves",
    "categories": {
      "valves": {
        "size_inch": "1/2",
        "valve_kind": "шаровый кран"
      }
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [],
  "restored_product_skus": null,
  "agents_used": [
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": true,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": true,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

### Ход 2

Пользователь: для воды

Время ответа: `6.94` сек.

Ответ бота:

```text
Нашёл подходящие варианты:
1. Кран шаровой MINI 1/2" вн.-нар.
   Артикул: VT.331.N.04
   Бренд: VALTEC
   Цена: 449 RUB
   Наличие: в наличии, 60 шт.
   Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини
   Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/
2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.
   Артикул: VT.217.N.04
   Бренд: VALTEC
   Цена: 452 RUB
   Наличие: в наличии, 76 шт.
   Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка
   Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/
3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2"
   Артикул: VT.217S.N.04
   Бренд: VALTEC
   Цена: 485 RUB
   Наличие: в наличии, 5 шт.
   Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка
   Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/
Могу сравнить эти варианты по главным отличиям для вашей задачи.
```

Товары:
- `VT.331.N.04` | Кран шаровой MINI 1/2" вн.-нар. | 449.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/
- `VT.217.N.04` | Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. | 452.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/
- `VT.217S.N.04` | Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" | 485.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/

Debug:

```json
{
  "intent": "attribute_request",
  "category": "valves",
  "slots": {
    "size_inch": "1/2",
    "valve_kind": "шаровый кран",
    "application": "вода"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "valves": {
        "category": "valves",
        "scope": null,
        "slots": {
          "size_inch": "1/2",
          "valve_kind": "шаровый кран",
          "application": "вода"
        },
        "pending": null
      }
    },
    "category_last_goal": {
      "valves": "valves"
    },
    "active_goal": "valves",
    "active_category": "valves",
    "categories": {
      "valves": {
        "size_inch": "1/2",
        "valve_kind": "шаровый кран",
        "application": "вода"
      }
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [
    "valves"
  ],
  "restored_product_skus": null,
  "agents_used": [
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "FeedSearchAgent",
    "RankingAgent",
    "ProductCardAgent",
    "GuardrailsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": true,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": true,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

### Ход 3

Пользователь: повтори ссылку ещё раз и артикул тоже

Время ответа: `3.05` сек.

Ответ бота:

```text
Вот ссылки на показанные товары:
1. VT.331.N.04: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/
2. VT.217.N.04: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/
3. VT.217S.N.04: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/
```

Товары:
- `VT.331.N.04` | Кран шаровой MINI 1/2" вн.-нар. | 449.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/
- `VT.217.N.04` | Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. | 452.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/
- `VT.217S.N.04` | Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" | 485.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/

Debug:

```json
{
  "intent": "link_request",
  "category": "other",
  "slots": {
    "size_inch": "1/2",
    "valve_kind": "шаровый кран",
    "application": "вода"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "valves": {
        "category": "valves",
        "scope": null,
        "slots": {
          "size_inch": "1/2",
          "valve_kind": "шаровый кран",
          "application": "вода"
        },
        "pending": null
      }
    },
    "category_last_goal": {
      "valves": "valves"
    },
    "active_goal": "valves",
    "active_category": "valves",
    "categories": {
      "valves": {
        "size_inch": "1/2",
        "valve_kind": "шаровый кран",
        "application": "вода"
      }
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [
    "valves"
  ],
  "restored_product_skus": null,
  "agents_used": [
    "IntentRouterAgent",
    "ResponseComposerAgent",
    "GuardrailsAgent"
  ],
  "llm_used": false,
  "llm_requested": false,
  "llm_transport_succeeded": false,
  "llm_output_accepted": false,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": false,
  "engineering_llm_used": false,
  "engineering_llm_output_accepted": false,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": false,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

### Ход 4

Пользователь: ты точно тот же товар прислал?

Время ответа: `10.28` сек.

Ответ бота:

```text
Да, это VT.331.N.04 — Кран шаровой MINI 1/2" вн.-нар.. Цена: 449 RUB. Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/
```

Товары:
- `VT.331.N.04` | Кран шаровой MINI 1/2" вн.-нар. | 449.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/
- `VT.217.N.04` | Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. | 452.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/
- `VT.217S.N.04` | Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" | 485.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/

Debug:

```json
{
  "intent": "unknown",
  "category": "valves",
  "slots": {
    "size_inch": "1/2",
    "valve_kind": "шаровый кран",
    "application": "вода"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "valves": {
        "category": "valves",
        "scope": null,
        "slots": {
          "size_inch": "1/2",
          "valve_kind": "шаровый кран",
          "application": "вода"
        },
        "pending": null
      }
    },
    "category_last_goal": {
      "valves": "valves"
    },
    "active_goal": "valves",
    "active_category": "valves",
    "categories": {
      "valves": {
        "size_inch": "1/2",
        "valve_kind": "шаровый кран",
        "application": "вода"
      }
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [
    "valves"
  ],
  "restored_product_skus": null,
  "agents_used": [
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "ResponseComposerAgent",
    "GuardrailsAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": true,
  "final_answer_source": "response_llm",
  "llm_rejection_reason": "intent_sanity_check_override",
  "intent_llm_used": true,
  "intent_llm_requested": true,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": "intent_sanity_check_override",
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": true,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": true,
  "response_llm_requested": true,
  "response_llm_output_accepted": true,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

## 36. Смена темы с крана на котёл

Категория: `смена темы`. Приоритет: `P0`. Вердикт: **PASS**.

### Ход 1

Пользователь: нужен кран шаровый

Время ответа: `6.4` сек.

Ответ бота:

```text
Уточните для чего нужен кран: вода (холодная/горячая), отопление или радиатор и размер: 1/2, 3/4 или диаметр в мм.
```

Debug:

```json
{
  "intent": "broad_category",
  "category": "valves",
  "slots": {
    "valve_kind": "шаровый кран"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "valves": {
        "category": "valves",
        "scope": null,
        "slots": {
          "valve_kind": "шаровый кран"
        },
        "pending": {
          "question_id": "valves.application",
          "text": "Уточните для чего нужен кран: вода (холодная/горячая), отопление или радиатор и размер: 1/2, 3/4 или диаметр в мм.",
          "expected_slots": [
            "application",
            "size_inch",
            "diameter_mm",
            "connection_size"
          ],
          "attempts": 0,
          "category": "valves",
          "intent_type": "broad_category"
        }
      }
    },
    "category_last_goal": {
      "valves": "valves"
    },
    "active_goal": "valves",
    "active_category": "valves",
    "categories": {
      "valves": {
        "valve_kind": "шаровый кран"
      }
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [],
  "restored_product_skus": null,
  "agents_used": [
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": true,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": true,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

### Ход 2

Пользователь: 1/2, для воды

Время ответа: `7.31` сек.

Ответ бота:

```text
Нашёл подходящие варианты:
1. Кран шаровой MINI 1/2" вн.-нар.
   Артикул: VT.331.N.04
   Бренд: VALTEC
   Цена: 449 RUB
   Наличие: в наличии, 60 шт.
   Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини
   Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/
2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.
   Артикул: VT.217.N.04
   Бренд: VALTEC
   Цена: 452 RUB
   Наличие: в наличии, 76 шт.
   Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка
   Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/
3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2"
   Артикул: VT.217S.N.04
   Бренд: VALTEC
   Цена: 485 RUB
   Наличие: в наличии, 5 шт.
   Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка
   Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/
Могу сравнить эти варианты по главным отличиям для вашей задачи.
```

Товары:
- `VT.331.N.04` | Кран шаровой MINI 1/2" вн.-нар. | 449.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/
- `VT.217.N.04` | Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. | 452.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/
- `VT.217S.N.04` | Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" | 485.0 RUB | в наличии | https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/

Debug:

```json
{
  "intent": "attribute_request",
  "category": "valves",
  "slots": {
    "valve_kind": "шаровый кран",
    "size_inch": "1/2",
    "application": "вода"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "valves": {
        "category": "valves",
        "scope": null,
        "slots": {
          "valve_kind": "шаровый кран",
          "size_inch": "1/2",
          "application": "вода"
        },
        "pending": null
      }
    },
    "category_last_goal": {
      "valves": "valves"
    },
    "active_goal": "valves",
    "active_category": "valves",
    "categories": {
      "valves": {
        "valve_kind": "шаровый кран",
        "size_inch": "1/2",
        "application": "вода"
      }
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [
    "valves"
  ],
  "restored_product_skus": null,
  "agents_used": [
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "FeedSearchAgent",
    "RankingAgent",
    "ProductCardAgent",
    "GuardrailsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": true,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": true,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

### Ход 3

Пользователь: теперь нужен котёл на 100 метров

Время ответа: `7.21` сек.

Ответ бота:

```text
Понял, подбираем котёл примерно на 100 м². Газовый или электрический?
```

Debug:

```json
{
  "intent": "attribute_request",
  "category": "boilers",
  "slots": {
    "area_m2": 100.0
  },
  "project_context": {
    "version": 2,
    "goals": {
      "valves": {
        "category": "valves",
        "scope": null,
        "slots": {
          "valve_kind": "шаровый кран",
          "size_inch": "1/2",
          "application": "вода"
        },
        "pending": null
      },
      "boilers": {
        "category": "boilers",
        "scope": null,
        "slots": {
          "area_m2": 100.0
        },
        "pending": {
          "question_id": "boilers.boiler_type",
          "text": "Понял, подбираем котёл примерно на 100 м². Газовый или электрический?",
          "expected_slots": [
            "boiler_type"
          ],
          "attempts": 0,
          "category": "boilers",
          "intent_type": "attribute_request"
        }
      }
    },
    "category_last_goal": {
      "valves": "valves",
      "boilers": "boilers"
    },
    "active_goal": "boilers",
    "active_category": "boilers",
    "categories": {
      "valves": {
        "valve_kind": "шаровый кран",
        "size_inch": "1/2",
        "application": "вода"
      },
      "boilers": {
        "area_m2": 100.0
      }
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [
    "valves"
  ],
  "restored_product_skus": null,
  "agents_used": [
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": true,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": true,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": true,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

## 37. Смена темы с насоса на канализацию

Категория: `смена темы`. Приоритет: `P1`. Вердикт: **PASS**.

### Ход 1

Пользователь: нужен насос для отопления

Время ответа: `6.36` сек.

Ответ бота:

```text
Понял, нужен циркуляционный насос. Это замена старого или новый подбор? Для замены пришлите маркировку, присоединение и монтажную длину. Для нового подбора нужны расчётный расход (м³/ч), напор (м) и схема системы; монтажный размер всё равно нужно сверить.
```

Debug:

```json
{
  "intent": "attribute_request",
  "category": "pumps",
  "slots": {
    "pump_type": "циркуляционный",
    "pump_use": "отопление",
    "pump_selection_mode": "новый подбор"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "pumps:heating": {
        "category": "pumps",
        "scope": null,
        "slots": {
          "pump_type": "циркуляционный",
          "pump_use": "отопление",
          "pump_selection_mode": "новый подбор"
        },
        "pending": {
          "question_id": "pumps.mounting_length_mm",
          "text": "Понял, нужен циркуляционный насос. Это замена старого или новый подбор? Для замены пришлите маркировку, присоединение и монтажную длину. Для нового подбора нужны расчётный расход (м³/ч), напор (м) и схема системы; монтажный размер всё равно нужно сверить.",
          "expected_slots": [
            "mounting_length_mm",
            "head_m",
            "required_head_m",
            "required_flow_m3_h",
            "connection_size"
          ],
          "attempts": 0,
          "category": "pumps",
          "intent_type": "attribute_request"
        }
      }
    },
    "category_last_goal": {
      "pumps": "pumps:heating"
    },
    "active_goal": "pumps:heating",
    "active_category": "pumps",
    "categories": {
      "pumps": {
        "pump_type": "циркуляционный",
        "pump_use": "отопление",
        "pump_selection_mode": "новый подбор"
      }
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [],
  "restored_product_skus": null,
  "agents_used": [
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": true,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": true,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

### Ход 2

Пользователь: ладно, не насос. теперь нужна канализационная труба 50

Время ответа: `7.17` сек.

Ответ бота:

```text
Внутренняя или наружная канализация? И какая длина трубы нужна?
```

Debug:

```json
{
  "intent": "attribute_request",
  "category": "sewer",
  "slots": {
    "element_type": "труба",
    "diameter_mm": 50,
    "pipe_purpose": "канализация"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "pumps:heating": {
        "category": "pumps",
        "scope": null,
        "slots": {
          "pump_type": "циркуляционный",
          "pump_use": "отопление",
          "pump_selection_mode": "новый подбор"
        },
        "pending": {
          "question_id": "pumps.mounting_length_mm",
          "text": "Понял, нужен циркуляционный насос. Это замена старого или новый подбор? Для замены пришлите маркировку, присоединение и монтажную длину. Для нового подбора нужны расчётный расход (м³/ч), напор (м) и схема системы; монтажный размер всё равно нужно сверить.",
          "expected_slots": [
            "mounting_length_mm",
            "head_m",
            "required_head_m",
            "required_flow_m3_h",
            "connection_size"
          ],
          "attempts": 0,
          "category": "pumps",
          "intent_type": "attribute_request"
        }
      },
      "sewer": {
        "category": "sewer",
        "scope": null,
        "slots": {
          "element_type": "труба",
          "diameter_mm": 50
        },
        "pending": null
      }
    },
    "category_last_goal": {
      "pumps": "pumps:heating",
      "sewer": "sewer"
    },
    "active_goal": "sewer",
    "active_category": "sewer",
    "categories": {
      "pumps": {
        "pump_type": "циркуляционный",
        "pump_use": "отопление",
        "pump_selection_mode": "новый подбор"
      },
      "sewer": {
        "element_type": "труба",
        "diameter_mm": 50
      }
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [],
  "restored_product_skus": null,
  "agents_used": [
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": true,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": true,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": true,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

## 46. Ау после короткого сбоя

Категория: `другое`. Приоритет: `P1`. Вердикт: **PASS**.

### Ход 1

Пользователь: нужен насос

Время ответа: `6.8` сек.

Ответ бота:

```text
Для какой задачи нужен насос: отопление, водоснабжение/полив, повышение давления или откачка воды?
```

Debug:

```json
{
  "intent": "broad_category",
  "category": "pumps",
  "slots": {
    "product_kind": "насос"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "pumps": {
        "category": "pumps",
        "scope": null,
        "slots": {},
        "pending": {
          "question_id": "pumps.pump_use",
          "text": "Для какой задачи нужен насос: отопление, водоснабжение/полив, повышение давления или откачка воды?",
          "expected_slots": [
            "pump_use",
            "pump_type",
            "inlet_pressure_bar",
            "required_pressure_bar"
          ],
          "attempts": 0,
          "category": "pumps",
          "intent_type": "broad_category"
        }
      }
    },
    "category_last_goal": {
      "pumps": "pumps"
    },
    "active_goal": "pumps",
    "active_category": "pumps",
    "categories": {
      "pumps": {}
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [],
  "restored_product_skus": null,
  "agents_used": [
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": true,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": true,
  "engineering_llm_fallback_reason": null,
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```

### Ход 2

Пользователь: ау

Время ответа: `7.07` сек.

Ответ бота:

```text
Чтобы продолжить без догадок, нужен именно этот параметр. Для какой задачи нужен насос: отопление, водоснабжение/полив, повышение давления или откачка воды?
```

Debug:

```json
{
  "intent": "broad_category",
  "category": "pumps",
  "slots": {
    "product_kind": "насос"
  },
  "project_context": {
    "version": 2,
    "goals": {
      "pumps": {
        "category": "pumps",
        "scope": null,
        "slots": {},
        "pending": {
          "question_id": "pumps.pump_use",
          "text": "Чтобы продолжить без догадок, нужен именно этот параметр. Для какой задачи нужен насос: отопление, водоснабжение/полив, повышение давления или откачка воды?",
          "expected_slots": [
            "pump_use",
            "pump_type",
            "inlet_pressure_bar",
            "required_pressure_bar"
          ],
          "attempts": 1,
          "category": "pumps",
          "intent_type": "broad_category"
        }
      }
    },
    "category_last_goal": {
      "pumps": "pumps"
    },
    "active_goal": "pumps",
    "active_category": "pumps",
    "categories": {
      "pumps": {}
    },
    "known_facts": {},
    "shared_by_scope": {}
  },
  "product_branch_categories": [],
  "restored_product_skus": null,
  "agents_used": [
    "PendingAnswerResolver",
    "EngineeringInterpreterAgent",
    "IntentRouterAgent",
    "EngineeringRequirementsAgent",
    "ResponseComposerAgent"
  ],
  "llm_used": true,
  "llm_requested": true,
  "llm_transport_succeeded": true,
  "llm_output_accepted": false,
  "final_answer_source": "deterministic",
  "llm_rejection_reason": null,
  "intent_llm_used": false,
  "intent_llm_requested": false,
  "intent_llm_output_accepted": false,
  "intent_llm_rejection_reason": null,
  "engineering_llm_requested": true,
  "engineering_llm_used": true,
  "engineering_llm_output_accepted": false,
  "engineering_llm_fallback_reason": "engineering interpretation JSON was not accepted",
  "engineering_reply_used": false,
  "response_llm_used": false,
  "response_llm_requested": false,
  "response_llm_output_accepted": false,
  "response_llm_rejection_reason": null,
  "response_llm_fallback_reason": null,
  "consultant_llm_used": false,
  "consultant_llm_requested": false,
  "consultant_llm_output_accepted": false,
  "consultant_llm_rejection_reason": null,
  "consultant_llm_fallback_reason": null,
  "any_llm_used": true,
  "topic_changed": false,
  "handoff_status": "none",
  "handoff_ticket_id": null,
  "products_loaded_from": "file"
}
```
