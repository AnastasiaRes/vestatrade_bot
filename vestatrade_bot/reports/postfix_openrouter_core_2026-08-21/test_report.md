# VestaTrade local bot evaluation

Дата: `2026-08-21T00:24:52+03:00`
Suite: `core`
API: `http://127.0.0.1:8765`
Модель: `qwen/qwen3-vl-8b-instruct` через `openrouter`
Каталог API: **14035** товаров, источник `file`.
XML ground truth: **14182** offer, **14054** уникальных SKU.

## Итог

Статус готовности: **NOT READY**

Диалогов: **40**; user turns: **112**.
Диалоги PASS/WARN/FAIL/UNVERIFIED: **32 / 0 / 8 / 0**.
Ответы PASS/WARN/FAIL/UNVERIFIED: **103 / 0 / 9 / 0**.
Pass rate: **80.0% по диалогам**, **91.96% по ответам**.
Hallucination rate: **0.0%** (0 ответов).
Потери контекста: **2**.
Latency p50/p95/max: **4.62 / 8.53 / 21.781 с**.
Технические ошибки: **0**; API probes: `{"PASS": 7}`.
Ходы с успешным LLM transport: **91**; источники финального ответа: `{"deterministic": 110, "response_llm": 2}`.

## Наиболее частые ошибки

- `WRONG_SKU`: 4
- `MISSED_CONSTRAINT`: 3
- `BAD_CLARIFICATION`: 2
- `CONTEXT_LOSS`: 2
- `WRONG_ATTRIBUTE`: 1

## Наиболее проблемные SKU

- `VT.331.N.04`: 3 FAIL-ответов
- `100013619`: 2 FAIL-ответов
- `500043`: 1 FAIL-ответов
- `500045`: 1 FAIL-ответов
- `500047`: 1 FAIL-ответов

## Повторяемость

- `similar_sku`: {"PASS": 3} — C-SIM-1, C-SIM-2, C-SIM-3
- `incomplete_request`: {"PASS": 3} — C-INC-1, C-INC-2, C-INC-3
- `analog`: {"PASS": 3} — C-ALT-1, C-ALT-2, C-ALT-3
- `correction`: {"PASS": 3} — C-COR-1, C-COR-2, C-COR-3
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

### C-CTX-1: Повтор multi-turn context #1

Session: `qa-20260821-002452-C-CTX-1-f2d84df0`

USER: Покажи кран 1/2 для воды

BOT: Нашёл подходящие варианты: 1. Кран шаровой MINI 1/2" вн.-нар. Артикул: VT.331.N.04 Бренд: VALTEC Цена: 449 RUB Наличие: в наличии, 60 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/ 2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.331.N.04", "name": "Кран шаровой MINI 1/2\" вн.-нар.", "price": 449.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/42.jpg"}, {"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.217S.N.04", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2\"", "price": 485.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.217S.N/VT.217S.N-1.png"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: А такой же 3/4?

BOT: Нашёл подходящие варианты: 1. Кран шаровой угловой для подкл. с/т приборов 1/2"х3/4" Артикул: VT.392.N.05 Бренд: VALTEC Цена: 521 RUB Наличие: в наличии, 113 шт. Характеристики: тип товара: Кран шаровой угловой Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-uglovoy-dlya-podkl-st-priborov-12h34/ 2. Кран шаровой для подкл. с/т приборов 1/2"х3/4"х1/2" Артикул: VT.256.N.04 Бренд: VALTEC Цена: 577 RUB Наличие: в наличии, 33 шт. Характеристики: тип товара: Кран шаровой Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-dlya-podkl-st-priborov-12h34h12/ 3. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.392.N.05", "name": "Кран шаровой угловой для подкл. с/т приборов 1/2\"х3/4\"", "price": 521.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-uglovoy-dlya-podkl-st-priborov-12h34/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/22.jpg"}, {"sku": "VT.256.N.04", "name": "Кран шаровой для подкл. с/т приборов 1/2\"х3/4\"х1/2\"", "price": 577.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-dlya-podkl-st-priborov-12h34h12/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/21.jpg"}, {"sku": "VT.217.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-вн.", "price": 687.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: А с бабочкой?

BOT: Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4" Артикул: VT.218S.N.05 Бренд: VALTEC Цена: 821 RUB Наличие: в наличии, 22 шт. Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.217.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-вн.", "price": 687.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.218.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-нар.", "price": 779.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/37.jpg"}, {"sku": "VT.218S.N.05", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4\"", "price": 821.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.218S.N/VT.218S.N_0.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Вернемся к первому показанному товару. Какой у него артикул?

BOT: Кран шаровой MINI 1/2" вн.-нар.. Артикул: VT.331.N.04.

Products: `[{"sku": "VT.331.N.04", "name": "Кран шаровой MINI 1/2\" вн.-нар.", "price": 449.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/42.jpg"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 1, "constraints": 0, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "MISSED_CONSTRAINT", "reason": "Returned card violates active constraints: VT.331.N.04: size_inch=3/4, handle=бабочка", "severity": "FAIL"}]}`

### C-CTX-2: Повтор multi-turn context #2

Session: `qa-20260821-002452-C-CTX-2-d355a1d8`

USER: Покажи кран 1/2 для воды

BOT: Нашёл подходящие варианты: 1. Кран шаровой MINI 1/2" вн.-нар. Артикул: VT.331.N.04 Бренд: VALTEC Цена: 449 RUB Наличие: в наличии, 60 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/ 2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.331.N.04", "name": "Кран шаровой MINI 1/2\" вн.-нар.", "price": 449.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/42.jpg"}, {"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.217S.N.04", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2\"", "price": 485.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.217S.N/VT.217S.N-1.png"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: А такой же 3/4?

BOT: Нашёл подходящие варианты: 1. Кран шаровой угловой для подкл. с/т приборов 1/2"х3/4" Артикул: VT.392.N.05 Бренд: VALTEC Цена: 521 RUB Наличие: в наличии, 113 шт. Характеристики: тип товара: Кран шаровой угловой Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-uglovoy-dlya-podkl-st-priborov-12h34/ 2. Кран шаровой для подкл. с/т приборов 1/2"х3/4"х1/2" Артикул: VT.256.N.04 Бренд: VALTEC Цена: 577 RUB Наличие: в наличии, 33 шт. Характеристики: тип товара: Кран шаровой Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-dlya-podkl-st-priborov-12h34h12/ 3. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.392.N.05", "name": "Кран шаровой угловой для подкл. с/т приборов 1/2\"х3/4\"", "price": 521.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-uglovoy-dlya-podkl-st-priborov-12h34/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/22.jpg"}, {"sku": "VT.256.N.04", "name": "Кран шаровой для подкл. с/т приборов 1/2\"х3/4\"х1/2\"", "price": 577.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-dlya-podkl-st-priborov-12h34h12/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/21.jpg"}, {"sku": "VT.217.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-вн.", "price": 687.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: А с бабочкой?

BOT: Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4" Артикул: VT.218S.N.05 Бренд: VALTEC Цена: 821 RUB Наличие: в наличии, 22 шт. Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.217.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-вн.", "price": 687.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.218.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-нар.", "price": 779.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/37.jpg"}, {"sku": "VT.218S.N.05", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4\"", "price": 821.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.218S.N/VT.218S.N_0.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Вернемся к первому показанному товару. Какой у него артикул?

BOT: Кран шаровой MINI 1/2" вн.-нар.. Артикул: VT.331.N.04.

Products: `[{"sku": "VT.331.N.04", "name": "Кран шаровой MINI 1/2\" вн.-нар.", "price": 449.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/42.jpg"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 1, "constraints": 0, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "MISSED_CONSTRAINT", "reason": "Returned card violates active constraints: VT.331.N.04: size_inch=3/4, handle=бабочка", "severity": "FAIL"}]}`

### C-CTX-3: Повтор multi-turn context #3

Session: `qa-20260821-002452-C-CTX-3-522ddbfe`

USER: Покажи кран 1/2 для воды

BOT: Нашёл подходящие варианты: 1. Кран шаровой MINI 1/2" вн.-нар. Артикул: VT.331.N.04 Бренд: VALTEC Цена: 449 RUB Наличие: в наличии, 60 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/ 2. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.331.N.04", "name": "Кран шаровой MINI 1/2\" вн.-нар.", "price": 449.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/42.jpg"}, {"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.217S.N.04", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2\"", "price": 485.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.217S.N/VT.217S.N-1.png"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: А такой же 3/4?

BOT: Нашёл подходящие варианты: 1. Кран шаровой угловой для подкл. с/т приборов 1/2"х3/4" Артикул: VT.392.N.05 Бренд: VALTEC Цена: 521 RUB Наличие: в наличии, 113 шт. Характеристики: тип товара: Кран шаровой угловой Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-uglovoy-dlya-podkl-st-priborov-12h34/ 2. Кран шаровой для подкл. с/т приборов 1/2"х3/4"х1/2" Артикул: VT.256.N.04 Бренд: VALTEC Цена: 577 RUB Наличие: в наличии, 33 шт. Характеристики: тип товара: Кран шаровой Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-dlya-podkl-st-priborov-12h34h12/ 3. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.392.N.05", "name": "Кран шаровой угловой для подкл. с/т приборов 1/2\"х3/4\"", "price": 521.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-uglovoy-dlya-podkl-st-priborov-12h34/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/22.jpg"}, {"sku": "VT.256.N.04", "name": "Кран шаровой для подкл. с/т приборов 1/2\"х3/4\"х1/2\"", "price": 577.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/kotelnoe-oborudovanie-navien/ventili-valtec-dlya-bytovoy-tehniki/kran-shar-dlya-podkl-st-priborov-12h34h12/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/21.jpg"}, {"sku": "VT.217.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-вн.", "price": 687.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: А с бабочкой?

BOT: Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-вн. Артикул: VT.217.N.05 Бренд: VALTEC Цена: 687 RUB Наличие: в наличии, 45 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/ 2. Кран шаровой BASE, рукоятка бабочка 3/4" вн.-нар. Артикул: VT.218.N.05 Бренд: VALTEC Цена: 779 RUB Наличие: в наличии, 94 шт. Характеристики: диаметр подключения, дюйм: 3/4; тип резьбы: С внутренней наружной резьбой (fm); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/ 3. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4" Артикул: VT.218S.N.05 Бренд: VALTEC Цена: 821 RUB Наличие: в наличии, 22 шт. Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.217.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-вн.", "price": 687.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.218.N.05", "name": "Кран шаровой BASE, рукоятка бабочка 3/4\" вн.-нар.", "price": 779.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-34-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/37.jpg"}, {"sku": "VT.218S.N.05", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-нар. DN20, PN40, 3/4\"", "price": 821.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-nar-dn20-pn40-34/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.218S.N/VT.218S.N_0.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Вернемся к первому показанному товару. Какой у него артикул?

BOT: Кран шаровой MINI 1/2" вн.-нар.. Артикул: VT.331.N.04.

Products: `[{"sku": "VT.331.N.04", "name": "Кран шаровой MINI 1/2\" вн.-нар.", "price": 449.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-nar/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/42.jpg"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 1, "constraints": 0, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "MISSED_CONSTRAINT", "reason": "Returned card violates active constraints: VT.331.N.04: size_inch=3/4, handle=бабочка", "severity": "FAIL"}]}`

### C03: Опечатка в артикуле

Session: `qa-20260821-002452-C03-60ffc646`

USER: Найди артикул 15100Z

BOT: AI-консультант Vesta Trading. Артикул 15100Z не найден в нашем каталоге. Возможно, вы имели в виду другой артикул или модель. Уточните, пожалуйста, название товара, его назначение или характеристики — я помогу найти подходящий вариант. Если это труба, насос, кран или другой элемент — укажите материал, диаметр, давление, температуру и область применения. Или просто скажите, что именно вам нужно — я подберу по вашему запросу. Готов помочь — просто уточните детали.

Products: `[]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "WRONG_SKU", "reason": "None of expected related SKUs present: ['151002']", "severity": "FAIL"}]}`

USER: Исправляю: точный артикул 151002

BOT: Нашёл подходящие варианты: 1. Водонагреватель THERMEX MK 50 V Артикул: 151002 Бренд: Thermex Цена: 13160 RUB Наличие: в наличии, 8 шт. Характеристики: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2 Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/ Перед покупкой сверьте способ монтажа, подвод воды, электропитание или источник нагрева с паспортом этой модели.

Products: `[{"sku": "151002", "name": "Водонагреватель THERMEX MK 50 V", "price": 13160.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/", "image_url": "https://www.vestatrade.ru/image/catalog/product/import/tcjs3p6ay22r1mo3vlvzd16dnlshawgk.png"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Какая у него основная характеристика по карточке?

BOT: Водонагреватель THERMEX MK 50 V. Артикул: 151002. Основные данные: объем бака, л: 50; тип водонагревателя: Накопительный; вид нагрева: Электрический; монтаж: Настенный; мощность, квт: 2. Ссылка: https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/

Products: `[{"sku": "151002", "name": "Водонагреватель THERMEX MK 50 V", "price": 13160.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodonagrevateli/vodonagrevateli-elektricheskie-nakopitelnye/vodonagrevateli-elektricheskie-nakopitelnye-thermex/vodonagrevatel-thermex-mk-50-v/", "image_url": "https://www.vestatrade.ru/image/catalog/product/import/tcjs3p6ay22r1mo3vlvzd16dnlshawgk.png"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

### C08: Канализация 50 против 110

Session: `qa-20260821-002452-C08-3167be24`

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

### C10: Дешевле

Session: `qa-20260821-002452-C10-6d815f08`

USER: Покажи два шаровых крана 1/2 ВР-ВР для воды

BOT: Нашёл подходящие варианты: 1. Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн. Артикул: VT.217.N.04 Бренд: VALTEC Цена: 452 RUB Наличие: в наличии, 76 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/ 2. Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2" Артикул: VT.217S.N.04 Бренд: VALTEC Цена: 485 RUB Наличие: в наличии, 5 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/ 3. Кран шаровой MINI 1/2" вн.-вн. Артикул: VT.330.N.04 Бренд: VALTEC Цена: 502 RUB Наличие: в наличии, 3 шт. Характеристики: диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Мини Ссылка: https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-vn/ Могу сравнить эти варианты по главным отличиям для вашей задачи.

Products: `[{"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.217S.N.04", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2\"", "price": 485.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.217S.N/VT.217S.N-1.png"}, {"sku": "VT.330.N.04", "name": "Кран шаровой MINI 1/2\" вн.-вн.", "price": 502.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/41.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Чем отличаются эти варианты?

BOT: Сравниваю показанные варианты по карточкам товаров: - VT.217.N.04 — Кран шаровой BASE, рукоятка бабочка 1/2" вн.-вн.: цена 452 RUB; наличие: в наличии, 76 шт.; диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Бабочка - VT.217S.N.04 — Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2": цена 485 RUB; наличие: в наличии, 5 шт.; диаметр подключения, дюйм: 1/2; тип резьбы: Внутренняя; тип ручки: Бабочка - VT.330.N.04 — Кран шаровой MINI 1/2" вн.-вн.: цена 502 RUB; наличие: в наличии, 3 шт.; диаметр подключения, дюйм: 1/2; тип резьбы: С внутренней резьбой (ff); тип ручки: Мини Главное отличие — тип ручки: Бабочка против Бабочка против Мини. Если опишете вашу систему, порекомендую один вариант.

Products: `[{"sku": "VT.217.N.04", "name": "Кран шаровой BASE, рукоятка бабочка 1/2\" вн.-вн.", "price": 452.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-base-rukoyatka-babochka-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/36.jpg"}, {"sku": "VT.217S.N.04", "name": "Кран шаровой BASE-ГОСТ полнопроходной с рукояткой-бабочкой вн.-вн. DN15 PN40 1/2\"", "price": 485.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base-gost/kran-sharovoj-base-gost-polnoprohodnoj-s-rukojatkoj-babochkoj-vn-vn-dn15-pn40-12/", "image_url": "https://www.vestatrade.ru/image/catalog/product/Valtek/VT.217S.N/VT.217S.N-1.png"}, {"sku": "VT.330.N.04", "name": "Кран шаровой MINI 1/2\" вн.-вн.", "price": 502.0, "currency": "RUB", "stock_status": "в наличии", "url": "https://www.vestatrade.ru/vodozapornaya-armatura-2/krany-sharovye/krany-sharovye-valtec-base/kran-shar-mini-12-vn-vn/", "image_url": "https://www.vestatrade.ru/image/catalog/krani_i_klapani/41.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Какой дешевле? Назовите его точный артикул.

BOT: Более дешёвых подходящих вариантов в текущем ассортименте не вижу. Последний подходящий вариант: VT.217.N.04, VT.217S.N.04, VT.330.N.04. Могу показать аналоги или передать вопрос менеджеру.

Products: `[]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 1, "constraints": 1, "context": 1, "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "WRONG_SKU", "reason": "Cheapest comparison does not explicitly identify one of the cheapest SKU(s): VT.217.N.04", "severity": "FAIL"}]}`

### C13: Опечатка термоголовка

Session: `qa-20260821-002452-C13-4fec4053`

USER: термогаловка на батарею 1/2

BOT: Подскажите для радиатора: прямое или угловое подключение; регулировать температуру (термоголовка) или просто перекрывать поток.

Products: `[]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": 0, "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "BAD_CLARIFICATION", "reason": "Incomplete first request produced products/no critical clarification", "severity": "FAIL"}]}`

USER: Нужна вместе с клапаном.

BOT: Подскажите для радиатора: прямое или угловое подключение; размер 1/2 или 3/4; регулировать температуру (термоголовка) или просто перекрывать поток.

Products: `[]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Клапан прямой.

BOT: Подскажите для радиатора: размер 1/2 или 3/4; регулировать температуру (термоголовка) или просто перекрывать поток.

Products: `[]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

### C23: Возврат к предыдущему SKU

Session: `qa-20260821-002452-C23-03f5f8b7`

USER: Покажи 100013611

BOT: Важно: у всех найденных позиций сейчас нет подтверждённого положительного остатка. Показываю их справочно; для покупки нужно подобрать доступный аналог или уточнить поставку у менеджера. Нашёл подходящие варианты: 1. Муфта комбинированная разъёмная ВР 25-1" (белый) РОСТерм Артикул: 100013611 Бренд: РОСТерм Цена: 386 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Американка; диаметр (мм): 25; присоединительная резьба, дюйм: 1 Ссылка: https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-dlya-polipropilenovyh-trub-rosterm/mufta-kombinirovannaja-razemnaja-vr-25-1-belyj-rosterm/ Могу показать сопоставимые аналоги. Фитинги должны совпадать с типом системы и обоими размерами перехода.

Products: `[{"sku": "100013611", "name": "Муфта комбинированная разъёмная ВР 25-1\" (белый) РОСТерм", "price": 386.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-dlya-polipropilenovyh-trub-rosterm/mufta-kombinirovannaja-razemnaja-vr-25-1-belyj-rosterm/", "image_url": "https://www.vestatrade.ru/image/catalog/product/ROSTerm/100013611/100013611_1.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Теперь покажи 100013619

BOT: Важно: у всех найденных позиций сейчас нет подтверждённого положительного остатка. Показываю их справочно; для покупки нужно подобрать доступный аналог или уточнить поставку у менеджера. Нашёл подходящие варианты: 1. Муфта комбинированная разъёмная ВР 40-11/4" (белый) РОСТерм Артикул: 100013619 Бренд: РОСТерм Цена: 648 RUB Наличие: нет в наличии, 0 шт. Характеристики: тип товара: Американка; диаметр (мм): 40; присоединительная резьба, дюйм: 1 1/4 Ссылка: https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-dlya-polipropilenovyh-trub-rosterm/mufta-kombinirovannaja-razemnaja-vr-40-114-belyj-rosterm/ Могу показать сопоставимые аналоги.

Products: `[{"sku": "100013619", "name": "Муфта комбинированная разъёмная ВР 40-11/4\" (белый) РОСТерм", "price": 648.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-dlya-polipropilenovyh-trub-rosterm/mufta-kombinirovannaja-razemnaja-vr-40-114-belyj-rosterm/", "image_url": "https://www.vestatrade.ru/image/catalog/product/ROSTerm/100013619/100013619_1.jpg"}]`

Assessment: `{"status": "PASS", "metrics": {"retrieval": 1, "factuality": 1, "constraints": 1, "context": "N/A", "clarification": "N/A", "hallucination": 1, "overall": "PASS"}, "issues": []}`

USER: Вернемся к первому товару. Какой у него артикул?

BOT: Муфта комбинированная разъёмная ВР 40-11/4" (белый) РОСТерм. Артикул: 100013619.

Products: `[{"sku": "100013619", "name": "Муфта комбинированная разъёмная ВР 40-11/4\" (белый) РОСТерм", "price": 648.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-dlya-polipropilenovyh-trub-rosterm/mufta-kombinirovannaja-razemnaja-vr-40-114-belyj-rosterm/", "image_url": "https://www.vestatrade.ru/image/catalog/product/ROSTerm/100013619/100013619_1.jpg"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 0, "constraints": 1, "context": 0, "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "WRONG_SKU", "reason": "Expected SKU(s) not returned/named: ['100013611']; got=['100013619']", "severity": "FAIL"}, {"code": "CONTEXT_LOSS", "reason": "Contextual referent lost: ['100013611']", "severity": "FAIL"}, {"code": "WRONG_ATTRIBUTE", "reason": "Expected grounded value absent from answer: 100013611", "severity": "FAIL"}]}`

USER: А цена у первого какая?

BOT: По уже показанной подборке цены на фитинги: - Муфта комбинированная разъёмная ВР 40-11/4" (белый) РОСТерм, арт. 100013619: 648 RUB; нет в наличии. Монтаж, доставка и дополнительные комплектующие в эту цену не входят.

Products: `[{"sku": "100013619", "name": "Муфта комбинированная разъёмная ВР 40-11/4\" (белый) РОСТерм", "price": 648.0, "currency": "RUB", "stock_status": "нет в наличии", "url": "https://www.vestatrade.ru/fitingi/fitingi-polipropilenovye/fitingi-dlya-polipropilenovyh-trub-rosterm/mufta-kombinirovannaja-razemnaja-vr-40-114-belyj-rosterm/", "image_url": "https://www.vestatrade.ru/image/catalog/product/ROSTerm/100013619/100013619_1.jpg"}]`

Assessment: `{"status": "FAIL", "metrics": {"retrieval": 0, "factuality": 1, "constraints": 1, "context": 0, "clarification": "N/A", "hallucination": 1, "overall": "FAIL"}, "issues": [{"code": "WRONG_SKU", "reason": "Expected SKU(s) not returned/named: ['100013611']; got=['100013619']", "severity": "FAIL"}, {"code": "CONTEXT_LOSS", "reason": "Contextual referent lost: ['100013611']", "severity": "FAIL"}]}`

## Ограничения интерпретации

- Цена и остаток сверяются с локальным XML, но расхождение получает `UNVERIFIED_DYNAMIC_DATA`, а не автоматический FAIL.
- Публичный `/chat` не раскрывает полный hidden retrieval candidate set. Runner различает retrieval/selection и final answer по карточкам, debug agent trace и статическим ограничениям; невидимые отброшенные candidates независимо проверить нельзя.
- Session state хранится в памяти процесса; свежая/потерянная session проверяется на fail-closed, но автоматический restart сервера runner не выполняет.
- URL товаров сравниваются только как строки и никогда не открываются.
