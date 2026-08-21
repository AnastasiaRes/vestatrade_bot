# Targeted post-fix OpenRouter evaluation

- Run UTC: `2026-08-20T22:40:03.355719+00:00`
- Endpoint: `http://127.0.0.1:8011`
- Model: `qwen/qwen3-vl-8b-instruct`
- Catalog: `/Users/anastasiagorbunova/Documents/chat_bot/vestatrade_bot/data/products_all.xml` (14182 offers)
- Dialogues / user turns: **27 / 78**
- Dialogue status: `{'PASS': 20, 'FAIL': 7}`; pass rate **74.07%**
- Turn status: `{'PASS': 71, 'FAIL': 7}`; pass rate **91.03%**
- Latency p50/p95/max: **4.8863 / 16.8789 / 19.2642 s**
- Technical errors: **0**
- Turns with real LLM transport/use: **54**
- Final sources: `{'deterministic': 78}`

## Repeated runs

- `T-CHEAPEST`: `{'PASS': 3}`
- `T-CORRECTION-FM`: `{'PASS': 3}`
- `T-FF-NATURAL`: `{'PASS': 3}`
- `T-FIRST-SHOWN`: `{'FAIL': 1, 'PASS': 2}`
- `T-FM-TERM`: `{'PASS': 3}`
- `T-PPR-45-NONE`: `{'FAIL': 3}`
- `T-PPR-90-EXACT`: `{'PASS': 3}`
- `T-PREVIOUS-SKU`: `{'PASS': 3}`
- `T-SKU-TYPO`: `{'FAIL': 3}`

## Errors

- `CONTEXT_LOSS`: 4
- `WRONG_SKU`: 3

## Failed dialogues

### T-FIRST-SHOWN-R1: Return to the actual first shown card

**USER:** Покажи кран 1/2 для воды

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой MINI 1/2" вн.-нар. Артикул: VT.331.N.04 Бренд: VALTEC Цена: 449 RUB Наличие: в наличии, 60 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/ 2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** А такой же 3/4?

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Тройник коллекторный PPR с шаровым краном, 40мм х 3/4" нар. (евроконус) Артикул: VTp.781.0.04005 Бренд: VALTEC Цена: 700 RUB Наличие: в наличии, 3 шт. Характеристики: диаметр (мм): 40; тип резьбы: Наружная; назначение: Водоснабжение, Отопление Ссылка: https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-belye-valtec/troynik-kollektornyy-ppr-s-sharovym-kranom-40mm-h-34-nar-evrokonus/ 3. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** А с бабочкой?

**BOT:** Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4" Артикул: VT.218S.N.05 Бренд: VALTEC Цена: 821 RUB Наличие: в наличии, 22 шт. Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

**USER:** Вернемся к первому показанному. Какой у него артикул?

**BOT:** Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.. Артикул: VT.217.N.04.

Issues: `[{"code": "CONTEXT_LOSS", "reason": "Expected actual first card VT.331.N.04, got ['VT.217.N.04']", "severity": "FAIL"}]`

### T-PPR-45-NONE-R1: Impossible PPR 20x1/2 male 45 degree must fail closed

**USER:** Нужен PPR угол 20×1/2 с наружной резьбой, но угол строго 45 градусов.

**BOT:** Не нашёл подходящие товары в текущем ассортименте. Могу уточнить параметры или передать вопрос менеджеру.

**USER:** То есть точного PPR 20×1/2 НР на 45° в каталоге нет?

**BOT:** Да. По последнему поиску точного совпадения с этими параметрами в текущем каталоге не найдено.

Issues: `[{"code": "CONTEXT_LOSS", "reason": "Follow-up did not preserve/confirm the established no-exact-match result", "severity": "FAIL"}]`

### T-PPR-45-NONE-R2: Impossible PPR 20x1/2 male 45 degree must fail closed

**USER:** Нужен PPR угол 20×1/2 с наружной резьбой, но угол строго 45 градусов.

**BOT:** Не нашёл подходящие товары в текущем ассортименте. Могу уточнить параметры или передать вопрос менеджеру.

**USER:** То есть точного PPR 20×1/2 НР на 45° в каталоге нет?

**BOT:** Да. По последнему поиску точного совпадения с этими параметрами в текущем каталоге не найдено.

Issues: `[{"code": "CONTEXT_LOSS", "reason": "Follow-up did not preserve/confirm the established no-exact-match result", "severity": "FAIL"}]`

### T-PPR-45-NONE-R3: Impossible PPR 20x1/2 male 45 degree must fail closed

**USER:** Нужен PPR угол 20×1/2 с наружной резьбой, но угол строго 45 градусов.

**BOT:** Не нашёл подходящие товары в текущем ассортименте. Могу уточнить параметры или передать вопрос менеджеру.

**USER:** То есть точного PPR 20×1/2 НР на 45° в каталоге нет?

**BOT:** Да. По последнему поиску точного совпадения с этими параметрами в текущем каталоге не найдено.

Issues: `[{"code": "CONTEXT_LOSS", "reason": "Follow-up did not preserve/confirm the established no-exact-match result", "severity": "FAIL"}]`

### T-SKU-TYPO-R1: One-character SKU typo should suggest unique close SKU

**USER:** Найди артикул 15100Z

**BOT:** Не нашёл подходящие товары в текущем ассортименте. Могу уточнить параметры или передать вопрос менеджеру.

Issues: `[{"code": "WRONG_SKU", "reason": "Unique one-character-neighbour SKU 151002 was not suggested", "severity": "FAIL"}]`

**USER:** Исправляю: точный артикул 151002

**BOT:** Нашёл подходящие варианты: 1. Водонагреватель THERMEX MK 50 V Артикул: 151002 Бренд: Thermex Цена: 13160 RUB Наличие: в наличии, 8 шт. Характеристики: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2 Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/ Перед покупкой сверьте способ монтажа, подвод воды, электропитание или источник нагрева с паспортом этой модели.

**USER:** Какая у него основная характеристика по карточке?

**BOT:** Водонагреватель THERMEX MK 50 V. Артикул: 151002. Основные данные: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2; макс. температура нагрева воды, °с: 75; присоединительный размер: G1/2; мощность, квт: 2; режимы мощности электрической, вт: 800/1200/2000; напор: в карточке не указано; расход/производительность: в карточке не указано; монтажная длина: в карточке не указано; материал нагревательного элемента: Медь; материал внутреннего бака: Нержавеющая сталь; макс. давление воды, мпа: 0.7; мин. давление воды, мпа: 0.05; количество контуров: в карточке не указано; камера сгорания: в карточке не указано. Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/

### T-SKU-TYPO-R2: One-character SKU typo should suggest unique close SKU

**USER:** Найди артикул 15100Z

**BOT:** Не нашёл подходящие товары в текущем ассортименте. Могу уточнить параметры или передать вопрос менеджеру.

Issues: `[{"code": "WRONG_SKU", "reason": "Unique one-character-neighbour SKU 151002 was not suggested", "severity": "FAIL"}]`

**USER:** Исправляю: точный артикул 151002

**BOT:** Нашёл подходящие варианты: 1. Водонагреватель THERMEX MK 50 V Артикул: 151002 Бренд: Thermex Цена: 13160 RUB Наличие: в наличии, 8 шт. Характеристики: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2 Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/ Перед покупкой сверьте способ монтажа, подвод воды, электропитание или источник нагрева с паспортом этой модели.

**USER:** Какая у него основная характеристика по карточке?

**BOT:** Водонагреватель THERMEX MK 50 V. Артикул: 151002. Основные данные: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2; макс. температура нагрева воды, °с: 75; присоединительный размер: G1/2; мощность, квт: 2; режимы мощности электрической, вт: 800/1200/2000; напор: в карточке не указано; расход/производительность: в карточке не указано; монтажная длина: в карточке не указано; материал нагревательного элемента: Медь; материал внутреннего бака: Нержавеющая сталь; макс. давление воды, мпа: 0.7; мин. давление воды, мпа: 0.05; количество контуров: в карточке не указано; камера сгорания: в карточке не указано. Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/

### T-SKU-TYPO-R3: One-character SKU typo should suggest unique close SKU

**USER:** Найди артикул 15100Z

**BOT:** Не нашёл подходящие товары в текущем ассортименте. Могу уточнить параметры или передать вопрос менеджеру.

Issues: `[{"code": "WRONG_SKU", "reason": "Unique one-character-neighbour SKU 151002 was not suggested", "severity": "FAIL"}]`

**USER:** Исправляю: точный артикул 151002

**BOT:** Нашёл подходящие варианты: 1. Водонагреватель THERMEX MK 50 V Артикул: 151002 Бренд: Thermex Цена: 13160 RUB Наличие: в наличии, 8 шт. Характеристики: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2 Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/ Перед покупкой сверьте способ монтажа, подвод воды, электропитание или источник нагрева с паспортом этой модели.

**USER:** Какая у него основная характеристика по карточке?

**BOT:** Водонагреватель THERMEX MK 50 V. Артикул: 151002. Основные данные: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2. Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/

