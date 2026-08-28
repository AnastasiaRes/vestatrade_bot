# V2 semantic holdout gate

Запуск: 2026-08-28T15:35:56.255663+03:00
Сценариев: 20; ходов: 50.
Полностью прошли: 20/20 (100.0%).
Проверок: 238; неуспешных: 0.
P50/P95 latency: 8.83/15.25 с.
Owners по всем ходам: `{'legacy': 2, 'v2': 48}`.
Semantic status: `{'accepted': 48, 'rejected': 2}`.

## Покрытие по семействам

| Семейство | Пройдено | Покрытие |
|---|---:|---:|
| insufficient | 2/2 | 100.0% |
| named | 2/2 | 100.0% |
| ordinal | 3/3 | 100.0% |
| ppr | 3/3 | 100.0% |
| pump | 4/4 | 100.0% |
| sewer | 3/3 | 100.0% |
| valves | 3/3 | 100.0% |

## Ordered SKU variants

- insufficient: 1 — без карточек
- named: 1 — VRS.254.18.0
- ordinal: 1 — 2459900, 53843, 9168934, VRS.254.18.0, VRS.256.13.0
- ppr: 1 — VTp.700.FB20.25
- pump: 1 — 2459900, 53843, 9168934, VRS.254.18.0, VRS.256.13.0
- sewer: 3 — 112050, 112060, 115020, 115050, 220010; 115020, 115050, 220010, 115030, 1491056; 220010, 1491056
- valves: 2 — VT.214.N.04, VT.214.N.06, VT.214.N.07, VT.217.N.04, VT.217.N.05; VT.217.N.04, VT.214.N.04, VT.245.N.04

## Частые semantic repairs

- `explicit_show_selection_control_recovered`: 16
- `generic_show_anchor_forced_continue`: 15
- `generic_show_rebound_to_active_goal`: 11
- `spoken_numeric_anchor_recovered`: 11
- `constraint_numeric_anchor_unit_recovered`: 5
- `untyped_ambiguous_selection_strategy_defaulted_to_standard`: 4
- `availability_constraint_without_durable_requirement_dropped`: 3
- `constraint_numeric_value_not_in_evidence_dropped`: 3
- `external_sewer_goal_recovered`: 3
- `product_evidence_rebound_to_current_message`: 2
- `constraint_evidence_rebound_to_current_message`: 2
- `constraint_unit_ambiguity_added`: 2
- `external_sewer_scope_recovered`: 2
- `pending_numeric_answer_confirmed`: 2
- `typed_characteristic_question_rebound_to_active_goal`: 2
- `constraint_non_known_without_explicit_status_dropped`: 2
- `constraint_incompatible_unit_dropped`: 1
- `constraint_numeric_unit_not_in_evidence_dropped`: 1
- `pipe_service_recovered_from_radiator_main`: 1
- `inherited_untyped_product_proposal_dropped`: 1

## Не прошедшие сценарии

- Нет.
## Методика

Gate использует настоящий `/chat`, защищённый `v2_preview`, реальную OpenRouter LLM, текущий feed100 и текущий паспортный/embedding-контур. Проверяется структура и телеметрия, а не точное совпадение текста.
