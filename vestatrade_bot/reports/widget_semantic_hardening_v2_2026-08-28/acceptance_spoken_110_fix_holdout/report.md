# V2 semantic holdout gate

Запуск: 2026-08-28T16:37:40.362326+03:00
Сценариев: 1; ходов: 3.
Полностью прошли: 1/1 (100.0%).
Проверок: 11; неуспешных: 0.
P50/P95 latency: 8.79/8.79 с.
Owners по всем ходам: `{'v2': 3}`.
Semantic status: `{'accepted': 3}`.

## Покрытие по семействам

| Семейство | Пройдено | Покрытие |
|---|---:|---:|
| sewer | 1/1 | 100.0% |

## Ordered SKU variants

- sewer: 1 — 220010, 1491056

## Частые semantic repairs

- `constraint_closed_value_not_allowed_dropped`: 1
- `constraint_categorical_ambiguity_added`: 1
- `external_sewer_goal_recovered`: 1
- `external_sewer_scope_recovered`: 1
- `spoken_sewer_diameter_anchor_canonicalized`: 1
- `untyped_ambiguous_selection_strategy_defaulted_to_standard`: 1
- `generic_show_rebound_to_active_goal`: 1
- `explicit_show_selection_control_recovered`: 1
- `generic_show_anchor_forced_continue`: 1

## Не прошедшие сценарии

- Нет.
## Методика

Gate использует настоящий `/chat`, защищённый `v2_preview`, реальную OpenRouter LLM, текущий feed100 и текущий паспортный/embedding-контур. Проверяется структура и телеметрия, а не точное совпадение текста.
