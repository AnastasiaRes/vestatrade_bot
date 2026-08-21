# VestaTrade — post-fix live OpenRouter evaluation

## Verdict

**NOT READY**

Проведено **77 диалогов / 219 пользовательских реплик** через штатный локальный HTTP API. Диалоги: PASS 62, FAIL 15 (80.52%). Реплики: PASS 199, FAIL 20 (90.87%).

Транспорт стабилен, автоматических галлюцинаций SKU/атрибутов не найдено, но релиз блокируют воспроизводимые retrieval/context/clarification ошибки.

## Environment

- Endpoint: `http://127.0.0.1:8011/chat`.
- Provider/model: `openrouter` / `qwen/qwen3-vl-8b-instruct`.
- Temperature: structured JSON 0.0; consultant 0.35, retry 0.2; response composer 0.2–0.5; top_p не задаётся.
- Timeout: 60 s на попытку, 180 s на user turn, до 2 retry.
- Каталог: локальный XML, raw offers **14182**, после sanitation backend **14035**, source=`file`.
- Credentials: `***REDACTED***`.
- Запросы к `vestatrade.ru` / `www.vestatrade.ru`: **0**; URL использовались только как строки из XML/API.

## Aggregate results

- Latency p50/p95/max: **4.8284 / 16.6162 / 19.2642 s**.
- End-to-end API errors/timeouts: **0**; API probes: `{'PASS': 7}`.
- Real LLM used: **170/219 turns**; final answer sources: `{'deterministic': 219}`.
- Structured LLM contract: `{'intent_requested': 48, 'intent_rejected_or_overridden': 11, 'engineering_requested': 165, 'engineering_rejected': 0, 'response_requested': 1, 'response_rejected': 1}`. Rejected outputs were handled by deterministic fallback/guards.
- Hallucination turns: **0 (0.0%)**.
- Цены и остатки считаются dynamic; совпадение с XML не подтверждает их актуальность на сайте.

### Automatic error frequency

- `BAD_CLARIFICATION`: **8**
- `WRONG_SKU`: **7**
- `CONTEXT_LOSS`: **4**
- `RETRIEVAL_WRONG_PRODUCT`: **4**
- `MISSED_CONSTRAINT`: **3**
- `WRONG_ATTRIBUTE`: **3**

### Problematic SKUs

- `VT.217.N.04`: **5** failed-turn appearances
- `VT.217.N.05`: **4** failed-turn appearances
- `VTp.781.0.04005`: **4** failed-turn appearances
- `VT.218.N.05`: **4** failed-turn appearances
- `VT.331.N.04`: **1** failed-turn appearances
- `VT.217S.N.04`: **1** failed-turn appearances
- `500043`: **1** failed-turn appearances
- `500045`: **1** failed-turn appearances
- `500047`: **1** failed-turn appearances

## Manual root-cause audit

- **P0 RETRIEVAL_WRONG_PRODUCT** — Follow-up 'такой же 3/4' returns VTp.781.0.04005, a PPR collector tee with an integrated valve, as if it were the same standalone ball valve. Reproducibility: 4/4 (smoke S10 plus core C-CTX-1..3).
- **P0 CONTEXT_LOSS** — 'Первый показанный' can resolve to VT.217.N.04 although the actual first card was VT.331.N.04. Reproducibility: 4 failures in 7 explicit first-shown runs.
- **P0 BAD_CLARIFICATION** — The radiator funnel re-asks regulate-vs-shutoff after a thermostatic valve was already requested; one typo dialogue also loses the earlier 1/2 size. Reproducibility: C07, C-COR-3 and C13.
- **P1 WRONG_SKU** — Explicit article typo 15100Z does not suggest the unique catalogue neighbor 151002, although the corrected exact SKU works on the next turn. Reproducibility: 4/4 (targeted 3 repeats plus core C03).
- **P1 BAD_CLARIFICATION** — The generic valve request shows mixed thread types before asking thread; the novice elbow request is routed to pipes and loops on application; a sewer pipe request shows several lengths without asking length. Reproducibility: smoke S01/S02 and core C08.

## Repeated runs

- PASS 3/3: ВР-ВР natural phrasing, FM terminology, ВР-ВР→ВР-НР correction, cheapest shown SKU, previous exact SKU, PPR 45° no-match, PPR 90° exact, similar-SKU comparison, analogs and generic incomplete requests.
- FAIL 3/3: explicit typo `15100Z` does not suggest `151002`.
- FAIL 3/3 in core: multi-turn valve context returns a wrong product class at 3/4 and later loses the actual first shown SKU.
- First-shown targeted repetitions alone: PASS 2/3, FAIL 1/3; combined with smoke/core: 4 failures in 7 runs.

## What is confirmed fixed

- No-match `PPR 20×1/2 НР 45°`: 3/3, no neighboring 90°/PEX/press SKU; confirmation keeps fittings context.
- Exact `PPR 20×1/2 НР 90°`: 3/3 only grounded matching cards.
- Explicit FF/FM constraints and thread correction: stable repeated PASS.
- One cheapest card selection: 3/3 targeted plus core comparison PASS after manual/evaluator audit.
- Exact previous SKU, analog source and similar-SKU comparison: stable repeated PASS.
- Session independence, malformed/empty request handling, 4xx/404 and lost-session fail-closed probes: 7/7 PASS.

## Priority fixes before the next live run

1. P0: enforce standalone `ball_valve` product identity; exclude fittings/collectors merely containing a valve from `такой же кран` follow-ups.
2. P0: resolve ordered historical emission snapshots before applying current handle/size slots; `первый показанный` must mean the first card of the first result set.
3. P0: make radiator slot requirements product-kind-aware; `thermostatic_valve` already implies regulation, and known size/form must survive follow-ups.
4. P1: wire explicit SKU typo suggestion to the actual built `SearchQuery`/slot path; keep confirmation-only behavior.
5. P1: clarify thread for a generic valve, route `уголок на трубу` as a fitting, and require pipe length before offering neighboring sewer SKUs.

## Full serious dialogues

### targeted:T-FIRST-SHOWN-R1 — Return to the actual first shown card

**USER:** Покажи кран 1/2 для воды

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой MINI 1/2" вн.-нар. Артикул: VT.331.N.04 Бренд: VALTEC Цена: 449 RUB Наличие: в наличии, 60 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/ 2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** А такой же 3/4?

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Тройник коллекторный PPR с шаровым краном, 40мм х 3/4" нар. (евроконус) Артикул: VTp.781.0.04005 Бренд: VALTEC Цена: 700 RUB Наличие: в наличии, 3 шт. Характеристики: диаметр (мм): 40; тип резьбы: Наружная; назначение: Водоснабжение, Отопление Ссылка: https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-belye-valtec/troynik-kollektornyy-ppr-s-sharovym-kranom-40mm-h-34-nar-evrokonus/ 3. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** А с бабочкой?

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4" Артикул: VT.218S.N.05 Бренд: VALTEC Цена: 821 RUB Наличие: в наличии, 22 шт. Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** Вернемся к первому показанному. Какой у него артикул?

**BOT:** Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.. Артикул: VT.217.N.04.

**Assessment:** `[{"code": "CONTEXT_LOSS", "reason": "Expected actual first card VT.331.N.04, got ['VT.217.N.04']", "severity": "FAIL"}]`

### targeted:T-SKU-TYPO-R1 — One-character SKU typo should suggest unique close SKU

**USER:** Найди артикул 15100Z

**BOT:** Не нашёл подходящие товары в текущем ассортименте. Могу уточнить параметры или передать вопрос менеджеру.

**Assessment:** `[{"code": "WRONG_SKU", "reason": "Unique one-character-neighbour SKU 151002 was not suggested", "severity": "FAIL"}]`

**USER:** Исправляю: точный артикул 151002

**BOT:** Нашёл подходящие варианты: 1. Водонагреватель THERMEX MK 50 V Артикул: 151002 Бренд: Thermex Цена: 13160 RUB Наличие: в наличии, 8 шт. Характеристики: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2 Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/ Перед покупкой сверьте способ монтажа, подвод воды, электропитание или источник нагрева с паспортом этой модели.

**USER:** Какая у него основная характеристика по карточке?

**BOT:** Водонагреватель THERMEX MK 50 V. Артикул: 151002. Основные данные: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2; макс. температура нагрева воды, °с: 75; присоединительный размер: G1/2; мощность, квт: 2; режимы мощности электрической, вт: 800/1200/2000; напор: в карточке не указано; расход/производительность: в карточке не указано; монтажная длина: в карточке не указано; материал нагревательного элемента: Медь; материал внутреннего бака: Нержавеющая сталь; макс. давление воды, мпа: 0.7; мин. давление воды, мпа: 0.05; количество контуров: в карточке не указано; камера сгорания: в карточке не указано. Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/

### smoke:S02 — Обычный покупатель: уголок на трубу 20

**USER:** Нужен уголок на пластиковую трубу 20

**BOT:** Понял, труба 20 мм. Для чего она: для холодной или горячей воды, для отопления или для канализации? Где именно она будет проложена?

**USER:** Труба PPR, нужен угол 90 градусов.

**BOT:** Уточню ещё раз, без этого не подберу точно. Понял, труба 20 мм. Для чего она: для холодной или горячей воды, для отопления или для канализации? Где именно она будет проложена?

**Assessment:** `[{"code": "BAD_CLARIFICATION", "reason": "Selection funnel asks an unrelated or already supplied parameter", "severity": "FAIL"}]`

**USER:** Резьбовой выход не нужен, обе стороны под сварку 20 мм.

**BOT:** Не буду подставлять случайный товар, пока этот параметр не известен. Его обычно можно посмотреть в паспорте изделия или на шильдике, либо замерить на месте. Если данных нет совсем — напишите «передай менеджеру», и я оформлю задачу с тем, что уже известно. Понял, труба 20 мм. Для чего она: для холодной или горячей воды, для отопления или для канализации? Где именно она будет проложена?

**Assessment:** `[{"code": "BAD_CLARIFICATION", "reason": "Selection funnel asks an unrelated or already supplied parameter", "severity": "FAIL"}]`

### core:C-COR-3 — Повтор исправления требования #3

**USER:** Нужен термостатический клапан прямой 1/2 для радиатора

**BOT:** Нашёл подходящие варианты: 1. Клапан термостатический для рад. прямой 1/2" (с доп. уплотнением) Артикул: VT.032.NR.04 Бренд: VALTEC Цена: 907 RUB Наличие: в наличии, 21 шт. Ссылка: https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-rad-pryamoy-12-s-dop-uplotneniem/ 2. Клапан термостатический для рад. прямой 1/2" Артикул: VT.032.N.04 Бренд: VALTEC Цена: 1673 RUB Наличие: в наличии, 1 шт. Ссылка: https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-rad-pryamoy-12/ 3. Клапан термостатический прямой Heizen 1/2" Артикул: TVD 566-15 Бренд: Heizen Цена: 739 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Клапан термостатический; область применения: Для систем отопления Ссылка: https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/armatura-dlya-radiatorov-heizen/klapan-termostaticheskij-prjamoj-heizen-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи. Кстати, если нужна регулировка температуры, к радиаторному клапану берут термоголовку — могу подобрать.

**USER:** Нет, перепутал: нужен угловой, а не прямой, остальные параметры те же.

**BOT:** Подскажите для радиатора: регулировать температуру (термоголовка) или просто перекрывать поток.

**Assessment:** `[{"code": "BAD_CLARIFICATION", "reason": "Selection funnel asks an unrelated or already supplied parameter", "severity": "FAIL"}]`

**USER:** Подтвердите текущую угловую форму.

**BOT:** Уточню ещё раз, без этого не подберу точно. Подскажите для радиатора: регулировать температуру (термоголовка) или просто перекрывать поток.

**Assessment:** `[{"code": "BAD_CLARIFICATION", "reason": "Selection funnel asks an unrelated or already supplied parameter", "severity": "FAIL"}]`

### core:C-CTX-1 — Повтор multi-turn context #1

**USER:** Покажи кран 1/2 для воды

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой MINI 1/2" вн.-нар. Артикул: VT.331.N.04 Бренд: VALTEC Цена: 449 RUB Наличие: в наличии, 60 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/ 2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** А такой же 3/4?

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Тройник коллекторный PPR с шаровым краном, 40мм х 3/4" нар. (евроконус) Артикул: VTp.781.0.04005 Бренд: VALTEC Цена: 700 RUB Наличие: в наличии, 3 шт. Характеристики: диаметр (мм): 40; тип резьбы: Наружная; назначение: Водоснабжение, Отопление Ссылка: https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-belye-valtec/troynik-kollektornyy-ppr-s-sharovym-kranom-40mm-h-34-nar-evrokonus/ 3. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**Assessment:** `[{"code": "RETRIEVAL_WRONG_PRODUCT", "reason": "Returned card violates active constraints: VTp.781.0.04005: product_kind=ball_valve", "severity": "FAIL"}]`

**USER:** А с бабочкой?

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4" Артикул: VT.218S.N.05 Бренд: VALTEC Цена: 821 RUB Наличие: в наличии, 22 шт. Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** Вернемся к первому показанному товару. Какой у него артикул?

**BOT:** Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.. Артикул: VT.217.N.04.

**Assessment:** `[{"code": "WRONG_SKU", "reason": "Expected SKU(s) not returned/named: ['VT.331.N.04']; got=['VT.217.N.04']", "severity": "FAIL"}, {"code": "CONTEXT_LOSS", "reason": "Contextual referent lost: ['VT.331.N.04']", "severity": "FAIL"}, {"code": "MISSED_CONSTRAINT", "reason": "Returned card violates active constraints: VT.217.N.04: size_inch=3/4", "severity": "FAIL"}, {"code": "WRONG_ATTRIBUTE", "reason": "Expected grounded value absent from answer: VT.331.N.04", "severity": "FAIL"}]`

### core:C08 — Канализация 50 против 110

**USER:** Нужна внутренняя канализационная труба 50 мм

**BOT:** Важно: у всех найденных позиций сейчас нет подтверждённого положительного остатка. Показываю их справочно; для покупки нужно подобрать доступный аналог или уточнить поставку у менеджера. Нашёл подходящие варианты: 1. Труба с раструбом 50 х 250 мм (СИНИКОН) Артикул: 500043 Бренд: СИНИКОН Цена: 114 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-250-mm-sinikon/ 2. Труба с раструбом 50 х 500 мм (СИНИКОН) Артикул: 500045 Бренд: СИНИКОН Цена: 144 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-500-mm-sinikon/ 3. Труба с раструбом 50 х 750 мм (СИНИКОН) Артикул: 500047 Бренд: СИНИКОН Цена: 183 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-750-mm-sinikon/ Могу сравнить эти варианты по главным отличиям для вашей задачи. Кстати, к канализационной трубе часто берут отводы и муфты того же диаметра. Если нужно, напишите, например, «отвод 50».

**Assessment:** `[{"code": "BAD_CLARIFICATION", "reason": "Incomplete first request produced products/no critical clarification", "severity": "FAIL"}]`

**USER:** Подождите, вы предложили товар до уточнения важных параметров. Длина 1000 мм.

**BOT:** Нашёл подходящие варианты: 1. Труба канализационная, HTEM, 50*1000"10 Артикул: 112040 Бренд: OSTENDORF Цена: 217 RUB Наличие: в наличии, 291 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-ostendorf/truba-kanalizacionnaya-htem-50100010/ 2. Труба с раструбом 50 х 1000 мм (СИНИКОН) Артикул: 500049 Бренд: СИНИКОН Цена: 228 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-1000-mm-sinikon/ 3. Труба PP д.50*1000 (Синикон) Комфорт Плюс Артикул: 500049.К Бренд: СИНИКОН Цена: 357 RUB Наличие: нет в наличии, 0 шт. Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-besshumnaya-sinikon/truba-pp-d501000-sinikon-komfort-plyus/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** Именно внутренняя, серая.

**BOT:** Нашёл подходящие варианты: 1. Труба канализационная, HTEM, 50*1000"10 Артикул: 112040 Бренд: OSTENDORF Цена: 217 RUB Наличие: в наличии, 291 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-ostendorf/truba-kanalizacionnaya-htem-50100010/ 2. Труба с раструбом 50 х 1000 мм (СИНИКОН) Артикул: 500049 Бренд: СИНИКОН Цена: 228 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-1000-mm-sinikon/ 3. Труба PP д.50*1000 (Синикон) Комфорт Плюс Артикул: 500049.К Бренд: СИНИКОН Цена: 357 RUB Наличие: нет в наличии, 0 шт. Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-besshumnaya-sinikon/truba-pp-d501000-sinikon-komfort-plyus/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

