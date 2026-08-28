# V2 paraphrase and fragmented-fact gate

Запуск: 2026-08-28T08:11:35.685253+03:00
Сценариев: 4; ходов: 12.
Полностью прошли: 2/4 (50.0%).
Проверок: 56; неуспешных: 10.
P50/P95 latency: 9.45/16.95 с.
Owners по всем ходам: `{'v2': 12}`.
Semantic status: `{'accepted': 12}`.

## Покрытие по семействам

| Семейство | Пройдено | Покрытие |
|---|---:|---:|
| ordinal | 2/4 | 50.0% |

## Ordered SKU variants

- ordinal: 1 — 2459900, 53843, 9168934, VRS.254.18.0, VRS.256.13.0

## Частые semantic repairs

- `constraint_non_known_fact_unresolved_dropped`: 4
- `constraint_non_known_fact_ambiguity_added`: 4
- `constraint_numeric_anchor_value_canonicalized`: 4
- `explicit_show_selection_control_recovered`: 4
- `untyped_ambiguous_selection_strategy_defaulted_to_standard`: 4
- `constraint_non_known_without_explicit_status_dropped`: 2
- `typed_characteristic_question_rebound_to_active_goal`: 1
- `constraint_ungrounded_evidence_dropped`: 1
- `typed_characteristic_question_explain_act_added`: 1
- `constraint_invalid_product_binding_removed`: 1

## Не прошедшие сценарии

### ordinal_isolated_first_mm (ordinal)

- failed: `['ordinal_reference_kind', 'ordinal_matches_visible_card', 'ordinal_predicate', 'ordinal_answered', 'ordinal_has_mm']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `shown` / `circulation_pump` / `None`
- ordered SKU: `['2459900', '53843', '9168934', 'VRS.254.18.0', 'VRS.256.13.0']`
- ответ: Расход в рабочей точке: 1.5 м3/ч.
Напор в рабочей точке: 4 м.
«Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK» (артикул 2459900) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 10521 руб.; остаток по фиду — 10 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-wilo/nasos-cirkulyacionnyj-s-montazhnymi-adapterami-i-gajkami-wilo-star-rs-25-6-130-180-rk/.
«Насос циркуляц. (отопл.) UPС 25-40 180» (артикул 53843) — предварительный вариант; по фиду не подтверждены «расход в рабочей то

### ordinal_isolated_first_between (ordinal)

- failed: `['ordinal_reference_kind', 'ordinal_matches_visible_card', 'ordinal_predicate', 'ordinal_answered', 'ordinal_has_mm']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `shown` / `circulation_pump` / `None`
- ordered SKU: `['2459900', '53843', '9168934', 'VRS.254.18.0', 'VRS.256.13.0']`
- ответ: Расход в рабочей точке: 1.5 м3/ч.
Напор в рабочей точке: 4 м.
«Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK» (артикул 2459900) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 10521 руб.; остаток по фиду — 10 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-wilo/nasos-cirkulyacionnyj-s-montazhnymi-adapterami-i-gajkami-wilo-star-rs-25-6-130-180-rk/.
«Насос циркуляц. (отопл.) UPС 25-40 180» (артикул 53843) — предварительный вариант; по фиду не подтверждены «расход в рабочей то

## Методика

Gate использует настоящий `/chat`, защищённый `v2_preview`, реальную OpenRouter LLM, текущий feed100 и текущий паспортный/embedding-контур. Проверяется структура и телеметрия, а не точное совпадение текста.
