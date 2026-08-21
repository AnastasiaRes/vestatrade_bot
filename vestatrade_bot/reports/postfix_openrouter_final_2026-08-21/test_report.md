# VestaTrade — post-fix OpenRouter dialogue evaluation

## Verdict

**NOT READY**

Проверено **77 диалогов / 219 пользовательских реплик**. Диалоги: PASS 46, FAIL 31 (pass rate 59.74%). Реплики: PASS 180, FAIL 39 (pass rate 82.19%).

Транспорт и базовый API стабильны, а новые hard constraints заметно улучшили PPR и резьбу. Релиз блокируют воспроизводимые ошибки ссылок на ранее показанный товар, смешение многопортовых размеров, выбор самого дешёвого и обработка опечатанного артикула.

## Environment

- Local API: `http://127.0.0.1:8765`; штатный `/chat` flow.
- OpenRouter model / strong model: `qwen/qwen3-vl-8b-instruct` / `qwen/qwen3-vl-8b-instruct`.
- Temperature: JSON agents 0.0; consultant 0.35, retry 0.2; response composer 0.2–0.5; `top_p` не задаётся.
- Timeouts: 60 s на попытку, 180 s на пользовательскую реплику, 2 retry.
- Local XML: **14035 offers**; `products_loaded_from=file`.
- Credentials: `***REDACTED***`.
- Запросов к `vestatrade.ru` / `www.vestatrade.ru`: **0**. URL из XML/API сохранялись только как строки.

## Aggregate results

- Latency p50/p95/max: **5.0107 / 10.4248 / 21.7809 s**.
- Technical/API errors: **0**; API probes: **7/7 PASS**.
- LLM used in routing/composition: **188/219 turns**.
- Final answers: `{'deterministic': 214, 'response_llm': 5}`. Most defects are deterministic, not caused by model prose.
- Automatic product hallucinations: **0**. Manual audit found **1** hallucinated/wrong terminology turn (**0.46%**): `FM` was once called flange-related.
- Dynamic prices/stock: `UNVERIFIED_DYNAMIC_DATA`; they were not counted as catalog truth errors.

### Error frequency

- `CONTEXT_LOSS`: **15**
- `WRONG_SKU`: **11**
- `BAD_CLARIFICATION`: **10**
- `MISSED_CONSTRAINT`: **7**
- `WRONG_ATTRIBUTE`: **2**

### Most problematic returned SKUs

- `100013619`: **8** failed-turn appearances
- `VT.217.N.05`: **6** failed-turn appearances
- `VT.331.N.04`: **3** failed-turn appearances
- `VT.392.N.05`: **3** failed-turn appearances
- `VT.256.N.04`: **3** failed-turn appearances
- `VT.217.N.04`: **1** failed-turn appearances
- `500043`: **1** failed-turn appearances
- `500045`: **1** failed-turn appearances
- `500047`: **1** failed-turn appearances

## Repeated-run results

- PASS 3/3: core similar-SKU, incomplete request, analog, correction; targeted FM terminology; exact PPR 20×1/2 НР 90°; exact FF→FM correction.
- Retrieval PASS 3/3 but dialogue FAIL 3/3: natural `внутренняя с обеих сторон` correctly returns FF cards, but `на воду` causes a redundant application question.
- First no-match answer PASS 3/3 but dialogue FAIL 3/3: PPR 45° correctly returns no exact item, then the confirmation turn switches from fittings to pipes.
- FAIL 3/3: mixed 3/4 follow-up, return to first shown, return to first explicit SKU, cheapest-one-SKU, one-character SKU typo.
- Intermittent terminology defect: `FM` flange error observed once in smoke, then not reproduced in 3 exact correction repeats.

## What the fixes improved

- `PPR 20×1/2 НР 45°`: no invented 90°/PEX/press product in all three first responses.
- `PPR 20×1/2 НР 90°`: only XML-grounded PPR male 20×1/2 90° cards in 3/3 runs.
- Explicit `ВР-ВР` and `ВР-НР`: selected cards comply in repeated runs.
- Requirement correction `ВР-ВР → ВР-НР`: 3/3 full dialogues PASS.
- API isolation, session independence, malformed JSON, 4xx/404 and lost-session probes: all PASS.

## Root-cause analysis and architecture

A full RAG/LLM rewrite is not justified. Keep the current pipeline, but strengthen three shared deterministic layers: normalized product facts, typed dialogue referents/outcomes, and state-aware dialogue acts. Prompt changes or a larger model will not fix the dominant defects: 214/219 final answers were deterministic.

1. **Port-aware product facts (P0).** `_inch_size_matches()` currently succeeds when a requested fraction occurs anywhere in the set of all product inches. Therefore 1/2×3/4 appliance valves pass a generic 3/4 constraint. At feed ingestion, build `connection_ports[]` with standard, size, gender and role plus a canonical `primary_size`. One shared fail-closed matcher must be used by retrieval, ranking and guardrails. A single-size query must reject mixed-port products unless the requested port is explicit.
2. **Referent ledger (P0).** `ProductBranchState` is the right direction, but recall still depends on narrow regexes and defaults to the latest snapshot. Store immutable ordered emission snapshots (`turn_id`, ordered SKUs, query constraints, relation) and resolve `первый показанный`, nounless `первому показанному`, `первый товар`, `предыдущий`, `исходный` before merging current slots. `last_products` must remain only a view.
3. **State-aware cheap/comparison act (P0).** `какой дешевле` over shown cards is a deterministic comparison, while `есть/покажи дешевле` is a new catalog search. Resolve the former before `cheap_request`; return exactly one min-price SKU. The current no-cheaper composer incorrectly lists all SKUs as one “last option”.
4. **Persist no-match outcome (P0).** Save `LastSearchOutcome(category=fittings, constraints, status=no_exact_match)`. A confirmation such as `то есть точного ... нет?` must answer from that state and must not open a new `pipes` goal without an explicit topic noun/change.
5. **Safe fuzzy SKU suggestion (P1).** Exact matching must remain exact. When the user explicitly says `артикул`, accept a broader SKU-like token, query a normalized SKU index, and suggest—not auto-select—a unique edit-distance-1 candidate such as `151002` for `15100Z`. Add confusable-character handling and ambiguity thresholds.
6. **Russian domain morphology (P1).** Centralize canonical lexemes (`вода/воды/воду → water`) before slot filling. Today the literal list recognizes `вода` and `воды`, but misses `воду`, producing redundant clarification.
7. **Grounded terminology renderer (P1).** Render `ff/fm/mm`, sizes and connection types from the ontology/template, not free-form LLM prose. If LLM paraphrasing is retained, run a semantic claim guard against the selected product facts.
8. **Evaluator contract (P1).** Score the actual response snapshot and port topology. On explicit recall, later constraints must not be applied to the restored historical card. Add checks for semantic code expansion (`FM`), one-of-N comparisons and no-match follow-ups.

## Priority acceptance gates

Before release, require all of the following in at least 3 repeated OpenRouter runs:

- 0 mixed-port products for a single 3/4 request;
- 3/3 correct for nounless and noun-qualified first/previous product references;
- 3/3 one exact cheapest SKU;
- 3/3 no-match confirmation stays in the same category/constraints;
- 3/3 unique one-character SKU typo suggests the correct candidate;
- no grounded terminology contradiction;
- existing PPR 45° fail-closed and PPR 90° exact suites remain 3/3.

## Manual evaluator audit

- Core `C-CTX-1..3`: the final `VT.331.N.04` recall was correct; the evaluator wrongly applied later 3/4/butterfly constraints. The dialogue still fails because turn 2 contains mixed 1/2×3/4 cards.
- Smoke `S08`: the automatic hallucination metric missed one false expansion of `FM` as flange-related.
- Targeted cheapest and PPR-no-match transcripts were rescored from saved HTTP data; no extra LLM calls were used for rescoring.

## Full representative serious dialogues

### smoke:S08 — Изменение ВР-ВР на ВР-НР

**USER:** Нужен шаровой кран 1/2 ВР-ВР для воды

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 2. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ 3. Кран шаровой MINI 1/2" вн.-вн. Артикул: VT.330.N.04 Бренд: VALTEC Цена: 502 RUB Наличие: в наличии, 3 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-vn/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** Нет, перепутал: нужен ВР-НР, остальные параметры те же.

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой MINI 1/2" вн.-нар. Артикул: VT.331.N.04 Бренд: VALTEC Цена: 449 RUB Наличие: в наличии, 60 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/ 2. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN15 PN40 1/2" Артикул: VT.218S.N.04 Бренд: VALTEC Цена: 508 RUB Наличие: в наличии, 195 шт. Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn15-pn40-12/ 3. Кран шаровой BASE, стальная рукоятка 1/2" вн.-нар. Артикул: VT.215.N.04 Бренд: VALTEC Цена: 555 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Рычаг Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-stalnaya-rukoyatka-12-vn-nar/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** Подтвердите текущую резьбу ВР-НР.

**BOT:** Да, у крана VT.331.N.04 резьба внутренняя — наружная (ВР-НР), как вы указали. Тип резьбы: FM (фланцевая внутренняя — наружная), подходит для соединения с трубами с наружной резьбой. Если нужно — могу предложить аналоги или подобрать по другому параметру. Хотите ли вы уточнить материал корпуса, давление или температуру рабочей среды?

**Assessment:** [{"code": "BAD_CLARIFICATION", "reason": "Selection funnel asks an unrelated or already supplied parameter", "severity": "FAIL"}]

### targeted:T-FIRST-SHOWN-R1 — Return to the actual first shown card

**USER:** Покажи кран 1/2 для воды

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой MINI 1/2" вн.-нар. Артикул: VT.331.N.04 Бренд: VALTEC Цена: 449 RUB Наличие: в наличии, 60 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/ 2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** А такой же 3/4?

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой угловой для подкл. с/т приборов 1/2"х3/4" Артикул: VT.392.N.05 Бренд: VALTEC Цена: 521 RUB Наличие: в наличии, 113 шт. Характеристики: тип товара: Кран шаровой угловой Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-uglovoy-dlya-podkl-st-priborov-12h34/ 2. Кран шаровой для подкл. с/т приборов 1/2"х3/4"х1/2" Артикул: VT.256.N.04 Бренд: VALTEC Цена: 577 RUB Наличие: в наличии, 33 шт. Характеристики: тип товара: Кран шаровой Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-dlya-podkl-st-priborov-12h34h12/ 3. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**Assessment:** [{"code": "MISSED_CONSTRAINT", "reason": "Cards violate single size 3/4: ['VT.392.N.05', 'VT.256.N.04']", "severity": "FAIL"}]

**USER:** А с бабочкой?

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4" Артикул: VT.218S.N.05 Бренд: VALTEC Цена: 821 RUB Наличие: в наличии, 22 шт. Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** Вернемся к первому показанному. Какой у него артикул?

**BOT:** Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн.. Артикул: VT.217.N.05.

**Assessment:** [{"code": "CONTEXT_LOSS", "reason": "Expected actual first card VT.331.N.04, got ['VT.217.N.05']", "severity": "FAIL"}]

### targeted:T-PREVIOUS-SKU-R1 — Return to first of two explicitly shown SKUs

**USER:** Покажи 100013611

**BOT:** Важно: у всех найденных позиций сейчас нет подтверждённого положительного остатка. Показываю их справочно; для покупки нужно подобрать доступный аналог или уточнить поставку у менеджера. Нашёл подходящие варианты: 1. Муфта комбинированная разъёмная ВР 25-1" (белый) РОСТерм Артикул: 100013611 Бренд: РОСТерм Цена: 386 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Американка; диаметр (мм): 25; присоединительная резьба, дюйм: 1 Ссылка: https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-dlya-polipropilenovyh-trub-rosterm/mufta-kombinirovannaja-razemnaja-vr-25-1-belyj-rosterm/ Могу показать сопоставимые аналоги. Фитинги должны совпадать с типом системы и обоими размерами перехода.

**USER:** Теперь покажи 100013619

**BOT:** Важно: у всех найденных позиций сейчас нет подтверждённого положительного остатка. Показываю их справочно; для покупки нужно подобрать доступный аналог или уточнить поставку у менеджера. Нашёл подходящие варианты: 1. Муфта комбинированная разъёмная ВР 40-11/4" (белый) РОСТерм Артикул: 100013619 Бренд: РОСТерм Цена: 648 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Американка; диаметр (мм): 40; присоединительная резьба, дюйм: 1 1/4 Ссылка: https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-dlya-polipropilenovyh-trub-rosterm/mufta-kombinirovannaja-razemnaja-vr-40-114-belyj-rosterm/ Могу показать сопоставимые аналоги.

**USER:** Вернемся к первому товару. Какой у него артикул?

**BOT:** Муфта комбинированная разъёмная ВР 40-11/4" (белый) РОСТерм. Артикул: 100013619.

**Assessment:** [{"code": "CONTEXT_LOSS", "reason": "Expected first explicit SKU 100013611 for article, got ['100013619']", "severity": "FAIL"}]

**USER:** А цена у первого какая?

**BOT:** По уже показанной подборке цены на фитинги: - Муфта комбинированная разъёмная ВР 40-11/4" (белый) РОСТерм, арт. 100013619: 648 RUB; нет в наличии. Монтаж, доставка и дополнительные комплектующие в эту цену не входят.

**Assessment:** [{"code": "CONTEXT_LOSS", "reason": "Expected first explicit SKU 100013611 for price, got ['100013619']", "severity": "FAIL"}]

### targeted:T-CHEAPEST-R1 — Cheapest product must be named by one exact SKU

**USER:** Покажи два шаровых крана 1/2 ВР-ВР для воды

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 2. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ 3. Кран шаровой MINI 1/2" вн.-вн. Артикул: VT.330.N.04 Бренд: VALTEC Цена: 502 RUB Наличие: в наличии, 3 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-vn/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** Чем отличаются эти варианты?

**BOT:** Сравниваю показанные варианты по карточкам товаров: - VT.217.N.04 — Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.: цена 452 RUB; наличие: в наличии, 76 шт.; диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка - VT.217S.N.04 — Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2": цена 485 RUB; наличие: в наличии, 5 шт.; диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка - VT.330.N.04 — Кран шаровой MINI 1/2" вн.-вн.: цена 502 RUB; наличие: в наличии, 3 шт.; диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Мини Главное отличие — тип ручки: Бабочка против Бабочка против Мини. Если опишете вашу систему, порекомендую один вариант.

**USER:** Какой дешевле? Назовите его один точный артикул.

**BOT:** Более дешёвых подходящих вариантов в текущем ассортименте не вижу. Последний подходящий вариант: VT.217.N.04, VT.217S.N.04, VT.330.N.04. Могу показать аналоги или передать вопрос менеджеру.

**Assessment:** [{"code": "WRONG_SKU", "reason": "Expected one cheapest SKU VT.217.N.04; named shown SKUs=['VT.217.N.04', 'VT.217S.N.04', 'VT.330.N.04']", "severity": "FAIL"}]

### targeted:T-PPR-45-NONE-R1 — Impossible PPR 20x1/2 male 45 degree must fail closed

**USER:** Нужен PPR угол 20×1/2 с наружной резьбой, но угол строго 45 градусов.

**BOT:** Не нашёл подходящие товары в текущем ассортименте. Могу уточнить параметры или передать вопрос менеджеру.

**USER:** То есть точного PPR 20×1/2 НР на 45° в каталоге нет?

**BOT:** Понял, труба 20 мм. Для чего она: для холодной или горячей воды, для отопления или для канализации? Где именно она будет проложена?

**Assessment:** [{"code": "CONTEXT_LOSS", "reason": "Follow-up did not preserve/confirm the established no-exact-match result", "severity": "FAIL"}]

### targeted:T-SKU-TYPO-R1 — One-character SKU typo should suggest unique close SKU

**USER:** Найди артикул 15100Z

**BOT:** Я консультант по товарам Vesta Trading. Могу помочь с трубами, насосами, котлами, кранами, канализацией и радиаторной арматурой. Напишите, что нужно подобрать.

**Assessment:** [{"code": "WRONG_SKU", "reason": "Unique one-character-neighbour SKU 151002 was not suggested", "severity": "FAIL"}]

**USER:** Исправляю: точный артикул 151002

**BOT:** Нашёл подходящие варианты: 1. Водонагреватель THERMEX MK 50 V Артикул: 151002 Бренд: Thermex Цена: 13160 RUB Наличие: в наличии, 8 шт. Характеристики: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2 Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/ Перед покупкой сверьте способ монтажа, подвод воды, электропитание или источник нагрева с паспортом этой модели.

**USER:** Какая у него основная характеристика по карточке?

**BOT:** Водонагреватель THERMEX MK 50 V. Артикул: 151002. Основные данные: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2. Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/

