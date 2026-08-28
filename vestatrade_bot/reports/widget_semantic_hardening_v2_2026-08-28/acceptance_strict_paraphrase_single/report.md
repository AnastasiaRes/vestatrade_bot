# V2 paraphrase and fragmented-fact gate

Запуск: 2026-08-28T16:40:45.952927+03:00
Сценариев: 37; ходов: 88.
Полностью прошли: 36/37 (97.3%).
Проверок: 496; неуспешных: 1.
P50/P95 latency: 8.73/14.28 с.
Owners по всем ходам: `{'v2': 87}`.
Semantic status: `{'accepted': 87, 'rejected': 1}`.

## Покрытие по семействам

| Семейство | Пройдено | Покрытие |
|---|---:|---:|
| insufficient | 4/4 | 100.0% |
| named | 3/3 | 100.0% |
| ordinal | 8/8 | 100.0% |
| ppr | 6/6 | 100.0% |
| pump | 6/6 | 100.0% |
| sewer | 4/5 | 80.0% |
| valves | 5/5 | 100.0% |

## Ordered SKU variants

- insufficient: 1 — без карточек
- named: 1 — VRS.254.18.0
- ordinal: 1 — 2459900, 53843, 9168934, VRS.254.18.0, VRS.256.13.0
- ppr: 1 — VTp.700.FB20.25
- pump: 1 — 2459900, 53843, 9168934, VRS.254.18.0, VRS.256.13.0
- sewer: 2 — 112050, 112060, 115020, 115050, 220010; 220010, 1491056
- valves: 2 — VT.214.N.04, VT.217.N.04, VT.245.N.04; VT.217.N.04, VT.214.N.04, VT.245.N.04

## Частые semantic repairs

- `explicit_show_selection_control_recovered`: 31
- `generic_show_anchor_forced_continue`: 26
- `spoken_numeric_anchor_recovered`: 21
- `generic_show_rebound_to_active_goal`: 21
- `constraint_unit_ambiguity_added`: 7
- `untyped_ambiguous_selection_strategy_defaulted_to_standard`: 7
- `constraint_numeric_anchor_unit_recovered`: 7
- `external_sewer_goal_recovered`: 7
- `constraint_closed_value_not_grounded_dropped`: 6
- `constraint_categorical_ambiguity_added`: 6
- `constraint_incompatible_unit_dropped`: 5
- `external_sewer_scope_recovered`: 5
- `typed_characteristic_question_rebound_to_active_goal`: 4
- `constraint_non_known_without_explicit_status_dropped`: 4
- `product_evidence_rebound_to_current_message`: 3
- `constraint_numeric_value_not_in_evidence_dropped`: 3
- `constraint_invalid_product_binding_removed`: 3
- `typed_numeric_anchor_skipped_model_fact_present`: 2
- `constraint_numeric_unit_not_in_evidence_dropped`: 2
- `constraint_non_known_fact_unresolved_dropped`: 2

## Не прошедшие сценарии

### sewer_fragmented (sewer)

- failed: `['all_target_turns_owned_by_v2']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `shown` / `sewer_pipe` / `None`
- ordered SKU: `['220010', '1491056']`
- ответ: Область применения канализации: наружная.
«Труба канализационная наружная (3,2мм), KGEM 110*1000» (артикул 220010) — предварительный вариант: цена — 585 руб.; остаток по фиду — 30 (единица складского учёта в фиде не указана); наличие — в наличии; область применения канализации — наружная; ссылка — https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-naruzhnaya/kanalizaciya-naruzhnaya-ostendorf/truba-kanalizacionnaya-naruzhnaya-32mm-kgem-1101000/.
«Труба наруж. ПВХ 110*1000 SN4 ХЕМКОР» (артикул 1491056) — предварительный вариант: цена — 436 руб.; остаток по фиду — 0 (единица складского учёта в фиде не указана); наличие — нет в наличии; область примене

## Методика

Gate использует настоящий `/chat`, защищённый `v2_preview`, реальную OpenRouter LLM, текущий feed100 и текущий паспортный/embedding-контур. Проверяется структура и телеметрия, а не точное совпадение текста.
