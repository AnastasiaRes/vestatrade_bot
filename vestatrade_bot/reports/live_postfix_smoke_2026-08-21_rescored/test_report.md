# VestaTrade local bot evaluation

Дата: `2026-08-21T01:40:58+03:00`
Suite: `smoke`
API: `http://127.0.0.1:8011`
Модель: `qwen/qwen3-vl-8b-instruct` через `openrouter`
Каталог API: **14035** товаров, источник `file`.
XML ground truth: **14182** offer, **14054** уникальных SKU.

## Итог

Статус готовности: **NOT READY**

Диалогов: **10**; user turns: **29**.
Диалоги PASS/WARN/FAIL/UNVERIFIED: **7 / 0 / 3 / 0**.
Ответы PASS/WARN/FAIL/UNVERIFIED: **25 / 0 / 4 / 0**.
Pass rate: **70.0% по диалогам**, **86.21% по ответам**.
Hallucination rate: **0.0%** (0 ответов).
Потери контекста: **0**.
Latency p50/p95/max: **5.322 / 16.723 / 17.812 с**.
Технические ошибки: **0**; API probes: `{"PASS": 7}`.
Ходы с успешным LLM transport: **27**; источники финального ответа: `{"deterministic": 29}`.

## Наиболее частые ошибки

- `BAD_CLARIFICATION`: 3
- `RETRIEVAL_WRONG_PRODUCT`: 1

## Наиболее проблемные SKU

- `VT.331.N.04`: 1 FAIL-ответов
- `VT.217.N.04`: 1 FAIL-ответов
- `VT.217S.N.04`: 1 FAIL-ответов
- `VT.217.N.05`: 1 FAIL-ответов
- `VTp.781.0.04005`: 1 FAIL-ответов
- `VT.218.N.05`: 1 FAIL-ответов

## Повторяемость


## Каталог

- Нулевой остаток в XML: **11937**; положительный: **2245**; неизвестный: **0**.
- Топ категорий: `[["Фитинги", 3724], ["Радиаторы отопления", 2976], ["Котельное оборудование", 707], ["Насосное оборудование", 697], ["Канализационные системы", 595], ["Водозапорная арматура", 510], ["Водонагреватели", 438], ["Инструмент", 434], ["Смесители", 403], ["Трубы", 398], ["Коллекторы и аксессуары", 350], ["Регулирующая арматура", 321]]`.
- Топ брендов: `[["VALTEC", 2532], ["STOUT", 1501], ["ROMMER", 1300], ["Royal Thermo", 880], ["GEKON", 641], ["РОСТерм", 421], ["Гейзер", 323], ["UNIPUMP", 318], ["PRO AQUA", 302], ["RIFAR", 280], ["Thermex", 260], ["KERMI", 258]]`.
- Топ типов товара: `[["<empty>", 3170], ["Радиатор отопления", 2148], ["Тройник", 817], ["Муфта", 659], ["Угольник", 528], ["Насос", 467], ["Конвектор", 457], ["Котёл", 445], ["Водонагреватель", 424], ["Кран шаровой", 338], ["Смеситель", 262], ["Коллектор", 232], ["Труба", 201], ["Соединитель", 164], ["Переходник", 155]]`.
- Повторные группы: `{"similar_sku": 3, "incomplete_request": 3, "analog": 3, "correction": 3, "multi_turn_context": 3}`.

## Приоритетные исправления

- P1: проверять уже заполненные слоты перед вопросом и не переспрашивать назначение/размер, явно указанные в текущей реплике.

## Техническая диагностика

- Дополнительные findings не переданы.

## Targeted retests

- Отдельные targeted retests не зафиксированы в metadata.

## Полные диалоги серьёзных ошибок

### S01: Обычный покупатель: кран 1/2

Session: `qa-20260821-014058-S01-e9f592de`

USER: Нужен кран на воду полдюйма

BOT: Нашёл подходящие варианты: 1. Кран шаровой MINI 1/2" вн.-нар. Артикул: VT.331.N.04 Бренд: VALTEC Цена: 449 RUB Наличие: в наличии, 60 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/ 2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.331.N.04", "name": "Кран шаровой MINI 1/2\" вн.-нар.", "price": 449.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/42.jpg"}, {"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.217S.N.04", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2\"", "price": 485.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.217S.N/VT.217S.N-1.png"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": 0, "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "BAD_CLARIFICATION", "reason": "Incomplete first request produced products/no critical clarification", "severity": "FAIL"}]}`

USER: Этот точно полдюйма и с внутренней резьбой с обеих сторон? Мне нужен ВР-ВР.

BOT: Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 2. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ 3. Кран шаровой MINI 1/2" вн.-вн. Артикул: VT.330.N.04 Бренд: VALTEC Цена: 502 RUB Наличие: в наличии, 3 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-vn/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.217S.N.04", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2\"", "price": 485.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.217S.N/VT.217S.N-1.png"}, {"sku": "VT.330.N.04", "name": "Кран шаровой MINI 1/2\" вн.-вн.", "price": 502.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/41.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: А есть такой же с ручкой-бабочкой?

BOT: Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 2. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ 3. Кран шаровой BASE угловой с накидной гайкой 1/2" вн.-вн. (короткий) Артикул: VT.266.NS.0404 Бренд: VALTEC Цена: 653 RUB Наличие: в наличии, 1 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-uglovoj-s-nakidnoj-gajkoj-1-2-vn-vn-korotkij/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.217S.N.04", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2\"", "price": 485.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.217S.N/VT.217S.N-1.png"}, {"sku": "VT.266.NS.0404", "name": "Кран шаровой BASE угловой с накидной гайкой 1/2\" вн.-вн. (короткий)", "price": 653.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-uglovoj-s-nakidnoj-gajkoj-1-2-vn-vn-korotkij/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.266.NS/VT.266.NS_0.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

### S02: Обычный покупатель: уголок на трубу 20

Session: `qa-20260821-014058-S02-622bef68`

USER: Нужен уголок на пластиковую трубу 20

BOT: Понял, труба 20 мм. Для чего она: для холодной или горячей воды, для отопления или для канализации? Где именно она будет проложена?

Products: `[]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": 1, "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Труба PPR, нужен угол 90 градусов.

BOT: Уточню ещё раз, без этого не подберу точно. Понял, труба 20 мм. Для чего она: для холодной или горячей воды, для отопления или для канализации? Где именно она будет проложена?

Products: `[]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": 0, "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "BAD_CLARIFICATION", "reason": "Selection funnel asks an unrelated or already supplied parameter", "severity": "FAIL"}]}`

USER: Резьбовой выход не нужен, обе стороны под сварку 20 мм.

BOT: Не буду подставлять случайный товар, пока этот параметр не известен. Его обычно можно посмотреть в паспорте изделия или на шильдике, либо замерить на месте. Если данных нет совсем — напишите «передай менеджеру», и я оформлю задачу с тем, что уже известно. Понял, труба 20 мм. Для чего она: для холодной или горячей воды, для отопления или для канализации? Где именно она будет проложена?

Products: `[]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": 0, "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "BAD_CLARIFICATION", "reason": "Selection funnel asks an unrelated or already supplied parameter", "severity": "FAIL"}]}`

### S10: Контекст: размер, ручка, возврат

Session: `qa-20260821-014058-S10-bf5f17dc`

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

BOT: Кран шаровой MINI 1/2" вн.-нар.. Артикул: VT.331.N.04.

Products: `[{"sku": "VT.331.N.04", "name": "Кран шаровой MINI 1/2\" вн.-нар.", "price": 449.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/42.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

## Ограничения интерпретации

- Цена и остаток сверяются с локальным XML, но расхождение получает `UNVERIFIED_DYNAMIC_DATA`, а не автоматический FAIL.
- Публичный `/chat` не раскрывает полный hidden retrieval candidate set. Runner различает retrieval/selection и final answer по карточкам, debug agent trace и статическим ограничениям; невидимые отброшенные candidates независимо проверить нельзя.
- Session state хранится в памяти процесса; свежая/потерянная session проверяется на fail-closed, но автоматический restart сервера runner не выполняет.
- URL товаров сравниваются только как строки и никогда не открываются.
