# V2 paraphrase and fragmented-fact gate

Запуск: 2026-08-28T07:56:22.258204+03:00
Сценариев: 33; ходов: 76.
Полностью прошли: 9/33 (27.3%).
Проверок: 383; неуспешных: 96.
P50/P95 latency: 8.33/14.02 с.
Owners по всем ходам: `{'legacy': 9, 'v2': 67}`.
Semantic status: `{'accepted': 72, 'rejected': 4}`.

## Покрытие по семействам

| Семейство | Пройдено | Покрытие |
|---|---:|---:|
| insufficient | 4/4 | 100.0% |
| named | 0/3 | 0.0% |
| ordinal | 0/4 | 0.0% |
| ppr | 3/6 | 50.0% |
| pump | 0/6 | 0.0% |
| sewer | 2/5 | 40.0% |
| valves | 0/5 | 0.0% |

## Ordered SKU variants

- insufficient: 1 — без карточек
- named: 1 — без карточек
- ordinal: 1 — без карточек
- ppr: 4 — без карточек; RAL-1210-050006, RBM-0210-050006, RRS-2020-115140, RRS-2020-223100; VTp.700.0020.25, VTp.700.AL25.25, VTp.700.FB20.25; VTp.700.FB20.25
- pump: 2 — без карточек; 2459900, 53843, 9168934, VRS.254.18.0, VRS.256.13.0
- sewer: 3 — без карточек; 112050, 112060, 115020, 115050, 220010; 220010, 1491056
- valves: 2 — без карточек; VT.217.N.04, VT.214.N.04, VT.245.N.04

## Частые semantic repairs

- `untyped_ambiguous_selection_strategy_defaulted_to_standard`: 10
- `constraint_categorical_ambiguity_added`: 9
- `constraint_unit_ambiguity_added`: 8
- `constraint_closed_value_not_grounded_dropped`: 8
- `explicit_show_selection_control_recovered`: 8
- `selection_strategy_evidence_recovered_from_control`: 7
- `constraint_incompatible_unit_dropped`: 6
- `external_sewer_goal_recovered`: 6
- `constraint_numeric_value_not_in_evidence_dropped`: 4
- `constraint_non_known_without_explicit_status_dropped`: 4
- `constraint_numeric_anchor_value_canonicalized`: 3
- `external_sewer_scope_recovered`: 3
- `stale_typed_product_evidence_dropped`: 2
- `constraint_numeric_unit_not_in_evidence_dropped`: 2
- `constraint_numeric_anchor_name_canonicalized`: 2
- `constraint_closed_value_not_allowed_dropped`: 2
- `constraint_evidence_rebound_to_current_message`: 1
- `constraint_numeric_anchor_unit_recovered`: 1
- `constraint_typed_numeric_anchor_recovered`: 1
- `constraint_non_known_fact_unresolved_dropped`: 1

## Не прошедшие сценарии

### ppr_slang (ppr)

- failed: `['selection_owner_v2', 'selection_v2_primary', 'selection_outcome_gate', 'ppr_exact_card', 'ppr_kind', 'ppr_diameter', 'ppr_reinforcement', 'ppr_heating']`
- semantic: `accepted`
- owner: `legacy`
- selection status/kind/missing: `None` / `None` / `None`
- ordered SKU: `[]`
- ответ: Труба для чего: для холодной или горячей воды, для отопления или для канализации? Уточните участок системы и диаметр; если диаметр нужно рассчитать — расход и длину трассы.

### ppr_reordered (ppr)

- failed: `['ppr_exact_card', 'ppr_kind', 'ppr_diameter', 'ppr_reinforcement', 'ppr_heating']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `shown` / `radiator` / `None`
- ordered SKU: `['RAL-1210-050006', 'RBM-0210-050006', 'RRS-2020-115140', 'RRS-2020-223100']`
- ответ: «Радиатор алюминиевый Rommer Profi 500 х 80 6 секций» (артикул RAL-1210-050006) — предварительный вариант: цена — 3462 руб.; остаток по фиду — 0 (единица складского учёта в фиде не указана); наличие — нет в наличии; ссылка — https://www.vestatrade.ru/radiatory-otopleniya/radiatory-alyuminievye/radiatory-alyuminievye-rommer/radiator-alyuminievyy-rommer-profi-500-h-80-6-sekciy/.
«Радиатор биметаллический Rommer Optima BM 500 х 80 6 секций» (артикул RBM-0210-050006) — предварительный вариант: цена — 3050 руб.; остаток по фиду — 0 (единица складского учёта в фиде не указана); наличие — нет в наличии; ссылка — https://www.vestatrade.ru/radiatory-otopleniya/radiatory-bimetallicheskie/radiatory-bim

### ppr_short_fragments (ppr)

- failed: `['ppr_exact_card', 'ppr_reinforcement', 'ppr_heating']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `shown` / `pipe` / `None`
- ordered SKU: `['VTp.700.0020.25', 'VTp.700.AL25.25', 'VTp.700.FB20.25']`
- ответ: Диаметр присоединения: 25 мм.
«Труба PN 20, 25 MM (белый)» (артикул VTp.700.0020.25) — предварительный вариант: цена — 182 руб.; остаток по фиду — 189 (единица складского учёта в фиде не указана); наличие — в наличии; диаметр присоединения — 25 мм; ссылка — https://www.vestatrade.ru/truby/truby-polipropilenovye/truby-belye-valtec/truba-pn-20-25-mm-belyy/.
«Труба PP-ALUX, арм. алюминием, PN 25, 25 MM (белый)» (артикул VTp.700.AL25.25) — предварительный вариант: цена — 261 руб.; остаток по фиду — 888 (единица складского учёта в фиде не указана); наличие — в наличии; диаметр присоединения — 25 мм; ссылка — https://www.vestatrade.ru/truby/truby-polipropilenovye/truby-belye-valtec/truba-pp-alux-a

### pump_colloquial (pump)

- failed: `['pump_flow', 'pump_head']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `shown` / `circulation_pump` / `None`
- ordered SKU: `['2459900', '53843', '9168934', 'VRS.254.18.0', 'VRS.256.13.0']`
- ответ: Расход в рабочей точке: полтора куба в час m³/h.
Напор в рабочей точке: четыре метра напора м.
«Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK» (артикул 2459900) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 10521 руб.; остаток по фиду — 10 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-wilo/nasos-cirkulyacionnyj-s-montazhnymi-adapterami-i-gajkami-wilo-star-rs-25-6-130-180-rk/.
«Насос циркуляц. (отопл.) UPС 25-40 180» (артикул 53843) — предварительный вариант; по фиду не 

### pump_typos (pump)

- failed: `['selection_outcome_gate', 'pump_cards', 'pump_kind', 'pump_flow', 'pump_head']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `None` / `None` / `None`
- ordered SKU: `[]`
- ответ: Максимальный напор: 4 метра.
Параметр «диаметр присоединения»: как измерить или проверить. Сначала прочитайте размер на маркировке трубы или детали. У пластиковой трубы наружный диаметр можно измерить штангенциркулем; у металлической резьбы наружный замер не равен дюймовому условному размеру, поэтому нужна маркировка или паспорт. Не разъединяйте работающий трубопровод ради замера. Ответ укажите в миллиметрах (мм).

### pump_fragmented (pump)

- failed: `['pump_cards', 'pump_flow', 'pump_head']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `no_match` / `circulation_pump` / `None`
- ordered SKU: `[]`
- ответ: Максимальный напор: четыре метра.
Параметр «диаметр присоединения» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «монтажная длина» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
По подтверждённым требованиям в каталоге нет товара, у которого можно проверить совпадение всех обязательных параметров. Обязательные параметры я не ослаблял и неподтверждённый аналог не выдаю за подходящий.
Повторный поиск по тем же подтверждённым требованиям даст тот же результат. Чтобы продолжить, укажите, какое одно обязательное требование допустимо изменить; без вашего явного разрешения я не буду ослаблять у

### pump_reordered (pump)

- failed: `['selection_owner_v2', 'selection_v2_primary', 'selection_outcome_gate', 'pump_cards', 'pump_kind', 'pump_flow', 'pump_head']`
- semantic: `accepted`
- owner: `legacy`
- selection status/kind/missing: `None` / `None` / `None`
- ordered SKU: `[]`
- ответ: Для какой задачи нужен насос: отопление, водоснабжение/полив, повышение давления или откачка воды?

### pump_everyday (pump)

- failed: `['pump_flow']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `shown` / `circulation_pump` / `None`
- ordered SKU: `['2459900', '53843', '9168934', 'VRS.254.18.0', 'VRS.256.13.0']`
- ответ: Напор в рабочей точке: 4 м.
«Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK» (артикул 2459900) — предварительный вариант; по фиду не подтверждены «напор в рабочей точке»: цена — 10521 руб.; остаток по фиду — 10 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-wilo/nasos-cirkulyacionnyj-s-montazhnymi-adapterami-i-gajkami-wilo-star-rs-25-6-130-180-rk/.
«Насос циркуляц. (отопл.) UPС 25-40 180» (артикул 53843) — предварительный вариант; по фиду не подтверждены «напор в рабочей точке»: цена — 3844 руб.; остаток по фиду — 2 (единица складско

### pump_engineering_notation (pump)

- failed: `['selection_outcome_gate', 'pump_cards', 'pump_kind', 'pump_flow', 'pump_head']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `None` / `None` / `None`
- ordered SKU: `[]`
- ответ: Расход в рабочей точке: 1.5 м³/ч.
Напор в рабочей точке: 4 м.
Параметр «диаметр присоединения»: как измерить или проверить. Сначала прочитайте размер на маркировке трубы или детали. У пластиковой трубы наружный диаметр можно измерить штангенциркулем; у металлической резьбы наружный замер не равен дюймовому условному размеру, поэтому нужна маркировка или паспорт. Не разъединяйте работающий трубопровод ради замера. Ответ укажите в миллиметрах (мм).

### valve_plain (valves)

- failed: `['valve_cards', 'valve_kind', 'valve_connection_fact']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `no_match` / `ball_valve` / `None`
- ordered SKU: `[]`
- ответ: Размер присоединения: полдюйма.
Параметр «тип резьбового соединения» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
По подтверждённым требованиям в каталоге нет товара, у которого можно проверить совпадение всех обязательных параметров. Обязательные параметры я не ослаблял и неподтверждённый аналог не выдаю за подходящий.
Повторный поиск по тем же подтверждённым требованиям даст тот же результат. Чтобы продолжить, укажите, какое одно обязательное требование допустимо изменить; без вашего явного разрешения я не буду ослаблять условия совместимости.

### valve_vr (valves)

- failed: `['valve_kind']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `shown` / `ball_valve` / `None`
- ordered SKU: `['VT.217.N.04', 'VT.214.N.04', 'VT.245.N.04']`
- ответ: Размер присоединения: G1/2.
Тип резьбового соединения: внутренняя/внутренняя резьба.
Рекомендую «Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.» (артикул VT.217.N.04) — точное подтверждённое совпадение; среди точных кандидатов с подтверждённой ценой он выбран по минимальной цене: цена — 452 руб.; остаток по фиду — 57 (единица складского учёта в фиде не указана); наличие — в наличии; размер присоединения — 1/2; тип резьбового соединения — внутренняя/внутренняя резьба; ссылка — https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/.
«Кран шаровой BASE, стальная рукоятка 1/2" вн.-вн.» (артикул VT.214.N.04) — доп

### valve_typos (valves)

- failed: `['selection_outcome_gate', 'valve_cards', 'valve_kind', 'valve_connection_fact']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `None` / `None` / `None`
- ordered SKU: `[]`
- ответ: Размер присоединения: G1/2.
Параметр «тип резьбового соединения»: как измерить или проверить. Осмотрите оба конца детали отдельно: резьба внутри отверстия — ВР, снаружи патрубка — НР. Передайте последовательность от входа к выходу и сверьте маркировку; не разбирайте горячее или находящееся под давлением соединение.

### valve_fragmented (valves)

- failed: `['valve_cards', 'valve_kind', 'valve_connection_fact']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `no_match` / `ball_valve` / `None`
- ordered SKU: `[]`
- ответ: Размер присоединения: полдюйма.
Параметр «тип резьбового соединения» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
По подтверждённым требованиям в каталоге нет товара, у которого можно проверить совпадение всех обязательных параметров. Обязательные параметры я не ослаблял и неподтверждённый аналог не выдаю за подходящий.
Повторный поиск по тем же подтверждённым требованиям даст тот же результат. Чтобы продолжить, укажите, какое одно обязательное требование допустимо изменить; без вашего явного разрешения я не буду ослаблять условия совместимости.

### valve_dn (valves)

- failed: `['selection_outcome_gate', 'valve_cards', 'valve_kind', 'valve_connection_fact']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `None` / `None` / `None`
- ordered SKU: `[]`
- ответ: Диаметр присоединения: 15.
Параметр «назначение трубы»: как измерить или проверить. Укажите участок: холодное или горячее водоснабжение либо отопление; для канализации нужен отдельный тип трубы.

### sewer_slang (sewer)

- failed: `['selection_owner_v2', 'selection_v2_primary', 'selection_outcome_gate', 'sewer_cards', 'sewer_kind']`
- semantic: `rejected`
- owner: `legacy`
- selection status/kind/missing: `None` / `None` / `None`
- ordered SKU: `[]`
- ответ: Канализация внутренняя или наружная? И что нужно: труба, отвод, тройник или муфта?

### sewer_typos (sewer)

- failed: `['sewer_cards']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `need_clarification` / `sewer_pipe` / `diameter_mm`
- ordered SKU: `[]`
- ответ: Уточните, пожалуйста, параметр «диаметр присоединения» — он влияет на выбор. Сначала прочитайте размер на маркировке трубы или детали. У пластиковой трубы наружный диаметр можно измерить штангенциркулем; у металлической резьбы наружный замер не равен дюймовому условному размеру, поэтому нужна маркировка или паспорт. Не разъединяйте работающий трубопровод ради замера. Ответ укажите в миллиметрах (мм).
Для точного следующего шага достаточно одного уточнения.

### sewer_outlet (sewer)

- failed: `['selection_owner_v2', 'selection_v2_primary', 'selection_outcome_gate', 'sewer_cards', 'sewer_kind']`
- semantic: `accepted`
- owner: `legacy`
- selection status/kind/missing: `None` / `None` / `None`
- ordered SKU: `[]`
- ответ: Канализация внутренняя или наружная? И что нужно: труба, отвод, тройник или муфта?

### ordinal_first_plain (ordinal)

- failed: `['ordinal_cards', 'ordinal_reference_kind', 'ordinal_answered', 'ordinal_has_mm']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `no_match` / `circulation_pump` / `None`
- ordered SKU: `[]`
- ответ: Максимальный напор: 4 метра.
Параметр «диаметр присоединения» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «монтажная длина» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
По подтверждённым требованиям в каталоге нет товара, у которого можно проверить совпадение всех обязательных параметров. Обязательные параметры я не ослаблял и неподтверждённый аналог не выдаю за подходящий.
Повторный поиск по тем же подтверждённым требованиям даст тот же результат. Чтобы продолжить, укажите, какое одно обязательное требование допустимо изменить; без вашего явного разрешения я не буду ослаблять услови

### ordinal_first_mm (ordinal)

- failed: `['selection_outcome_gate', 'ordinal_cards', 'ordinal_reference_kind', 'ordinal_predicate', 'ordinal_answered', 'ordinal_has_mm']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `None` / `None` / `None`
- ordered SKU: `[]`
- ответ: Расход в рабочей точке: 1.5 м3/ч.
Максимальный напор: 4 м.
Параметр «диаметр присоединения»: как измерить или проверить. Сначала прочитайте размер на маркировке трубы или детали. У пластиковой трубы наружный диаметр можно измерить штангенциркулем; у металлической резьбы наружный замер не равен дюймовому условному размеру, поэтому нужна маркировка или паспорт. Не разъединяйте работающий трубопровод ради замера. Ответ укажите в миллиметрах (мм).

### ordinal_first_between (ordinal)

- failed: `['ordinal_cards', 'ordinal_reference_kind', 'ordinal_predicate', 'ordinal_answered', 'ordinal_has_mm']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `no_match` / `circulation_pump` / `None`
- ordered SKU: `[]`
- ответ: Максимальная подача: полтора куба m³/h.
Максимальный напор: четыре метра напора м.
Параметр «диаметр присоединения» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «монтажная длина» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
По подтверждённым требованиям в каталоге нет товара, у которого можно проверить совпадение всех обязательных параметров. Обязательные параметры я не ослаблял и неподтверждённый аналог не выдаю за подходящий.
Повторный поиск по тем же подтверждённым требованиям даст тот же результат. Чтобы продолжить, укажите, какое одно обязательное требование допустимо изменить; б

### ordinal_second (ordinal)

- failed: `['ordinal_cards', 'ordinal_reference_kind', 'ordinal_answered', 'ordinal_has_mm']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `no_match` / `circulation_pump` / `None`
- ordered SKU: `[]`
- ответ: Максимальный напор: 4 метра.
Параметр «диаметр присоединения» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «монтажная длина» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
По подтверждённым требованиям в каталоге нет товара, у которого можно проверить совпадение всех обязательных параметров. Обязательные параметры я не ослаблял и неподтверждённый аналог не выдаю за подходящий.
Повторный поиск по тем же подтверждённым требованиям даст тот же результат. Чтобы продолжить, укажите, какое одно обязательное требование допустимо изменить; без вашего явного разрешения я не буду ослаблять услови

### named_exact (named)

- failed: `['named_expected_sku', 'named_shown']`
- semantic: `accepted`
- owner: `v2`
- selection status/kind/missing: `no_match` / `circulation_pump` / `None`
- ordered SKU: `[]`
- ответ: Размер присоединения: G1/2.
Артикул: VRS.254.18.0.
По подтверждённым требованиям в каталоге нет товара, у которого можно проверить совпадение всех обязательных параметров. Обязательные параметры я не ослаблял и неподтверждённый аналог не выдаю за подходящий.
Повторный поиск по тем же подтверждённым требованиям даст тот же результат. Чтобы продолжить, укажите, какое одно обязательное требование допустимо изменить; без вашего явного разрешения я не буду ослаблять условия совместимости.

### named_partial_sku (named)

- failed: `['selection_owner_v2', 'selection_v2_primary', 'selection_outcome_gate', 'cards_equal_order', 'named_shown']`
- semantic: `rejected`
- owner: `legacy`
- selection status/kind/missing: `None` / `None` / `None`
- ordered SKU: `[]`
- ответ: Сокращение vrs.254 однозначно соответствует артикулу VRS.254.18.0 в текущем каталоге.

Нашёл подходящие варианты:
1. Насос циркуляционный VALTEC RS 25/4-180 с гайками
   Артикул: VRS.254.18.0
   Бренд: VALTEC
   Цена: 3989 RUB
   Наличие: в наличии, 13 шт.
   Характеристики: тип товара: Насос; тип насоса: Циркуляционный; максимальный напор, м: 4.2
   Ссылка: https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-valtec/nasos-cirkulyacionnyy-valtec-rs-254-180-s-gaykami/
Могу показать сопоставимые аналоги.

### explicit_sku_over_stale_goal (named)

- failed: `['selection_owner_v2', 'selection_v2_primary', 'selection_outcome_gate', 'cards_equal_order', 'named_shown']`
- semantic: `rejected`
- owner: `legacy`
- selection status/kind/missing: `None` / `None` / `None`
- ordered SKU: `[]`
- ответ: Нашёл подходящие варианты:
1. Насос циркуляционный VALTEC RS 25/4-180 с гайками
   Артикул: VRS.254.18.0
   Бренд: VALTEC
   Цена: 3989 RUB
   Наличие: в наличии, 13 шт.
   Характеристики: тип товара: Насос; тип насоса: Циркуляционный; максимальный напор, м: 4.2
   Ссылка: https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-valtec/nasos-cirkulyacionnyy-valtec-rs-254-180-s-gaykami/
Могу показать сопоставимые аналоги.

## Методика

Gate использует настоящий `/chat`, защищённый `v2_preview`, реальную OpenRouter LLM, текущий feed100 и текущий паспортный/embedding-контур. Проверяется структура и телеметрия, а не точное совпадение текста.
