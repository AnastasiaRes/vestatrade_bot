# Полный прогон виджет-бота: Legacy / Shadow / V2 Preview

Дата: 2026-08-30T18:35:10.874134+03:00
Маршрут: `http://127.0.0.1:8010/chat` (тот же контракт, что у widget-loader.js)
Сценариев: 10, ходов на режим: 31

## Конфигурация стенда

- health: `{"ok": true, "http_status": 200, "latency_sec": 0.014, "payload": {"status": "ok", "products_loaded": 100, "products_loaded_from": "file", "product_docs_loaded": 28, "llm_provider": "openrouter", "llm_configured": true, "llm_model": "qwen/qwen3-vl-8b-instruct", "llm_request_timeout_seconds": 180.0, "llm_attempt_timeout_seconds": 60.0, "llm_max_retries": 2}}`
- режимы: v2_preview
- QA-токен и ключ провайдера в отчёт не записываются.

## Сводка

| Режим | HTTP | С товарами | Цитаты паспорта | Сравнения | V2-владелец | Legacy-владелец | Semantic accepted | Embeddings ok/fail | avg / p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| v2_preview | 31/31 | 7 | 1 | 0 | 29 | 2 | 30 | 3/0 | 10.4s / 15.9s |

## Диагностика: v2_preview

- semantic statuses: `{"accepted": 30, "rejected": 1}`
- semantic repairs: `{"boiler_type_recovered_from_closed_alias": 1, "bounded_fact_followup_rebound_to_active_goal": 2, "connection_pattern_recovered_from_explicit_pair": 1, "constraint_categorical_ambiguity_added": 4, "constraint_closed_value_not_allowed_dropped": 1, "constraint_closed_value_not_grounded_dropped": 3, "constraint_evidence_rebound_to_current_message": 1, "constraint_non_known_fact_ambiguity_added": 1, "constraint_non_known_fact_unresolved_dropped": 1, "constraint_non_known_without_explicit_status_dropped": 3, "constraint_numeric_anchor_unit_recovered": 2, "constraint_numeric_value_not_in_evidence_dropped": 1, "constraint_shown_card_sku_unverified_dropped": 1, "constraint_ungrounded_evidence_dropped": 3, "explicit_show_selection_control_recovered": 6, "explicit_sku_constraint_recovered": 1, "explicit_sku_overrode_stale_goal": 1, "explicit_sku_product_scope_recovered": 1, "external_sewer_goal_recovered": 2, "external_sewer_scope_recovered": 1, "generic_show_anchor_forced_continue": 4, "generic_show_rebound_to_active_goal": 1, "shown_card_identifier_unmatched": 2, "spoken_numeric_anchor_recovered": 1, "typed_characteristic_question_explain_act_added": 1, "typed_characteristic_question_rebound_to_active_goal": 1, "typed_numeric_anchor_skipped_model_fact_present": 1, "untyped_ambiguous_selection_strategy_defaulted_to_standard": 7}`
- owners: `{"legacy": 2, "v2": 29}`
- candidate rejection codes: `{"answer_plan_missing": 1, "answer_plan_status_boundary_not_deliverable": 1, "dialogue_v2_skipped": 1, "grounding_not_accepted": 1, "rendered_answer_missing": 1}`
- parity: `{"unavailable": 2}`
- LLM completions: 68; cost: $0.124572; prompt/completion tokens: 916747/14684
- diagnostic LLM events: 136; embedding calls: 3; passport events: 15

# Стенограмма: v2_preview

## Новичок · канализация

_не знает терминов, описывает бытовыми словами_

**П:** Здравствуйте! У меня в частном доме воняет из туалета, наверное труба плохая

**Б:** Подскажите, пожалуйста, назначение канализации? Уточните, где проходит участок: внутри отапливаемого здания или снаружи, в грунте. Переход через стену или фундамент опишите как отдельный участок.
После ответа покажу варианты по вашему запросу.

<sub>{"eligible": true, "latency_sec": 8.092, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["external_sewer_goal_recovered"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Наверное надо менять. А какую брать, серую или рыжую?

**Б:** Подскажите, пожалуйста, назначение канализации? Уточните, где проходит участок: внутри отапливаемого здания или снаружи, в грунте. Переход через стену или фундамент опишите как отдельный участок.
После ответа покажу варианты по вашему запросу.

<sub>{"eligible": true, "latency_sec": 8.973, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["constraint_closed_value_not_allowed_dropped", "constraint_non_known_without_explicit_status_dropped", "constraint_categorical_ambiguity_added", "untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Мне на улицу, от дома до септика. Покажите что есть

**Б:** Показываю 2 предварительных варианта по подтверждённым данным: Назначение канализации: наружная.
Для точного подтверждения пригодности ещё нужно уточнить «диаметр», «длина». Карточки ниже — предварительные, не окончательная рекомендация.

<sub>товары: 220010 Труба канализационная наружная (3,2мм), KGEM 110*1000 — 585.0 RUB; 1491056 Труба наруж. ПВХ 110*1000 SN4 ХЕМКОР — 436.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 8.916, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["bounded_fact_followup_rebound_to_active_goal", "external_sewer_goal_recovered", "external_sewer_scope_recovered", "explicit_show_selection_control_recovered"], "semantic": "accepted", "v2_action": "show_preliminary_options"}</sub>

**П:** Сравните их, что лучше?

**Б:** Сравнение показанных вариантов:
1. Труба канализационная наружная (3,2мм), KGEM 110*1000 (220010)
2. Труба наруж. ПВХ 110*1000 SN4 ХЕМКОР (1491056)
• Цена: 1 — 585 ₽; 2 — 436 ₽.
• Наличие: 1 — в наличии; 2 — нет в наличии.
• Бренд: 1 — OSTENDORF; 2 — ХЕМКОР.
Какой критерий для вас решающий: цена, наличие?

<sub>{"eligible": true, "latency_sec": 6.597, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "compare"}</sub>

## Монтажник · трубы

_знает терминологию, формулирует точно_

**П:** Нужна ППР 25 армированная стекловолокном на радиаторную магистраль, подача 90 °С

**Б:** Диаметр присоединения: 25 мм.
Тип армирования: стекловолокно.
Назначение трубы: отопление.
Рабочая температура: 90 °C.
Подскажите, пожалуйста, рабочее давление? Укажите максимальное рабочее давление системы. Проверьте его в проекте, на настройке автоматики или по показанию исправного манометра. Ответ укажите в барах (бар).
После ответа покажу варианты по вашему запросу.

<sub>{"eligible": true, "latency_sec": 11.545, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["constraint_evidence_rebound_to_current_message"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Покажите варианты

**Б:** Показываю 1 предварительный вариант по подтверждённым данным: Диаметр: 25 мм; Тип армирования: стекловолокно; Назначение трубы: отопление.
Для точного подтверждения пригодности ещё нужно уточнить «рабочее давление». Карточки ниже — предварительные, не окончательная рекомендация.

<sub>товары: VTp.700.FB20.25 Труба PP-FIBER арм. стекл., PN 20, 25 MM (белый) — 168.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 15.861, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["generic_show_rebound_to_active_goal", "constraint_ungrounded_evidence_dropped", "explicit_show_selection_control_recovered", "generic_show_anchor_forced_continue"], "semantic": "accepted", "v2_action": "show_preliminary_options"}</sub>

**П:** Сравните по классу эксплуатации

**Б:** Для сравнения нужны минимум две реально показанные карточки. Покажите ещё один вариант или назовите второй товар.

<sub>{"eligible": true, "latency_sec": 8.236, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "compare"}</sub>

## Прораб · краны

_думает партиями и деньгами_

**П:** Нужны шаровые краны BASE 1/2 вн-вн, штук двадцать

**Б:** Подобрал 3 подходящих варианта. Карточки ниже расположены по соответствию запросу.

<sub>товары: VT.217.N.04 Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. — 452.0 RUB; VT.214.N.04 Кран шаровой BASE, стальная рукоятка 1/2" вн.-вн. — 503.0 RUB; VT.245.N.04 Кран шаровой BASE с дренажом и воздухоотводчиком 1/2" вн.-вн. — 753.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 12.227, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["constraint_ungrounded_evidence_dropped", "constraint_numeric_value_not_in_evidence_dropped", "connection_pattern_recovered_from_explicit_pair"], "semantic": "accepted", "v2_action": "recommend_one"}</sub>

**П:** Чем 214-я серия отличается от 217-й?

**Б:** Размер присоединения: 1/2.
Тип резьбового соединения: внутренняя/внутренняя резьба.
Продолжу, когда вы будете готовы сообщить недостающие данные.

<sub>{"eligible": true, "latency_sec": 8.715, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["shown_card_identifier_unmatched", "untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "wait_for_semantic_understanding"}</sub>

**П:** Сколько это выйдет за двадцать штук?

**Б:** Укажите, для какой из показанных карточек посчитать количество: первой, второй или по артикулу.

<sub>{"eligible": true, "latency_sec": 6.442, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": [], "semantic": "accepted", "v2_action": "calculate_preliminary"}</sub>

## Новичок · радиаторы

_не понимает разницы материалов_

**П:** Хочу батарею в комнату 18 квадратов, что посоветуете?

**Б:** Подскажите, пожалуйста, межосевое расстояние? Измерьте расстояние между осями верхнего и нижнего присоединений радиатора, а не полную высоту корпуса. Не ослабляйте пробки и соединения заполненной системы. Ответ укажите в миллиметрах (мм).
После ответа покажу варианты по вашему запросу.

<sub>{"eligible": true, "latency_sec": 9.142, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": [], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** А чем алюминиевый радиатор от биметаллического отличается?

**Б:** Подтверждённого значения параметра «уточняемая характеристика» в доступных проверенных источниках этого ответа нет. Я не буду подставлять значение или типичное значение без источника.

<sub>{"eligible": true, "latency_sec": 8.267, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": [], "semantic": "accepted", "v2_action": "answer_direct_question"}</sub>

**П:** Покажите что есть

**Б:** Подскажите для радиатора: прямое или угловое подключение; размер 1/2 или 3/4; регулировать температуру термоголовкой или просто перекрывать поток.

<sub>{"eligible": false, "latency_sec": 10.568, "owner": "legacy", "passport_events": 0, "rejections": ["answer_plan_status_boundary_not_deliverable"], "repairs": ["explicit_show_selection_control_recovered", "generic_show_anchor_forced_continue"], "semantic": "accepted", "v2_action": "state_capability_boundary"}</sub>

## Проектировщик · насосы

_оперирует рабочей точкой_

**П:** Циркуляционный насос: расчётный расход 1,5 м3/ч, напор 4 м, схема радиаторная

**Б:** Расход в рабочей точке: 1500 л/ч.
Напор в рабочей точке: 4 м.
Какой размер присоединения или трубы нужен? Его обычно указывают на старом насосе, трубе или в паспорте. Ответ укажите в миллиметрах (мм).
После ответа покажу варианты по вашему запросу.

<sub>{"eligible": true, "latency_sec": 11.492, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["constraint_closed_value_not_grounded_dropped", "constraint_categorical_ambiguity_added", "spoken_numeric_anchor_recovered", "constraint_numeric_anchor_unit_recovered"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Покажите варианты

**Б:** Показываю 5 предварительных вариантов по подтверждённым данным: Расход в рабочей точке: 1,5 м³/ч; Напор в рабочей точке: 4 м.
Для точного подтверждения пригодности ещё нужно уточнить «диаметр», «монтажная длина». Карточки ниже — предварительные, не окончательная рекомендация.

<sub>товары: 2459900 Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK — 10521.0 RUB; 53843 Насос циркуляц. (отопл.) UPС 25-40 180 — 3844.0 RUB; 9168934 Насос циркуляционный Wilo Star RS 25/6-130-RK — 7148.0 RUB; VRS.254.18.0 Насос циркуляционный VALTEC RS 25/4-180 с гайками — 3989.0 RUB; VRS.256.13.0 Насос циркуляционный VALTEC RS 25/6-130 с гайками — 4311.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 7.387, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["explicit_show_selection_control_recovered", "generic_show_anchor_forced_continue"], "semantic": "accepted", "v2_action": "show_preliminary_options"}</sub>

**П:** Сравните их между собой

**Б:** Сравнение показанных вариантов:
1. Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK (2459900)
2. Насос циркуляц. (отопл.) UPС 25-40 180 (53843)
3. Насос циркуляционный Wilo Star RS 25/6-130-RK (9168934)
4. Насос циркуляционный VALTEC RS 25/4-180 с гайками (VRS.254.18.0)
5. Насос циркуляционный VALTEC RS 25/6-130 с гайками (VRS.256.13.0)
• Цена: 1 — 10521 ₽; 2 — 3844 ₽; 3 — 7148 ₽; 4 — 3989 ₽; 5 — 4311 ₽.
• Бренд: 1 — Wilo; 2 — UNIPUMP; 3 — Wilo; 4 — VALTEC; 5 — VALTEC.
• Максимальный напор: 1 — 6 м; 2 — 4,5 м; 3 — 6 м; 4 — 4,2 м; 5 — 6 м.

<sub>{"eligible": true, "latency_sec": 12.095, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": [], "semantic": "accepted", "v2_action": "compare"}</sub>

**П:** Какая у первого монтажная длина?

**Б:** Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK (2459900). Монтажная длина — 130–180 мм. По паспорту: «Монтажная длина 130 мм / 180 мм». Источник: Циркуляционные_насосы_Wilo_Star_RS_с_мокрым_ротором.pdf, пункт 5.2.

<sub>товары: 2459900 Насос циркуляционный с монтажными адаптерами и гайками Wilo Star RS 25/6-130(180)-RK — 10521.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 11.394, "owner": "v2", "passport_events": 4, "rejections": [], "repairs": ["constraint_non_known_without_explicit_status_dropped"], "semantic": "accepted", "v2_action": "wait_for_semantic_understanding"}</sub>

## Сомневающийся · котлы

_переспрашивает и требует обоснований_

**П:** Нужен газовый котёл на дом 150 квадратов

**Б:** Площадь: 150 м².
Тип котла: газовый.
Котёл будет только отапливать дом или ещё готовить горячую воду? Если горячую воду обеспечивает отдельный водонагреватель, тоже напишите.
После ответа покажу варианты по вашему запросу.

<sub>{"eligible": true, "latency_sec": 9.772, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["boiler_type_recovered_from_closed_alias", "constraint_numeric_anchor_unit_recovered"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** А почему именно такая мощность?

**Б:** Для указанного товара паспорт может подтвердить характеристики самой модели, но не обоснование мощности для конкретного дома. Для такого вывода нужны расчётные теплопотери, климат, утепление и запас на ГВС. Поэтому не буду подменять расчёт посторонней характеристикой из паспорта.

<sub>{"eligible": true, "latency_sec": 7.125, "owner": "v2", "passport_events": 1, "rejections": [], "repairs": ["typed_characteristic_question_rebound_to_active_goal", "untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "explain_term_or_method"}</sub>

**П:** А вы уверены? Мне сосед говорил что надо больше

**Б:** Площадь: 150 м².
Тип котла: газовый.
Подбор выполнен по уже подтверждённым данным.

<sub>{"eligible": true, "latency_sec": 8.506, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["constraint_non_known_fact_unresolved_dropped", "constraint_non_known_fact_ambiguity_added"], "semantic": "accepted", "v2_action": "continue_with_confirmed_facts"}</sub>

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

<sub>{"eligible": false, "latency_sec": 11.886, "owner": "legacy", "passport_events": 0, "rejections": ["dialogue_v2_skipped", "answer_plan_missing", "rendered_answer_missing", "grounding_not_accepted"], "repairs": [], "semantic": "rejected", "v2_action": "wait_for_semantic_understanding"}</sub>

**П:** Какая резьба под термоголовку у этого клапана?

**Б:** Не могу однозначно определить, о каком товаре задан вопрос. Укажите артикул или номер ранее показанной карточки; без product scope искать ответ по всем паспортам небезопасно.

<sub>{"eligible": true, "latency_sec": 14.612, "owner": "v2", "passport_events": 1, "rejections": [], "repairs": [], "semantic": "accepted", "v2_action": "answer_direct_question"}</sub>

**П:** А головка VT.1500 подойдёт?

**Б:** Артикул VT.1500 однозначно распознан как VT.1500.0.0 — Термоголовка диап. регул-ки 6,5 - 28°C жидкостная. Сам факт наличия товара не доказывает совместимость: для ответа нужно отдельно подтвердить интерфейсы обоих изделий. Без этого совместимость обещать не буду.

<sub>товары: VT.1500.0.0 Термоголовка диап. регул-ки 6,5 - 28°C жидкостная — 1044.0 RUB</sub>

<sub>{"eligible": true, "latency_sec": 18.475, "owner": "v2", "passport_events": 1, "rejections": [], "repairs": ["explicit_sku_product_scope_recovered", "explicit_sku_constraint_recovered", "explicit_sku_overrode_stale_goal", "constraint_shown_card_sku_unverified_dropped", "shown_card_identifier_unmatched", "untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "wait_for_semantic_understanding"}</sub>

## Новичок · фитинги

_не знает как соединять_

**П:** Мне надо полипропиленовую трубу присоединить к железной, что купить?

**Б:** Для какого участка нужна труба: холодное или горячее водоснабжение, отопление или канализация? Для канализации нужен отдельный тип трубы.
После ответа покажу варианты по вашему запросу.

<sub>{"eligible": true, "latency_sec": 9.021, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["constraint_closed_value_not_grounded_dropped", "constraint_categorical_ambiguity_added"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Труба 25 миллиметров, резьба дюймовая

**Б:** Диаметр присоединения: 25 мм.
Для какого участка нужна труба: холодное или горячее водоснабжение, отопление или канализация? Для канализации нужен отдельный тип трубы.
После ответа покажу варианты по вашему запросу.

<sub>{"eligible": true, "latency_sec": 15.89, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["bounded_fact_followup_rebound_to_active_goal", "constraint_closed_value_not_grounded_dropped", "constraint_categorical_ambiguity_added", "typed_numeric_anchor_skipped_model_fact_present"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Покажите варианты

**Б:** Диаметр присоединения: 25 мм.
Параметр «назначение трубы»: как измерить или проверить. Укажите участок: холодное или горячее водоснабжение либо отопление; для канализации нужен отдельный тип трубы.

<sub>{"eligible": true, "latency_sec": 9.914, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["constraint_ungrounded_evidence_dropped", "explicit_show_selection_control_recovered", "generic_show_anchor_forced_continue"], "semantic": "accepted", "v2_action": "explain_how_to_find_fact"}</sub>

## Снабженец · котельная

_мыслит комплектом_

**П:** Собираю котельную на 200 квадратов, что нужно кроме котла?

**Б:** Площадь: 200 м².
Какой котёл вам нужен: газовый или электрический?
После ответа покажу варианты по вашему запросу.

<sub>{"eligible": true, "latency_sec": 11.42, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["typed_characteristic_question_explain_act_added"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

**П:** Покажите насос и краны для обвязки

**Б:** Подскажите, пожалуйста, расход в рабочей точке? Ответ укажите в литрах в час (л/ч).
После ответа покажу варианты по вашему запросу.

<sub>{"eligible": true, "latency_sec": 9.344, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["explicit_show_selection_control_recovered"], "semantic": "accepted", "v2_action": "ask_decision_changing_question"}</sub>

## Дотошный · трубы

_проверяет цифры и сравнивает_

**П:** Какая максимальная рабочая температура у трубы PP-FIBER PN 20?

**Б:** PP-FIBER PN 20. Максимальная рабочая температура — 90 °C. В карточке: «максимальная рабочая температура, °с: 90». Источник: feed_showcase_100_2026-06-14.xml, серия PP-FIBER PN 20, attributes_normalized.

<sub>{"eligible": true, "latency_sec": 12.672, "owner": "v2", "passport_events": 4, "rejections": [], "repairs": ["constraint_non_known_without_explicit_status_dropped"], "semantic": "accepted", "v2_action": "wait_for_semantic_understanding"}</sub>

**П:** А какое давление при радиаторном отоплении?

**Б:** PP-FIBER PN 20. Рабочее давление при радиаторном отоплении — 6 бар. В карточке: «рабочее давление, радиаторное отопление, бар: 6». Источник: feed_showcase_100_2026-06-14.xml, серия PP-FIBER PN 20, attributes_normalized.

<sub>{"eligible": true, "latency_sec": 9.98, "owner": "v2", "passport_events": 4, "rejections": [], "repairs": [], "semantic": "accepted", "v2_action": "answer_direct_question"}</sub>

**П:** Сравните её с PP-ALUX

**Б:** Для сравнения нужны минимум две реально показанные карточки. Покажите ещё один вариант или назовите второй товар.

<sub>{"eligible": true, "latency_sec": 7.833, "owner": "v2", "passport_events": 0, "rejections": [], "repairs": ["untyped_ambiguous_selection_strategy_defaulted_to_standard"], "semantic": "accepted", "v2_action": "compare"}</sub>
