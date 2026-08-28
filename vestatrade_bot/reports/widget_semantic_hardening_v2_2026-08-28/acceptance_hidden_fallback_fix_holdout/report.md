# V2 semantic holdout gate

Запуск: 2026-08-28T16:34:59.646764+03:00
Сценариев: 2; ходов: 7.
Полностью прошли: 1/2 (50.0%).
Проверок: 24; неуспешных: 1.
P50/P95 latency: 9.04/9.81 с.
Owners по всем ходам: `{'v2': 7}`.
Semantic status: `{'accepted': 7}`.

## Покрытие по семействам

| Семейство | Пройдено | Покрытие |
|---|---:|---:|
| sewer | 0/1 | 0.0% |
| valves | 1/1 | 100.0% |

## Ordered SKU variants

- sewer: 1 — без карточек
- valves: 1 — VT.217.N.04, VT.214.N.04, VT.245.N.04

## Частые semantic repairs

- `constraint_ungrounded_evidence_dropped`: 2
- `stale_typed_product_evidence_dropped`: 1
- `constraint_stale_product_binding_detached`: 1
- `selection_strategy_evidence_recovered_from_control`: 1
- `external_sewer_goal_recovered`: 1
- `external_sewer_scope_recovered`: 1
- `untyped_ambiguous_selection_strategy_defaulted_to_standard`: 1
- `generic_show_rebound_to_active_goal`: 1
- `explicit_show_selection_control_recovered`: 1
- `generic_show_anchor_forced_continue`: 1

## Не прошедшие сценарии

### holdout_sewer_everyday (sewer)

- failed: `['sewer_cards_or_necessary_clarification']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `no_match` / `sewer_pipe` / `None`
- ordered SKU: `[]`
- ответ: Область применения канализации: наружная.
Диаметр присоединения: сто десятый.
Параметр «длина» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
По подтверждённым требованиям в каталоге нет товара, у которого можно проверить совпадение всех обязательных параметров. Обязательные параметры я не ослаблял и неподтверждённый аналог не выдаю за подходящий.
Повторный поиск по тем же подтверждённым требованиям даст тот же результат. Чтобы продолжить, укажите, какое одно обязательное требование допустимо изменить; без вашего явного разрешения я не буду ослаблять условия совместимости.

## Методика

Gate использует настоящий `/chat`, защищённый `v2_preview`, реальную OpenRouter LLM, текущий feed100 и текущий паспортный/embedding-контур. Проверяется структура и телеметрия, а не точное совпадение текста.
