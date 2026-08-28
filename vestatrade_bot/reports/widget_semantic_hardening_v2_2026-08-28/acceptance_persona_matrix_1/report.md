# Полный прогон виджет-бота: Legacy / Shadow / V2 Preview

Дата: 2026-08-28T15:59:31.117269+03:00
Маршрут: `http://127.0.0.1:8010/chat` (тот же контракт, что у widget-loader.js)
Сценариев: 10, ходов на режим: 31

## Конфигурация стенда

- health: `{"ok": true, "http_status": 200, "latency_sec": 0.015, "payload": {"status": "ok", "products_loaded": 100, "products_loaded_from": "file", "product_docs_loaded": 24, "llm_provider": "openrouter", "llm_configured": true, "llm_model": "qwen/qwen3-vl-8b-instruct", "llm_request_timeout_seconds": 180.0, "llm_attempt_timeout_seconds": 60.0, "llm_max_retries": 2}}`
- режимы: v2_preview
- QA-токен и ключ провайдера в отчёт не записываются.

## Сводка

| Режим | HTTP | С товарами | Цитаты паспорта | Сравнения | V2-владелец | Legacy-владелец | Semantic accepted | Embeddings ok/fail | avg / p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v2_preview | 31/31 | 12 | 1 | 1 | 27 | 4 | 28 | 4/0 | 12.0s / 23.7s |

## Диагностика: v2_preview

- semantic statuses: `{"accepted": 28, "rejected": 3}`
- semantic repairs: `{"constraint_categorical_ambiguity_added": 4, "constraint_closed_value_not_grounded_dropped": 4, "constraint_evidence_rebound_to_current_message": 1, "constraint_non_known_without_explicit_status_dropped": 1, "constraint_numeric_anchor_unit_recovered": 1, "constraint_shown_card_sku_unverified_dropped": 1, "explicit_show_selection_control_recovered": 6, "explicit_sku_constraint_recovered": 1, "explicit_sku_overrode_stale_goal": 1, "explicit_sku_product_scope_recovered": 1, "external_sewer_goal_recovered": 2, "external_sewer_scope_recovered": 1, "generic_show_anchor_forced_continue": 4, "generic_show_rebound_to_active_goal": 4, "selection_strategy_evidence_recovered_from_control": 1, "shown_card_identifier_unmatched": 1, "spoken_numeric_anchor_recovered": 1, "typed_characteristic_question_explain_act_added": 1, "typed_characteristic_question_rebound_to_active_goal": 1, "typed_numeric_anchor_skipped_model_fact_present": 1, "untyped_ambiguous_selection_strategy_defaulted_to_standard": 8}`
- owners: `{"legacy": 4, "v2": 27}`
- candidate rejection codes: `{"answer_plan_missing": 2, "answer_plan_status_unsupported_not_deliverable": 2, "dialogue_v2_skipped": 2, "grounding_not_accepted": 2, "not_all_answer_tasks_have_contracts": 1, "product_contract_resolution_missing": 1, "rendered_answer_missing": 2}`
- parity: `{"unavailable": 4}`
- LLM completions: 76; cost: $0.128459; prompt/completion tokens: 875107/15796
- diagnostic LLM events: 152; embedding calls: 4; passport events: 17

# Стенограмма: v2_preview

## Новичок · канализация

_не знает терминов, описывает бытовыми словами_

**П:** Здравствуйте! У меня в частном доме воняет из туалета, наверное труба плохая

**Б:** Уточните, пожалуйста, параметр «диаметр присоединения» — он влияет на выбор. Сначала прочитайте размер на маркировке трубы или детали. У пластиковой трубы наружный диаметр можно измерить штангенциркулем; у металлической резьбы наружный замер не равен дюймовому условному размеру, поэтому нужна маркировка или паспорт. Не разъединяйте работающий трубопровод ради замера. Ответ укажите в миллиметрах (мм).
Для точного следующего шага достаточно одного уточнения.

<sub>{"eligible": true, "latency_sec": 4.87, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["external_sewer_goal_recovered"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Наверное надо менять. А какую брать, серую или рыжую?

**Б:** Труба для чего: для холодной или горячей воды, для отопления или для канализации? Уточните участок системы и диаметр; если диаметр нужно рассчитать — расход и длину трассы.

<sub>{"eligible": false, "latency_sec": 12.34, "owner": "legacy", "passport_events": 0, "rejections": ["answer_plan_status_unsupported_not_deliverable", "product_contract_resolution_missing", "not_all_answer_tasks_have_contracts"], "repairs": ["untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "show_preliminary_options"}</sub>

**П:** Мне на улицу, от дома до септика. Покажите что есть

**Б:** Область применения канализации: наружная.
«Труба канализационная наружная (3,2мм), KGEM 110*1000» (артикул 220010) — предварительный вариант: цена — 585 руб.; остаток по фиду — 30 (единица складского учёта в фиде не указана); наличие — в наличии; область применения канализации — наружная; ссылка — https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-naruzhnaya/kanalizaciya-naruzhnaya-ostendorf/truba-kanalizacionnaya-naruzhnaya-32mm-kgem-1101000/.
«Труба наруж. ПВХ 110*1000 SN4 ХЕМКОР» (артикул 1491056) — предварительный вариант: цена — 436 руб.; остаток по фиду — 0 (единица складского учёта в фиде не указана); наличие — нет в наличии; область применения канализации — наружная; ссылка — https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-naruzhnaya/kanalizacija-naruzhnaja-hemkor/truba-naruzh-pvh-1101000-sn4-hemkor/.
Параметр «диаметр присоединения» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «длина» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Выше показаны предварительные варианты: перед покупкой проверьте отмеченные ограничения.

<sub>товары: 220010 Труба канализационная наружная (3,2мм), KGEM 110*1000 — 585.0 RUB; 1491056 Труба наруж. ПВХ 110*1000 SN4 ХЕМКОР — 436.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 7.391, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["external_sewer_goal_recovered", "external_sewer_scope_recovered", "explicit_show_selection_control_recovered"], "semantic": "accepted", "v2_action": "show_preliminary_options"}</sub>

**П:** Сравните их, что лучше?

**Б:** Область применения канализации: наружная.
Сравню подходящие варианты по параметрам, которые влияют на решение.

<sub>{"eligible": true, "latency_sec": 6.22, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": [], "semantic": "accepted", "v2_action": "compare"}</sub>

## Монтажник · трубы

_знает терминологию, формулирует точно_

**П:** Нужна ППР 25 армированная стекловолокном на радиаторную магистраль, подача 90 °С

**Б:** Диаметр присоединения: 25 мм.
Тип армирования: стекловолокно.
Назначение трубы: отопление.
Рабочая температура: 90 °C.
Уточните, пожалуйста, параметр «рабочее давление» — он влияет на выбор. Укажите максимальное рабочее давление системы. Проверьте его в проекте, на настройке автоматики или по показанию исправного манометра. Ответ укажите в барах (бар).
Для точного следующего шага достаточно одного уточнения.

<sub>{"eligible": true, "latency_sec": 19.26, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["constraint_evidence_rebound_to_current_message"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Покажите варианты

**Б:** Диаметр присоединения: 25 мм.
Тип армирования: стекловолокно.
Назначение трубы: отопление.
Рабочая температура: 90 °C.
«Труба PP-FIBER арм. стекл., PN 20, 25 MM (белый)» (артикул VTp.700.FB20.25) — предварительный вариант: цена — 168 руб.; остаток по фиду — 1043 (единица складского учёта в фиде не указана); наличие — в наличии; диаметр присоединения — 25 мм; рабочая температура — 90 °C; тип армирования — стекловолокно; назначение трубы — холодная и горячая вода, отопление; ссылка — https://www.vestatrade.ru/truby/truby-polipropilenovye/truby-belye-valtec/truba-pp-fiber-arm-stekl-pn-20-25-mm-belyy/.
Параметр «рабочее давление» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Выше показаны предварительные варианты: перед покупкой проверьте отмеченные ограничения.

<sub>товары: VTp.700.FB20.25 Труба PP-FIBER арм. стекл., PN 20, 25 MM (белый) — 168.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 6.342, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["generic_show_rebound_to_active_goal", "explicit_show_selection_control_recovered", "generic_show_anchor_forced_continue"], "semantic": "accepted", "v2_action": "show_preliminary_options"}</sub>

**П:** Сравните по классу эксплуатации

**Б:** Диаметр присоединения: 25 мм.
Тип армирования: стекловолокно.
Назначение трубы: отопление.
Рабочая температура: 90 °C.
Сравню подходящие варианты по параметрам, которые влияют на решение.

<sub>{"eligible": true, "latency_sec": 17.243, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "compare"}</sub>

## Прораб · краны

_думает партиями и деньгами_

**П:** Нужны шаровые краны BASE 1/2 вн-вн, штук двадцать

**Б:** Размер присоединения: 1/2.
Тип резьбового соединения: внутренняя/внутренняя резьба.
Уточняемая характеристика: 20 шт.
Рекомендую «Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.» (артикул VT.217.N.04) — точное подтверждённое совпадение; среди точных кандидатов с подтверждённой ценой он выбран по минимальной цене: цена — 452 руб.; остаток по фиду — 57 (единица складского учёта в фиде не указана); наличие — в наличии; размер присоединения — 1/2; тип резьбового соединения — внутренняя/внутренняя резьба; ссылка — https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/.
«Кран шаровой BASE, стальная рукоятка 1/2" вн.-вн.» (артикул VT.214.N.04) — дополнительный точный вариант: цена — 503 руб.; остаток по фиду — 2 (единица складского учёта в фиде не указана); наличие — в наличии; размер присоединения — 1/2; тип резьбового соединения — внутренняя/внутренняя резьба; ссылка — https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-stalnaya-rukoyatka-12-vn-vn/.
«Кран шаровой BASE с дренажом и воздухоотводчиком 1/2" вн.-вн.» (артикул VT.245.N.04) — дополнительный точный вариант: цена — 753 руб.; остаток по фиду — 0 (единица складского учёта в фиде не указана); наличие — нет в наличии; размер присоединения — 1/2; тип резьбового соединения — внутренняя/внутренняя резьба; ссылка — https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-s-drenazhom-i-vozduhootvodchikom-12-vn-vn/.
Выше выделен основной точный вариант; дополнительные карточки — точные альтернативы.

<sub>товары: VT.217.N.04 Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. — 452.0 RUB; VT.214.N.04 Кран шаровой BASE, стальная рукоятка 1/2" вн.-вн. — 503.0 RUB; VT.245.N.04 Кран шаровой BASE с дренажом и воздухоотводчиком 1/2" вн.-вн. — 753.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 23.692, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": [], "semantic": "accepted", "v2_action": "recommend_one"}</sub>

**П:** Чем 214-я серия отличается от 217-й?

**Б:** Сравниваю показанные варианты по карточкам товаров:
- VT.217.N.04 — Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.: цена 452 RUB; наличие: в наличии, 57 шт.
- VT.214.N.04 — Кран шаровой BASE, стальная рукоятка 1/2" вн.-вн.: цена 503 RUB; наличие: в наличии, 2 шт.
- VT.245.N.04 — Кран шаровой BASE с дренажом и воздухоотводчиком 1/2" вн.-вн.: цена 753 RUB; наличие: нет в наличии, 0 шт.
Главное отличие — цена: 452 RUB против 503 RUB против 753 RUB.
Если опишете вашу систему, порекомендую один вариант.

<sub>товары: VT.217.N.04 Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. — 452.0 RUB; VT.214.N.04 Кран шаровой BASE, стальная рукоятка 1/2" вн.-вн. — 503.0 RUB; VT.245.N.04 Кран шаровой BASE с дренажом и воздухоотводчиком 1/2" вн.-вн. — 753.0 RUB</sub>

<sub>{"eligible": false, "latency_sec": 14.743, "owner": "legacy", "passport_events": 2, "rejections": ["dialogue_v2_skipped", "answer_plan_missing", "rendered_answer_missing", "grounding_not_accepted"], "repairs": [], "semantic": "rejected", "v2_action": "wait_for_semantic_understanding"}</sub>

**П:** Сколько это выйдет за двадцать штук?

**Б:** Размер присоединения: 1/2.
Тип резьбового соединения: внутренняя/внутренняя резьба.
Продолжу, когда вы будете готовы сообщить недостающие данные.

<sub>{"eligible": true, "latency_sec": 7.849, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": [], "semantic": "accepted", "v2_action": "calculate_preliminary"}</sub>

## Новичок · радиаторы

_не понимает разницы материалов_

**П:** Хочу батарею в комнату 18 квадратов, что посоветуете?

**Б:** Уточните, пожалуйста, параметр «материал» — он влияет на выбор.
Для точного следующего шага достаточно одного уточнения.

<sub>{"eligible": true, "latency_sec": 17.706, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["constraint_closed_value_not_grounded_dropped", "constraint_categorical_ambiguity_added"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** А чем алюминиевый радиатор от биметаллического отличается?

**Б:** В подключённых проверенных источниках нет отдельного подтверждённого определения параметра «уточняемая характеристика». Я не буду подменять значение термина инструкцией по измерению или предположением.

<sub>{"eligible": true, "latency_sec": 6.463, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": [], "semantic": "accepted", "v2_action": "explain_term_or_method"}</sub>

**П:** Покажите что есть

**Б:** «Радиатор алюминиевый Rommer Profi 500 х 80 6 секций» (артикул RAL-1210-050006) — предварительный вариант: цена — 3462 руб.; остаток по фиду — 0 (единица складского учёта в фиде не указана); наличие — нет в наличии; ссылка — https://www.vestatrade.ru/radiatory-otopleniya/radiatory-alyuminievye/radiatory-alyuminievye-rommer/radiator-alyuminievyy-rommer-profi-500-h-80-6-sekciy/.
«Радиатор биметаллический Rommer Optima BM 500 х 80 6 секций» (артикул RBM-0210-050006) — предварительный вариант: цена — 3050 руб.; остаток по фиду — 0 (единица складского учёта в фиде не указана); наличие — нет в наличии; ссылка — https://www.vestatrade.ru/radiatory-otopleniya/radiatory-bimetallicheskie/radiatory-bimetallicheskie-rommer/radiator-bimetallicheskiy-rommer-optima-bm-500-h-80-6-sekciy/.
«Радиатор 11/500/1400 стальной панельный нижнее подключение Ventil ROMMER» (артикул RRS-2020-115140) — предварительный вариант: цена — 8782 руб.; остаток по фиду — 0 (единица складского учёта в фиде не указана); наличие — нет в наличии; ссылка — https://www.vestatrade.ru/radiatory-otopleniya/radiatory-stalnye-s-nizhnim/radiatory-rommer-ventil-nizhnee-podklyuchenie/rommer-ventil-tip-11/rommer-115001400-radiator-stalnoy-panelnyy-nizhnee-podklyuchenie-ventil/.
«Радиатор 22/300/1000 стальной панельный нижнее подключение Ventil ROMMER» (артикул RRS-2020-223100) — предварительный вариант: цена — 7941 руб.; остаток по фиду — 0 (единица складского учёта в фиде не указана); наличие — нет в наличии; ссылка — https://www.vestatrade.ru/radiatory-otopleniya/radiatory-stalnye-s-nizhnim/radiatory-rommer-ventil-nizhnee-podklyuchenie/rommer-ventil-tip-22/rommer-223001000-radiator-stalnoy-panelnyy-nizhnee-podklyuchenie-ventil/.
Параметр «материал» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «межосевое расстояние» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «размер присоединения» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Выше показаны предварительные варианты: перед покупкой проверьте отмеченные ограничения.

<sub>товары: RAL-1210-050006 Радиатор алюминиевый Rommer Profi 500 х 80 6 секций — 3462.0 RUB; RBM-0210-050006 Радиатор биметаллический Rommer Optima BM 500 х 80 6 секций — 3050.0 RUB; RRS-2020-115140 Радиатор 11/500/1400 стальной панельный нижнее подключение Ventil ROMMER — 8782.0 RUB; RRS-2020-223100 Радиатор 22/300/1000 стальной панельный нижнее подключение Ventil ROMMER — 7941.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 7.03, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["generic_show_rebound_to_active_goal", "explicit_show_selection_control_recovered", "generic_show_anchor_forced_continue"], "semantic": "accepted", "v2_action": "show_preliminary_options"}</sub>

## Проектировщик · насосы

_оперирует рабочей точкой_

**П:** Циркуляционный насос: расчётный расход 1,5 м3/ч, напор 4 м, схема радиаторная

**Б:** Расход в рабочей точке: 1500 л/ч.
Напор в рабочей точке: 4 м.
Уточните, пожалуйста, параметр «диаметр присоединения» — он влияет на выбор. Сначала прочитайте размер на маркировке трубы или детали. У пластиковой трубы наружный диаметр можно измерить штангенциркулем; у металлической резьбы наружный замер не равен дюймовому условному размеру, поэтому нужна маркировка или паспорт. Не разъединяйте работающий трубопровод ради замера. Ответ укажите в миллиметрах (мм).
Для точного следующего шага достаточно одного уточнения.

<sub>{"eligible": true, "latency_sec": 28.403, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["constraint_closed_value_not_grounded_dropped", "constraint_categorical_ambiguity_added", "spoken_numeric_anchor_recovered", "constraint_numeric_anchor_unit_recovered"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Покажите варианты

**Б:** Расход в рабочей точке: 1500 л/ч.
Напор в рабочей точке: 4 м.
«Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK» (артикул 2459900) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 10521 руб.; остаток по фиду — 10 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-wilo/nasos-cirkulyacionnyj-s-montazhnymi-adapterami-i-gajkami-wilo-star-rs-25-6-130-180-rk/.
«Насос циркуляц. (отопл.) UPС 25-40 180» (артикул 53843) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 3844 руб.; остаток по фиду — 2 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/cirkulyacionnye-nasosy-unipump/nasos-cirkulyac-otopl-ups-25-40-180/.
«Насос циркуляционный Wilo Star RS 25/6-130-RK» (артикул 9168934) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 7148 руб.; остаток по фиду — 9 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-wilo/nasos-cirkulyacionnyj-wilo-star-rs-25-6-130-rk/.
«Насос циркуляционный VALTEC RS 25/4-180 с гайками» (артикул VRS.254.18.0) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 3989 руб.; остаток по фиду — 13 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-valtec/nasos-cirkulyacionnyy-valtec-rs-254-180-s-gaykami/.
«Насос циркуляционный VALTEC RS 25/6-130 с гайками» (артикул VRS.256.13.0) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 4311 руб.; остаток по фиду — 14 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-valtec/nasos-cirkulyacionnyy-valtec-rs-256-130-s-gaykami/.
Параметр «диаметр присоединения» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «монтажная длина» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Выше показаны предварительные варианты: перед покупкой проверьте отмеченные ограничения.

<sub>товары: 2459900 Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK — 10521.0 RUB; 53843 Насос циркуляц. (отопл.) UPС 25-40 180 — 3844.0 RUB; 9168934 Насос циркуляционный Wilo Star RS 25/6-130-RK — 7148.0 RUB; VRS.254.18.0 Насос циркуляционный VALTEC RS 25/4-180 с гайками — 3989.0 RUB; VRS.256.13.0 Насос циркуляционный VALTEC RS 25/6-130 с гайками — 4311.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 6.8, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["generic_show_rebound_to_active_goal", "explicit_show_selection_control_recovered", "generic_show_anchor_forced_continue"], "semantic": "accepted", "v2_action": "show_preliminary_options"}</sub>

**П:** Сравните их между собой

**Б:** Расход в рабочей точке: 1500 л/ч.
Напор в рабочей точке: 4 м.
«Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK» (артикул 2459900) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 10521 руб.; остаток по фиду — 10 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-wilo/nasos-cirkulyacionnyj-s-montazhnymi-adapterami-i-gajkami-wilo-star-rs-25-6-130-180-rk/.
«Насос циркуляц. (отопл.) UPС 25-40 180» (артикул 53843) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 3844 руб.; остаток по фиду — 2 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/cirkulyacionnye-nasosy-unipump/nasos-cirkulyac-otopl-ups-25-40-180/.
«Насос циркуляционный Wilo Star RS 25/6-130-RK» (артикул 9168934) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 7148 руб.; остаток по фиду — 9 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-wilo/nasos-cirkulyacionnyj-wilo-star-rs-25-6-130-rk/.
«Насос циркуляционный VALTEC RS 25/4-180 с гайками» (артикул VRS.254.18.0) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 3989 руб.; остаток по фиду — 13 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-valtec/nasos-cirkulyacionnyy-valtec-rs-254-180-s-gaykami/.
«Насос циркуляционный VALTEC RS 25/6-130 с гайками» (артикул VRS.256.13.0) — предварительный вариант; по фиду не подтверждены «расход в рабочей точке», «напор в рабочей точке»: цена — 4311 руб.; остаток по фиду — 14 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-valtec/nasos-cirkulyacionnyy-valtec-rs-256-130-s-gaykami/.
Параметр «диаметр присоединения» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «монтажная длина» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Сравню подходящие варианты по параметрам, которые влияют на решение.

<sub>товары: 2459900 Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK — 10521.0 RUB; 53843 Насос циркуляц. (отопл.) UPС 25-40 180 — 3844.0 RUB; 9168934 Насос циркуляционный Wilo Star RS 25/6-130-RK — 7148.0 RUB; VRS.254.18.0 Насос циркуляционный VALTEC RS 25/4-180 с гайками — 3989.0 RUB; VRS.256.13.0 Насос циркуляционный VALTEC RS 25/6-130 с гайками — 4311.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 7.205, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["selection_strategy_evidence_recovered_from_control"], "semantic": "accepted", "v2_action": "compare"}</sub>

**П:** Какая у первого монтажная длина?

**Б:** Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK (2459900). Монтажная длина — 130–180 мм. По паспорту: «Монтажная длина 130 мм / 180 мм». Источник: Циркуляционные_насосы_Wilo_Star_RS_с_мокрым_ротором.pdf, пункт 5.2.

<sub>товары: 2459900 Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK — 10521.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 17.053, "owner": "v2", "passport_events": 4, "rejections": [], "repairs": ["typed_characteristic_question_rebound_to_active_goal", "typed_characteristic_question_explain_act_added", "constraint_non_known_without_explicit_status_dropped", "untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "wait_for_semantic_understanding"}</sub>

## Сомневающийся · котлы

_переспрашивает и требует обоснований_

**П:** Нужен газовый котёл на дом 150 квадратов

**Б:** Тип котла: газовый.
Уточните, пожалуйста, параметр «мощность» — он влияет на выбор. Возьмите расчёт теплопотерь или проектную мощность отопления. Если их нет, расчёт выполняют по ограждающим конструкциям, климату, вентиляции и режиму здания; одну площадь нельзя считать окончательным расчётом. Ответ укажите в киловаттах (кВт).
Для точного следующего шага достаточно одного уточнения.

<sub>{"eligible": true, "latency_sec": 17.702, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["constraint_closed_value_not_grounded_dropped", "constraint_categorical_ambiguity_added"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** А почему именно такая мощность?

**Б:** Для указанного товара паспорт может подтвердить характеристики самой модели, но не обоснование мощности для конкретного дома. Для такого вывода нужны расчётные теплопотери, климат, утепление и запас на ГВС. Поэтому не буду подменять расчёт посторонней характеристикой из паспорта.

<sub>{"eligible": true, "latency_sec": 7.654, "owner": "v2", "passport_events": 1, "rejections": [], "repairs": ["untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "explain_term_or_method"}</sub>

**П:** А вы уверены? Мне сосед говорил что надо больше

**Б:** Тип котла: газовый.
Подбор выполнен по уже подтверждённым данным.

<sub>{"eligible": true, "latency_sec": 9.873, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "continue_with_confirmed_facts"}</sub>

## Монтажник · радиаторная арматура

_проверяет совместимость_

**П:** Нужен термостатический клапан прямой 1/2 и головка к нему

**Б:** Нашёл подходящие варианты:
1. Клапан термостатический для рад. прямой 1/2"
   Артикул: VT.032.N.04
   Бренд: VALTEC
   Цена: 1673 RUB
   Наличие: в наличии, 1 шт.
   Характеристики: номинальный диаметр dn, мм: 15, 20; срок службы, лет: 15; номинальное давление, мпа: 1.0
   Ссылка: https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-rad-pryamoy-12/
2. Клапан с термостатической головкой для рад. прямой 1/2"
   Артикул: VT.048.N.04
   Бренд: VALTEC
   Цена: 1404 RUB
   Наличие: нет в наличии, 0 шт.
   Характеристики: номинальный диаметр dn, мм: 15; срок службы, лет: 15; номинальное давление, мпа: 1.0
   Ссылка: https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-s-termostaticheskoy-golovkoy-dlya-rad-pryamoy-12/
Могу сравнить эти варианты по главным отличиям для вашей задачи.

Кстати, если нужна регулировка температуры, к радиаторному клапану берут термоголовку — могу подобрать.

<sub>товары: VT.032.N.04 Клапан термостатический для рад. прямой 1/2" — 1673.0 RUB; VT.048.N.04 Клапан с термостатической головкой для рад. прямой 1/2" — 1404.0 RUB</sub>

<sub>{"eligible": false, "latency_sec": 11.774, "owner": "legacy", "passport_events": 0, "rejections": ["dialogue_v2_skipped", "answer_plan_missing", "rendered_answer_missing", "grounding_not_accepted"], "repairs": [], "semantic": "rejected", "v2_action": "wait_for_semantic_understanding"}</sub>

**П:** Какая резьба под термоголовку у этого клапана?

**Б:** Не могу однозначно определить, о каком товаре задан вопрос. Укажите артикул или номер ранее показанной карточки; без product scope искать ответ по всем паспортам небезопасно.

<sub>{"eligible": true, "latency_sec": 7.351, "owner": "v2", "passport_events": 1, "rejections": [], "repairs": ["untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "answer_direct_question"}</sub>

**П:** А головка VT.1500 подойдёт?

**Б:** Артикул VT.1500 однозначно распознан как VT.1500.0.0 — Термоголовка диап. регул-ки 6,5 - 28°C жидкостная. Сам факт наличия товара не доказывает совместимость: для ответа нужно отдельно подтвердить интерфейсы обоих изделий. Без этого совместимость обещать не буду.

<sub>товары: VT.1500.0.0 Термоголовка диап. регул-ки 6,5 - 28°C жидкостная — 1044.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 7.336, "owner": "v2", "passport_events": 1, "rejections": [], "repairs": ["explicit_sku_product_scope_recovered", "explicit_sku_constraint_recovered", "explicit_sku_overrode_stale_goal", "constraint_shown_card_sku_unverified_dropped", "shown_card_identifier_unmatched", "untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "wait_for_semantic_understanding"}</sub>

## Новичок · фитинги

_не знает как соединять_

**П:** Мне надо полипропиленовую трубу присоединить к железной, что купить?

**Б:** Уточните, пожалуйста, параметр «назначение трубы» — он влияет на выбор. Укажите участок: холодное или горячее водоснабжение либо отопление; для канализации нужен отдельный тип трубы.
Для точного следующего шага достаточно одного уточнения.

<sub>{"eligible": true, "latency_sec": 14.334, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["constraint_closed_value_not_grounded_dropped", "constraint_categorical_ambiguity_added"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Труба 25 миллиметров, резьба дюймовая

**Б:** Диаметр присоединения: 25 мм.
Уточните, пожалуйста, параметр «назначение трубы» — он влияет на выбор. Укажите участок: холодное или горячее водоснабжение либо отопление; для канализации нужен отдельный тип трубы.
Для точного следующего шага достаточно одного уточнения.

<sub>{"eligible": true, "latency_sec": 9.33, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["typed_numeric_anchor_skipped_model_fact_present"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Покажите варианты

**Б:** Диаметр присоединения: 25 мм.
«Труба PN 20, 25 MM (белый)» (артикул VTp.700.0020.25) — предварительный вариант: цена — 182 руб.; остаток по фиду — 189 (единица складского учёта в фиде не указана); наличие — в наличии; диаметр присоединения — 25 мм; ссылка — https://www.vestatrade.ru/truby/truby-polipropilenovye/truby-belye-valtec/truba-pn-20-25-mm-belyy/.
«Труба PP-ALUX, арм. алюминием, PN 25, 25 MM (белый)» (артикул VTp.700.AL25.25) — предварительный вариант: цена — 261 руб.; остаток по фиду — 888 (единица складского учёта в фиде не указана); наличие — в наличии; диаметр присоединения — 25 мм; ссылка — https://www.vestatrade.ru/truby/truby-polipropilenovye/truby-belye-valtec/truba-pp-alux-arm-alyuminiem-pn-25-25-mm-belyy/.
«Труба PP-FIBER арм. стекл., PN 20, 25 MM (белый)» (артикул VTp.700.FB20.25) — предварительный вариант: цена — 168 руб.; остаток по фиду — 1043 (единица складского учёта в фиде не указана); наличие — в наличии; диаметр присоединения — 25 мм; ссылка — https://www.vestatrade.ru/truby/truby-polipropilenovye/truby-belye-valtec/truba-pp-fiber-arm-stekl-pn-20-25-mm-belyy/.
Параметр «назначение трубы» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «рабочая температура» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «рабочее давление» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Выше показаны предварительные варианты: перед покупкой проверьте отмеченные ограничения.

<sub>товары: VTp.700.0020.25 Труба PN 20, 25 MM (белый) — 182.0 RUB; VTp.700.AL25.25 Труба PP-ALUX, арм. алюминием, PN 25, 25 MM (белый) — 261.0 RUB; VTp.700.FB20.25 Труба PP-FIBER арм. стекл., PN 20, 25 MM (белый) — 168.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 6.233, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["generic_show_rebound_to_active_goal", "explicit_show_selection_control_recovered", "generic_show_anchor_forced_continue"], "semantic": "accepted", "v2_action": "show_preliminary_options"}</sub>

## Снабженец · котельная

_мыслит комплектом_

**П:** Собираю котельную на 200 квадратов, что нужно кроме котла?

**Б:** Уточните, пожалуйста, параметр «тип котла» — он влияет на выбор. Укажите реально доступный источник энергии: подключённый газ, электроснабжение с известными напряжением и выделенной мощностью либо возможность безопасно использовать твёрдое топливо.
Для точного следующего шага достаточно одного уточнения.

<sub>{"eligible": true, "latency_sec": 7.069, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": [], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Покажите насос и краны для обвязки

**Б:** «Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK» (артикул 2459900) — предварительный вариант: цена — 10521 руб.; остаток по фиду — 10 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-wilo/nasos-cirkulyacionnyj-s-montazhnymi-adapterami-i-gajkami-wilo-star-rs-25-6-130-180-rk/.
«Насос циркуляц. (отопл.) UPС 25-40 180» (артикул 53843) — предварительный вариант: цена — 3844 руб.; остаток по фиду — 2 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/cirkulyacionnye-nasosy-unipump/nasos-cirkulyac-otopl-ups-25-40-180/.
«Насос циркуляционный Wilo Star RS 25/6-130-RK» (артикул 9168934) — предварительный вариант: цена — 7148 руб.; остаток по фиду — 9 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-wilo/nasos-cirkulyacionnyj-wilo-star-rs-25-6-130-rk/.
«Насос циркуляционный VALTEC RS 25/4-180 с гайками» (артикул VRS.254.18.0) — предварительный вариант: цена — 3989 руб.; остаток по фиду — 13 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-valtec/nasos-cirkulyacionnyy-valtec-rs-254-180-s-gaykami/.
«Насос циркуляционный VALTEC RS 25/6-130 с гайками» (артикул VRS.256.13.0) — предварительный вариант: цена — 4311 руб.; остаток по фиду — 14 (единица складского учёта в фиде не указана); наличие — в наличии; ссылка — https://www.vestatrade.ru/nasosnoe-oborudovanie/cirkulyacionnye-nasosy/nasosy-cirkulyacionnye-valtec/nasos-cirkulyacionnyy-valtec-rs-256-130-s-gaykami/.
Параметр «диаметр присоединения» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «максимальный напор» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Параметр «монтажная длина» пока не указан. Поэтому варианты предварительные: перед покупкой этот параметр нужно уточнить.
Уточните, пожалуйста, параметр «размер присоединения» — он влияет на выбор. Перепишите размер и стандарт присоединения с корпуса, шильдика или паспорта. Не переводите наружный диаметр резьбы в дюймовый размер без таблицы производителя.
Для точного следующего шага достаточно одного уточнения.

<sub>товары: 2459900 Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK — 10521.0 RUB; 53843 Насос циркуляц. (отопл.) UPС 25-40 180 — 3844.0 RUB; 9168934 Насос циркуляционный Wilo Star RS 25/6-130-RK — 7148.0 RUB; VRS.254.18.0 Насос циркуляционный VALTEC RS 25/4-180 с гайками — 3989.0 RUB; VRS.256.13.0 Насос циркуляционный VALTEC RS 25/6-130 с гайками — 4311.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 7.697, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["explicit_show_selection_control_recovered"], "semantic": "accepted", "v2_action": "show_preliminary_options"}</sub>

## Дотошный · трубы

_проверяет цифры и сравнивает_

**П:** Какая максимальная рабочая температура у трубы PP-FIBER PN 20?

**Б:** PP-FIBER PN 20. Максимальная рабочая температура — 90 °C. В карточке: «максимальная рабочая температура, °с: 90». Источник: feed_showcase_100_2026-06-14.xml, серия PP-FIBER PN 20, attributes_normalized.

<sub>{"eligible": true, "latency_sec": 23.711, "owner": "v2", "passport_events": 4, "rejections": [], "repairs": [], "semantic": "rejected", "v2_action": "wait_for_semantic_understanding"}</sub>

**П:** А какое давление при радиаторном отоплении?

**Б:** PP-FIBER PN 20. Рабочее давление при радиаторном отоплении — 6 бар. В карточке: «рабочее давление, радиаторное отопление, бар: 6». Источник: feed_showcase_100_2026-06-14.xml, серия PP-FIBER PN 20, attributes_normalized.

<sub>{"eligible": true, "latency_sec": 11.285, "owner": "v2", "passport_events": 4, "rejections": [], "repairs": [], "semantic": "accepted", "v2_action": "answer_direct_question"}</sub>

**П:** Сравните её с PP-ALUX

**Б:** Труба для чего: для холодной или горячей воды, для отопления или для канализации? Уточните участок системы и диаметр; если диаметр нужно рассчитать — расход и длину трассы.

<sub>{"eligible": false, "latency_sec": 21.168, "owner": "legacy", "passport_events": 0, "rejections": ["answer_plan_status_unsupported_not_deliverable"], "repairs": ["untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "compare"}</sub>
