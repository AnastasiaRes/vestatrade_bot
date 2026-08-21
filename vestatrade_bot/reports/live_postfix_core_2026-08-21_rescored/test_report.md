# VestaTrade local bot evaluation

Дата: `2026-08-21T01:45:35+03:00`
Suite: `core`
API: `http://127.0.0.1:8011`
Модель: `qwen/qwen3-vl-8b-instruct` через `openrouter`
Каталог API: **14035** товаров, источник `file`.
XML ground truth: **14182** offer, **14054** уникальных SKU.

## Итог

Статус готовности: **NOT READY**

Диалогов: **40**; user turns: **112**.
Диалоги PASS/WARN/FAIL/UNVERIFIED: **32 / 0 / 8 / 0**.
Ответы PASS/WARN/FAIL/UNVERIFIED: **100 / 0 / 12 / 0**.
Pass rate: **80.0% по диалогам**, **89.29% по ответам**.
Hallucination rate: **0.0%** (0 ответов).
Потери контекста: **3**.
Latency p50/p95/max: **4.586 / 14.81 / 18.642 с**.
Технические ошибки: **0**; API probes: `{"PASS": 7}`.
Ходы с успешным LLM transport: **89**; источники финального ответа: `{"deterministic": 112}`.

## Наиболее частые ошибки

- `BAD_CLARIFICATION`: 5
- `WRONG_SKU`: 4
- `RETRIEVAL_WRONG_PRODUCT`: 3
- `CONTEXT_LOSS`: 3
- `MISSED_CONSTRAINT`: 3
- `WRONG_ATTRIBUTE`: 3

## Наиболее проблемные SKU

- `VT.217.N.05`: 3 FAIL-ответов
- `VTp.781.0.04005`: 3 FAIL-ответов
- `VT.218.N.05`: 3 FAIL-ответов
- `VT.217.N.04`: 3 FAIL-ответов
- `500043`: 1 FAIL-ответов
- `500045`: 1 FAIL-ответов
- `500047`: 1 FAIL-ответов

## Повторяемость

- `similar_sku`: {"PASS": 3} — C-SIM-1, C-SIM-2, C-SIM-3
- `incomplete_request`: {"PASS": 3} — C-INC-1, C-INC-2, C-INC-3
- `analog`: {"PASS": 3} — C-ALT-1, C-ALT-2, C-ALT-3
- `correction`: {"PASS": 2, "FAIL": 1} — C-COR-1, C-COR-2, C-COR-3
- `multi_turn_context`: {"FAIL": 3} — C-CTX-1, C-CTX-2, C-CTX-3

## Каталог

- Нулевой остаток в XML: **11937**; положительный: **2245**; неизвестный: **0**.
- Топ категорий: `[["Фитинги", 3724], ["Радиаторы отопления", 2976], ["Котельное оборудование", 707], ["Насосное оборудование", 697], ["Канализационные системы", 595], ["Водозапорная арматура", 510], ["Водонагреватели", 438], ["Инструмент", 434], ["Смесители", 403], ["Трубы", 398], ["Коллекторы и аксессуары", 350], ["Регулирующая арматура", 321]]`.
- Топ брендов: `[["VALTEC", 2532], ["STOUT", 1501], ["ROMMER", 1300], ["Royal Thermo", 880], ["GEKON", 641], ["РОСТерм", 421], ["Гейзер", 323], ["UNIPUMP", 318], ["PRO AQUA", 302], ["RIFAR", 280], ["Thermex", 260], ["KERMI", 258]]`.
- Топ типов товара: `[["<empty>", 3170], ["Радиатор отопления", 2148], ["Тройник", 817], ["Муфта", 659], ["Угольник", 528], ["Насос", 467], ["Конвектор", 457], ["Котёл", 445], ["Водонагреватель", 424], ["Кран шаровой", 338], ["Смеситель", 262], ["Коллектор", 232], ["Труба", 201], ["Соединитель", 164], ["Переходник", 155]]`.
- Повторные группы: `{"similar_sku": 3, "incomplete_request": 3, "analog": 3, "correction": 3, "multi_turn_context": 3}`.

## Приоритетные исправления

- P0: усилить hard-filtering до ранжирования для размера, типа резьбы, формы, ручки и системы соединения; не показывать SKU с несовпавшим обязательным параметром.
- P1: хранить versioned active constraints и явно заменять исправленный слот; добавить проверку referent SKU перед ответом на «первый/он/такой же».
- P1: проверять уже заполненные слоты перед вопросом и не переспрашивать назначение/размер, явно указанные в текущей реплике.

## Техническая диагностика

- Дополнительные findings не переданы.

## Targeted retests

- Отдельные targeted retests не зафиксированы в metadata.

## Полные диалоги серьёзных ошибок

### C-COR-3: Повтор исправления требования #3

Session: `qa-20260821-014535-C-COR-3-20e665df`

USER: Нужен термостатический клапан прямой 1/2 для радиатора

BOT: Нашёл подходящие варианты: 1. Клапан термостатический для рад. прямой 1/2" (с доп. уплотнением) Артикул: VT.032.NR.04 Бренд: VALTEC Цена: 907 RUB Наличие: в наличии, 21 шт. Ссылка: https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-rad-pryamoy-12-s-dop-uplotneniem/ 2. Клапан термостатический для рад. прямой 1/2" Артикул: VT.032.N.04 Бренд: VALTEC Цена: 1673 RUB Наличие: в наличии, 1 шт. Ссылка: https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-rad-pryamoy-12/ 3. Клапан термостатический прямой Heizen 1/2" Артикул: TVD 566-15 Бренд: Heizen Цена: 739 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Клапан термостатический; область применения: Для систем отопления Ссылка: https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/armatura-dlya-radiatorov-heizen/klapan-termostaticheskij-prjamoj-heizen-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи. Кстати, если нужна регулировка температуры, к радиаторному клапану берут термоголовку — могу подобрать.

Products: `[{"sku": "VT.032.NR.04", "name": "Клапан термостатический для рад. прямой 1/2\" (с доп. уплотнением)", "price": 907.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-rad-pryamoy-12-s-dop-uplotneniem/", "image_url": "https://www.vestatrade.ru/image/catalog/radiatori_otopleniy/klapanvaltek/vt.032.nr_0.jpg"}, {"sku": "VT.032.N.04", "name": "Клапан термостатический для рад. прямой 1/2\"", "price": 1673.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-rad-pryamoy-12/", "image_url": "https://www.vestatrade.ru/image/catalog/radiatori_otopleniy/klapanvaltek/termap.jpg"}, {"sku": "TVD 566-15", "name": "Клапан термостатический прямой Heizen 1/2\"", "price": 739.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/armatura-dlya-radiatorov-heizen/klapan-termostaticheskij-prjamoj-heizen-12/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Heizen/TVD566-15/TVD_566-15_1.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Нет, перепутал: нужен угловой, а не прямой, остальные параметры те же.

BOT: Подскажите для радиатора: регулировать температуру (термоголовка) или просто перекрывать поток.

Products: `[]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": 0, "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "BAD_CLARIFICATION", "reason": "Selection funnel asks an unrelated or already supplied parameter", "severity": "FAIL"}]}`

USER: Подтвердите текущую угловую форму.

BOT: Уточню ещё раз, без этого не подберу точно. Подскажите для радиатора: регулировать температуру (термоголовка) или просто перекрывать поток.

Products: `[]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": 0, "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "BAD_CLARIFICATION", "reason": "Selection funnel asks an unrelated or already supplied parameter", "severity": "FAIL"}]}`

### C-CTX-1: Повтор multi-turn context #1

Session: `qa-20260821-014535-C-CTX-1-535962dc`

USER: Покажи кран 1/2 для воды

BOT: Нашёл подходящие варианты: 1. Кран шаровой MINI 1/2" вн.-нар. Артикул: VT.331.N.04 Бренд: VALTEC Цена: 449 RUB Наличие: в наличии, 60 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/ 2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.331.N.04", "name": "Кран шаровой MINI 1/2\" вн.-нар.", "price": 449.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/42.jpg"}, {"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.217S.N.04", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2\"", "price": 485.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.217S.N/VT.217S.N-1.png"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: А такой же 3/4?

BOT: Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Тройник коллекторный PPR с шаровым краном, 40мм х 3/4" нар. (евроконус) Артикул: VTp.781.0.04005 Бренд: VALTEC Цена: 700 RUB Наличие: в наличии, 3 шт. Характеристики: диаметр (мм): 40; тип резьбы: Наружная; назначение: Водоснабжение, Отопление Ссылка: https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-belye-valtec/troynik-kollektornyy-ppr-s-sharovym-kranom-40mm-h-34-nar-evrokonus/ 3. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.217.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-вн.", "price": 687.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VTp.781.0.04005", "name": "Тройник коллекторный PPR с шаровым краном, 40мм х 3/4\" нар. (евроконус)", "price": 700.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-belye-valtec/troynik-kollektornyy-ppr-s-sharovym-kranom-40mm-h-34-nar-evrokonus/", "image_url": "https://www.vestatrade.ru/image/catalog/product/trubiifitingi/valtec/b.jpg"}, {"sku": "VT.218.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-нар.", "price": 779.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/37.jpg"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 1, "constraints": 0, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "RETRIEVAL_WRONG_PRODUCT", "reason": "Returned card violates active constraints: VTp.781.0.04005: product_kind=ball_valve", "severity": "FAIL"}]}`

USER: А с бабочкой?

BOT: Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4" Артикул: VT.218S.N.05 Бренд: VALTEC Цена: 821 RUB Наличие: в наличии, 22 шт. Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.217.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-вн.", "price": 687.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.218.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-нар.", "price": 779.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/37.jpg"}, {"sku": "VT.218S.N.05", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4\"", "price": 821.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.218S.N/VT.218S.N_0.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Вернемся к первому показанному товару. Какой у него артикул?

BOT: Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.. Артикул: VT.217.N.04.

Products: `[{"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 0, "constraints": 0, "context": 0, "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "WRONG_SKU", "reason": "Expected SKU(s) not returned/named: ['VT.331.N.04']; got=['VT.217.N.04']", "severity": "FAIL"}, {"code": "CONTEXT_LOSS", "reason": "Contextual referent lost: ['VT.331.N.04']", "severity": "FAIL"}, {"code": "MISSED_CONSTRAINT", "reason": "Returned card violates active constraints: VT.217.N.04: size_inch=3/4", "severity": "FAIL"}, {"code": "WRONG_ATTRIBUTE", "reason": "Expected grounded value absent from answer: VT.331.N.04", "severity": "FAIL"}]}`

### C-CTX-2: Повтор multi-turn context #2

Session: `qa-20260821-014535-C-CTX-2-f6c11c03`

USER: Покажи кран 1/2 для воды

BOT: Нашёл подходящие варианты: 1. Кран шаровой MINI 1/2" вн.-нар. Артикул: VT.331.N.04 Бренд: VALTEC Цена: 449 RUB Наличие: в наличии, 60 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/ 2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.331.N.04", "name": "Кран шаровой MINI 1/2\" вн.-нар.", "price": 449.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/42.jpg"}, {"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.217S.N.04", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2\"", "price": 485.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.217S.N/VT.217S.N-1.png"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: А такой же 3/4?

BOT: Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Тройник коллекторный PPR с шаровым краном, 40мм х 3/4" нар. (евроконус) Артикул: VTp.781.0.04005 Бренд: VALTEC Цена: 700 RUB Наличие: в наличии, 3 шт. Характеристики: диаметр (мм): 40; тип резьбы: Наружная; назначение: Водоснабжение, Отопление Ссылка: https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-belye-valtec/troynik-kollektornyy-ppr-s-sharovym-kranom-40mm-h-34-nar-evrokonus/ 3. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.217.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-вн.", "price": 687.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VTp.781.0.04005", "name": "Тройник коллекторный PPR с шаровым краном, 40мм х 3/4\" нар. (евроконус)", "price": 700.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-belye-valtec/troynik-kollektornyy-ppr-s-sharovym-kranom-40mm-h-34-nar-evrokonus/", "image_url": "https://www.vestatrade.ru/image/catalog/product/trubiifitingi/valtec/b.jpg"}, {"sku": "VT.218.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-нар.", "price": 779.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/37.jpg"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 1, "constraints": 0, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "RETRIEVAL_WRONG_PRODUCT", "reason": "Returned card violates active constraints: VTp.781.0.04005: product_kind=ball_valve", "severity": "FAIL"}]}`

USER: А с бабочкой?

BOT: Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4" Артикул: VT.218S.N.05 Бренд: VALTEC Цена: 821 RUB Наличие: в наличии, 22 шт. Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.217.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-вн.", "price": 687.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.218.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-нар.", "price": 779.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/37.jpg"}, {"sku": "VT.218S.N.05", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4\"", "price": 821.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.218S.N/VT.218S.N_0.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Вернемся к первому показанному товару. Какой у него артикул?

BOT: Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.. Артикул: VT.217.N.04.

Products: `[{"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 0, "constraints": 0, "context": 0, "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "WRONG_SKU", "reason": "Expected SKU(s) not returned/named: ['VT.331.N.04']; got=['VT.217.N.04']", "severity": "FAIL"}, {"code": "CONTEXT_LOSS", "reason": "Contextual referent lost: ['VT.331.N.04']", "severity": "FAIL"}, {"code": "MISSED_CONSTRAINT", "reason": "Returned card violates active constraints: VT.217.N.04: size_inch=3/4", "severity": "FAIL"}, {"code": "WRONG_ATTRIBUTE", "reason": "Expected grounded value absent from answer: VT.331.N.04", "severity": "FAIL"}]}`

### C-CTX-3: Повтор multi-turn context #3

Session: `qa-20260821-014535-C-CTX-3-68177001`

USER: Покажи кран 1/2 для воды

BOT: Нашёл подходящие варианты: 1. Кран шаровой MINI 1/2" вн.-нар. Артикул: VT.331.N.04 Бренд: VALTEC Цена: 449 RUB Наличие: в наличии, 60 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/ 2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.331.N.04", "name": "Кран шаровой MINI 1/2\" вн.-нар.", "price": 449.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/42.jpg"}, {"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.217S.N.04", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2\"", "price": 485.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.217S.N/VT.217S.N-1.png"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: А такой же 3/4?

BOT: Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Тройник коллекторный PPR с шаровым краном, 40мм х 3/4" нар. (евроконус) Артикул: VTp.781.0.04005 Бренд: VALTEC Цена: 700 RUB Наличие: в наличии, 3 шт. Характеристики: диаметр (мм): 40; тип резьбы: Наружная; назначение: Водоснабжение, Отопление Ссылка: https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-belye-valtec/troynik-kollektornyy-ppr-s-sharovym-kranom-40mm-h-34-nar-evrokonus/ 3. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.217.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-вн.", "price": 687.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VTp.781.0.04005", "name": "Тройник коллекторный PPR с шаровым краном, 40мм х 3/4\" нар. (евроконус)", "price": 700.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-belye-valtec/troynik-kollektornyy-ppr-s-sharovym-kranom-40mm-h-34-nar-evrokonus/", "image_url": "https://www.vestatrade.ru/image/catalog/product/trubiifitingi/valtec/b.jpg"}, {"sku": "VT.218.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-нар.", "price": 779.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/37.jpg"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 1, "constraints": 0, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "RETRIEVAL_WRONG_PRODUCT", "reason": "Returned card violates active constraints: VTp.781.0.04005: product_kind=ball_valve", "severity": "FAIL"}]}`

USER: А с бабочкой?

BOT: Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4" Артикул: VT.218S.N.05 Бренд: VALTEC Цена: 821 RUB Наличие: в наличии, 22 шт. Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.217.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-вн.", "price": 687.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.218.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-нар.", "price": 779.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/37.jpg"}, {"sku": "VT.218S.N.05", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4\"", "price": 821.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.218S.N/VT.218S.N_0.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Вернемся к первому показанному товару. Какой у него артикул?

BOT: Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.. Артикул: VT.217.N.04.

Products: `[{"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 0, "constraints": 0, "context": 0, "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "WRONG_SKU", "reason": "Expected SKU(s) not returned/named: ['VT.331.N.04']; got=['VT.217.N.04']", "severity": "FAIL"}, {"code": "CONTEXT_LOSS", "reason": "Contextual referent lost: ['VT.331.N.04']", "severity": "FAIL"}, {"code": "MISSED_CONSTRAINT", "reason": "Returned card violates active constraints: VT.217.N.04: size_inch=3/4", "severity": "FAIL"}, {"code": "WRONG_ATTRIBUTE", "reason": "Expected grounded value absent from answer: VT.331.N.04", "severity": "FAIL"}]}`

### C03: Опечатка в артикуле

Session: `qa-20260821-014535-C03-04ce9499`

USER: Найди артикул 15100Z

BOT: Не нашёл подходящие товары в текущем ассортименте. Могу уточнить параметры или передать вопрос менеджеру.

Products: `[]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "WRONG_SKU", "reason": "None of expected related SKUs present: ['151002']", "severity": "FAIL"}]}`

USER: Исправляю: точный артикул 151002

BOT: Нашёл подходящие варианты: 1. Водонагреватель THERMEX MK 50 V Артикул: 151002 Бренд: Thermex Цена: 13160 RUB Наличие: в наличии, 8 шт. Характеристики: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2 Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/ Перед покупкой сверьте способ монтажа, подвод воды, электропитание или источник нагрева с паспортом этой модели.

Products: `[{"sku": "151002", "name": "Водонагреватель THERMEX MK 50 V", "price": 13160.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/", "image_url": "https://www.vestatrade.ru/image/catalog/product/import/tcjs3p6ay22r1mo3vlvzd16dnlshawgk.png"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Какая у него основная характеристика по карточке?

BOT: Водонагреватель THERMEX MK 50 V. Артикул: 151002. Основные данные: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2; макс. температура нагрева воды, °с: 75; присоединительный размер: G1/2; мощность, квт: 2; режимы мощности электрической, вт: 800/1200/2000; напор: в карточке не указано; расход/производительность: в карточке не указано; монтажная длина: в карточке не указано; материал нагревательного элемента: Медь; материал внутреннего бака: Нержавеющая сталь; макс. давление воды, мпа: 0.7; мин. давление воды, мпа: 0.05; количество контуров: в карточке не указано; камера сгорания: в карточке не указано. Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/

Products: `[{"sku": "151002", "name": "Водонагреватель THERMEX MK 50 V", "price": 13160.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/", "image_url": "https://www.vestatrade.ru/image/catalog/product/import/tcjs3p6ay22r1mo3vlvzd16dnlshawgk.png"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

### C07: Прямой против углового

Session: `qa-20260821-014535-C07-8fecf4c0`

USER: Термостатический клапан для радиатора угловой 1/2

BOT: Нашёл подходящие варианты: 1. Клапан термостатический для рад. угловой 1/2" (с доп. уплотнением) Артикул: VT.031.NR.04 Бренд: VALTEC Цена: 919 RUB Наличие: в наличии, 18 шт. Ссылка: https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-rad-uglovoy-12-s-dop-uplotneniem/ 2. Клапан термостатический для радиатора угловой с преднастройкой (KV 0,1-0,6) 1/2" Артикул: VT.037.N.04 Бренд: VALTEC Цена: 1715 RUB Наличие: нет в наличии, 0 шт. Ссылка: https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-radiatora-uglovoy-s-prednastroykoy-kv-01-06-12/ 3. Клапан термостатический для радиатора угловой с осевым управлением 1/2" Артикул: VT.179.N.04 Бренд: VALTEC Цена: 1935 RUB Наличие: нет в наличии, 0 шт. Ссылка: https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-radiatora-uglovoy-s-osevym-upravleniem-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи. Кстати, если нужна регулировка температуры, к радиаторному клапану берут термоголовку — могу подобрать.

Products: `[{"sku": "VT.031.NR.04", "name": "Клапан термостатический для рад. угловой 1/2\" (с доп. уплотнением)", "price": 919.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-rad-uglovoy-12-s-dop-uplotneniem/", "image_url": "https://www.vestatrade.ru/image/catalog/radiatori_otopleniy/klapanvaltek/vt.031.nr_0222.jpg"}, {"sku": "VT.037.N.04", "name": "Клапан термостатический для радиатора угловой с преднастройкой (KV 0,1-0,6) 1/2\"", "price": 1715.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-radiatora-uglovoy-s-prednastroykoy-kv-01-06-12/", "image_url": "https://www.vestatrade.ru/image/catalog/radiatori_otopleniy/klapanvaltek/2.jpg"}, {"sku": "VT.179.N.04", "name": "Клапан термостатический для радиатора угловой с осевым управлением 1/2\"", "price": 1935.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/termoreguliruyushchaya-armatura-dlya-radiatorov/klapany-valtec-dlya-radiatorov/klapany-termostaticheskie-valtec-dlya-radiatorov/klapan-termostaticheskiy-dlya-radiatora-uglovoy-s-osevym-upravleniem-12/", "image_url": "https://www.vestatrade.ru/image/catalog/radiatori_otopleniy/klapanvaltek/1.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Проверьте, что он угловой, а не прямой.

BOT: Подскажите для радиатора: регулировать температуру (термоголовка) или просто перекрывать поток.

Products: `[]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": 0, "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "BAD_CLARIFICATION", "reason": "Selection funnel asks an unrelated or already supplied parameter", "severity": "FAIL"}]}`

### C08: Канализация 50 против 110

Session: `qa-20260821-014535-C08-14c326d5`

USER: Нужна внутренняя канализационная труба 50 мм

BOT: Важно: у всех найденных позиций сейчас нет подтверждённого положительного остатка. Показываю их справочно; для покупки нужно подобрать доступный аналог или уточнить поставку у менеджера. Нашёл подходящие варианты: 1. Труба с раструбом 50 х 250 мм (СИНИКОН) Артикул: 500043 Бренд: СИНИКОН Цена: 114 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-250-mm-sinikon/ 2. Труба с раструбом 50 х 500 мм (СИНИКОН) Артикул: 500045 Бренд: СИНИКОН Цена: 144 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-500-mm-sinikon/ 3. Труба с раструбом 50 х 750 мм (СИНИКОН) Артикул: 500047 Бренд: СИНИКОН Цена: 183 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-750-mm-sinikon/ Могу сравнить эти варианты по главным отличиям для вашей задачи. Кстати, к канализационной трубе часто берут отводы и муфты того же диаметра. Если нужно, напишите, например, «отвод 50».

Products: `[{"sku": "500043", "name": "Труба с раструбом 50 х 250 мм (СИНИКОН)", "price": 114.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-250-mm-sinikon/", "image_url": "https://www.vestatrade.ru/image/catalog/product/kanalizacionnyesistemy/123/59.jpg"}, {"sku": "500045", "name": "Труба с раструбом 50 х 500 мм (СИНИКОН)", "price": 144.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-500-mm-sinikon/", "image_url": "https://www.vestatrade.ru/image/catalog/product/kanalizacionnyesistemy/123/59.jpg"}, {"sku": "500047", "name": "Труба с раструбом 50 х 750 мм (СИНИКОН)", "price": 183.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-750-mm-sinikon/", "image_url": "https://www.vestatrade.ru/image/catalog/product/kanalizacionnyesistemy/123/59.jpg"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": 0, "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "BAD_CLARIFICATION", "reason": "Incomplete first request produced products/no critical clarification", "severity": "FAIL"}]}`

USER: Подождите, вы предложили товар до уточнения важных параметров. Длина 1000 мм.

BOT: Нашёл подходящие варианты: 1. Труба канализационная, HTEM, 50*1000"10 Артикул: 112040 Бренд: OSTENDORF Цена: 217 RUB Наличие: в наличии, 291 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-ostendorf/truba-kanalizacionnaya-htem-50100010/ 2. Труба с раструбом 50 х 1000 мм (СИНИКОН) Артикул: 500049 Бренд: СИНИКОН Цена: 228 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-1000-mm-sinikon/ 3. Труба PP д.50*1000 (Синикон) Комфорт Плюс Артикул: 500049.К Бренд: СИНИКОН Цена: 357 RUB Наличие: нет в наличии, 0 шт. Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-besshumnaya-sinikon/truba-pp-d501000-sinikon-komfort-plyus/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "112040", "name": "Труба канализационная, HTEM, 50*1000\"10", "price": 217.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-ostendorf/truba-kanalizacionnaya-htem-50100010/", "image_url": "https://www.vestatrade.ru/image/catalog/product/kanalizacionnyesistemy/25.jpg"}, {"sku": "500049", "name": "Труба с раструбом 50 х 1000 мм (СИНИКОН)", "price": 228.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-1000-mm-sinikon/", "image_url": "https://www.vestatrade.ru/image/catalog/product/kanalizacionnyesistemy/123/59.jpg"}, {"sku": "500049.К", "name": "Труба PP д.50*1000 (Синикон) Комфорт Плюс", "price": 357.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-besshumnaya-sinikon/truba-pp-d501000-sinikon-komfort-plyus/", "image_url": "https://www.vestatrade.ru/image/catalog/product/fitingi/cinikon/500049.k-photo.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Именно внутренняя, серая.

BOT: Нашёл подходящие варианты: 1. Труба канализационная, HTEM, 50*1000"10 Артикул: 112040 Бренд: OSTENDORF Цена: 217 RUB Наличие: в наличии, 291 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-ostendorf/truba-kanalizacionnaya-htem-50100010/ 2. Труба с раструбом 50 х 1000 мм (СИНИКОН) Артикул: 500049 Бренд: СИНИКОН Цена: 228 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Труба Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-1000-mm-sinikon/ 3. Труба PP д.50*1000 (Синикон) Комфорт Плюс Артикул: 500049.К Бренд: СИНИКОН Цена: 357 RUB Наличие: нет в наличии, 0 шт. Ссылка: https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-besshumnaya-sinikon/truba-pp-d501000-sinikon-komfort-plyus/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "112040", "name": "Труба канализационная, HTEM, 50*1000\"10", "price": 217.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-ostendorf/truba-kanalizacionnaya-htem-50100010/", "image_url": "https://www.vestatrade.ru/image/catalog/product/kanalizacionnyesistemy/25.jpg"}, {"sku": "500049", "name": "Труба с раструбом 50 х 1000 мм (СИНИКОН)", "price": 228.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-vnutrennyaya-sinikon/truba-s-rastrubom-50-h-1000-mm-sinikon/", "image_url": "https://www.vestatrade.ru/image/catalog/product/kanalizacionnyesistemy/123/59.jpg"}, {"sku": "500049.К", "name": "Труба PP д.50*1000 (Синикон) Комфорт Плюс", "price": 357.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/kanalizacionnye-sistemy/kanalizacionnye-truby-i-fitingi/kanalizaciya-vnutrennyaya/kanalizaciya-besshumnaya-sinikon/truba-pp-d501000-sinikon-komfort-plyus/", "image_url": "https://www.vestatrade.ru/image/catalog/product/fitingi/cinikon/500049.k-photo.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

### C13: Опечатка термоголовка

Session: `qa-20260821-014535-C13-6b10bcba`

USER: термогаловка на батарею 1/2

BOT: Подскажите для радиатора: прямое или угловое подключение; регулировать температуру (термоголовка) или просто перекрывать поток.

Products: `[]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": 1, "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Нужна вместе с клапаном.

BOT: Подскажите для радиатора: прямое или угловое подключение; размер 1/2 или 3/4; регулировать температуру (термоголовка) или просто перекрывать поток.

Products: `[]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Клапан прямой.

BOT: Подскажите для радиатора: размер 1/2 или 3/4; регулировать температуру (термоголовка) или просто перекрывать поток.

Products: `[]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": 0, "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "BAD_CLARIFICATION", "reason": "Selection funnel asks an unrelated or already supplied parameter", "severity": "FAIL"}]}`

## Ограничения интерпретации

- Цена и остаток сверяются с локальным XML, но расхождение получает `UNVERIFIED_DYNAMIC_DATA`, а не автоматический FAIL.
- Публичный `/chat` не раскрывает полный hidden retrieval candidate set. Runner различает retrieval/selection и final answer по карточкам, debug agent trace и статическим ограничениям; невидимые отброшенные candidates независимо проверить нельзя.
- Session state хранится в памяти процесса; свежая/потерянная session проверяется на fail-closed, но автоматический restart сервера runner не выполняет.
- URL товаров сравниваются только как строки и никогда не открываются.
