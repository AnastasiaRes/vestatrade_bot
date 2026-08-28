# V2 paraphrase and fragmented-fact gate

Запуск: 2026-08-28T16:58:25.404931+03:00
Сценариев: 1; ходов: 4.
Полностью прошли: 1/1 (100.0%).
Проверок: 13; неуспешных: 0.
P50/P95 latency: 6.59/6.65 с.
Owners по всем ходам: `{'v2': 4}`.
Semantic status: `{'accepted': 4}`.

## Покрытие по семействам

| Семейство | Пройдено | Покрытие |
|---|---:|---:|
| sewer | 1/1 | 100.0% |

## Ordered SKU variants

- sewer: 1 — 220010, 1491056

## Частые semantic repairs

- `external_sewer_goal_recovered`: 3
- `external_sewer_scope_recovered`: 2
- `product_category_canonicalized_from_registry`: 1
- `product_evidence_rebound_to_current_message`: 1
- `constraint_closed_value_not_allowed_dropped`: 1
- `constraint_categorical_ambiguity_added`: 1
- `generic_show_rebound_to_active_goal`: 1
- `explicit_show_selection_control_recovered`: 1
- `generic_show_anchor_forced_continue`: 1

## Не прошедшие сценарии

- Нет.
## Методика

Gate использует настоящий `/chat`, защищённый `v2_preview`, реальную OpenRouter LLM, текущий feed100 и текущий паспортный/embedding-контур. Проверяется структура и телеметрия, а не точное совпадение текста.
