# V2 selection → verified widget cards: итоговый отчёт

Дата: 28.08.2026, Europe/Moscow.

## Решение

- **Ограниченный этап: ACCEPT.** Целевые однокатегорийные selection-сценарии прошли 3/3, карточки доставляет V2, customer-visible scope сохраняется, следующий ordinal direct-fact ход выполняется V2 без Legacy setup.
- **Публичный rollout: BLOCK.** V2 остаётся только в Shadow и защищённом Preview. Публичные routing/live/canary-флаги не включались, canary остаётся 0%.

## Зафиксированный baseline

- commit: `69475ad11bba07cda4584ff5819890d3c0c117eb`;
- ветка: `qa/live-evaluation-and-fixes-2026-08-22`;
- рабочее дерево уже было изменено до этапа; reset/checkout/commit/push не выполнялись;
- feed: `data/feed_showcase_100_2026-06-14.xml`, 100 товаров;
- SHA-256 feed: `81ed35da3a188c88d5f000bb7d6df9c02c562047616f97f88d36ea6046a9384f`;
- паспортов: 24 PDF;
- aggregate SHA-256 паспортов: `aa9d96c55cd7dbcc20b0315582d25b9665cf22249c975f1efd93873c4535ebad`;
- runtime catalog/source revision: `ba3eebcd8c8023fb2ee4010f07ce0d91c45668c10869a308d5b2a83cabe7255d`;
- LLM: OpenRouter, `qwen/qwen3-vl-8b-instruct`;
- embeddings: `openai/text-embedding-3-small`;
- публичные V2 routing/live/canary-флаги: выключены; canary: 0%;
- исходный полный pytest: `52 failed, 2571 passed, 66 skipped`.

## Архитектурная причина исходного дефекта

До этапа V2 уже умела интерпретировать ход, вести состояние, разрешать product contract и иногда строить каталоговый план. Но между каталоговым планом и пользовательским ответом не было законченного типизированного контракта доставки:

1. `show/recommend` мог превратиться в очередной required-slot question, даже когда пользователь явно просил показать варианты.
2. Кандидаты каталога не проходили отдельный outcome-gate и не становились гарантированно проверенными `ChatResponse.products`.
3. Renderer мог сформировать обещание показать или сравнить товары без самих карточек.
4. Доставленные V2-карточки не имели атомарного общего пути в customer-visible `last_products`, V2 scope и product focus.
5. В семантическом слое отсутствовали несколько ограниченных канонизаций: `радиаторная магистраль`, `ППР`, `вн-вн/ВР-ВР`, бытовой канализационный контекст.
6. Exact/partial SKU и точное полное имя могли проиграть устаревшему `product_kind`.

То есть проблема была не в отсутствии второго поиска, а в незавершённом шве `semantic state → catalog_v2 → answer/cutover → visible widget state`.

## Реализованный общий шов

Реализован путь:

`V2 selection request → canonical facts/provenance → contract/readiness → существующий catalog_v2/source snapshot → typed selection result → outcome-gate → ChatResponse.products → atomic visible-state commit`

Ключевые свойства:

- добавлены типизированные `SelectionRequest` и `SelectionResult` со статусами, task/goal/contract ID, фактами, provenance, hard/soft constraints, filters, relaxations/rejections, ordered SKU, полными карточками, revision и reason codes;
- используется существующий catalog snapshot, normalization, planner и ranking; второй каталог, индекс или Legacy Response Composer не создавались;
- явная команда `Покажите варианты` становится selection control и имеет приоритет над очередным некритичным вопросом анкеты;
- добавлены только ограниченные детерминированные канонизации целевых формулировок, без неограниченного fuzzy-поиска;
- явный exact/unique partial SKU имеет приоритет над устаревшим goal/product-kind;
- полное имя товара разрешается только строгим однозначным совпадением;
- outcome-gate разрешает доставку только для `shown`, предметного `no_match` или одного критичного `need_clarification`;
- каждая карточка повторно сверяется с source snapshot по SKU, цене, валюте, наличию, URL и image URL;
- renderer представляет уже проверенный результат и не ищет товары самостоятельно;
- только фактически доставленные Preview-карточки атомарно обновляют `last_products`, V2 scope, selection ID и product focus;
- Shadow-кандидат не меняет Legacy answer/state; отклонённый кандидат не оставляет карточки;
- ранее реализованный `ProductFactEvidenceService` переиспользуется для следующего direct-fact хода.

## Файлы этапа

Основной код:

- `app/catalog_v2/contracts.py` — typed selection request/result;
- `app/catalog_v2/selection.py` — сборка запроса/результата, exact-name binding, outcome-gate;
- `app/catalog_v2/registry.py` — приоритет однозначного explicit SKU над stale goal;
- `app/agents/semantic_interpreter.py` — ограниченные канонизации и explicit show control;
- `app/dialogue_v2/controller.py` — binding exact named product и catalog-aware contract resolution;
- `app/cutover_v2/contracts.py` — selection request/result в кандидате;
- `app/cutover_v2/assembler.py` — общий selection seam и блокировка при провале gate;
- `app/agents/orchestrator.py` — передача context, телеметрия и атомарный commit customer-visible scope.

Тесты и QA:

- `tests/test_v2_selection_characterization.py` — новые characterization/unit/integration сценарии;
- `tests/test_cutover_v2_integration.py` — усиленная проверка Shadow isolation;
- `scripts/run_widget_selection_gate.py` — реальный selection gate через `/chat`;
- каталог `reports/widget_selection_v2_2026-08-28/` — новые артефакты, baseline предыдущих этапов не изменялся.

## Тесты

### Characterization и интеграция

До реализации целевая выборка фиксировала 4 падения при 1 проходе: explicit show, PPR purpose, `вн-вн` и sewer context не доходили до корректного V2 selection outcome.

После реализации:

- selection/cutover/product-fact/SKU focused suite: `104 passed`;
- более широкие профильные прогоны этапа также проходили без новых ошибок;
- финальный полный pytest: `52 failed, 2585 passed, 66 skipped`;
- относительно baseline новых падений нет: те же 52 исторические ошибки, +14 новых успешных тестов.

### Целевой живой V2 selection gate

Настоящий `/chat`, OpenRouter, feed100, отдельные session/client turn ID, 3 повтора каждого сценария:

- 18/18 прогонов;
- 162/162 проверок;
- ошибок: 0;
- P50: 6.98 с;
- P95: 11.17 с.

Стабильный ordered SKU:

| Сценарий | Результат 3/3 |
|---|---|
| pump | `2459900, 53843, 9168934, VRS.254.18.0, VRS.256.13.0` |
| PPR | `VTp.700.FB20.25` |
| BASE ВР/ВР | `VT.217.N.04, VT.214.N.04, VT.245.N.04` |
| named product | `VRS.254.18.0` |
| insufficient | один `pipe_service` question, без карточек |
| sewer | только sewer SKUs, без PPR |

Отдельный targeted modes gate для Legacy/Shadow: 12/12 прогонов, 24/24 проверок. Shadow visible owner — Legacy, visible state не обновлялся.

### Регрессия принятого product-fact этапа

Повторный реальный gate после selection-изменений:

- 18/18 сценариев;
- 120/120 проверок;
- `VRS.254.18.0 → 180 мм`: 3/3;
- PP-FIBER pressure predicate → 6 бар: 3/3;
- boiler power rationale без цитаты о давлении газа: 3/3;
- `VT.1500 → VT.1500.0.0`: 3/3;
- unknown fact без выдуманного значения: 3/3;
- ambiguous product без глобального паспортного поиска: 3/3.

## Полные persona-матрицы

Выполнены три независимые матрицы по 93 хода каждая (`legacy`, `shadow`, `v2_preview`), первая — сразу после холодного старта приложения.

| Матрица | HTTP | Preview V2 owner | Preview Legacy fallback | V2 card-delivery turns | Preview P50 | Preview P95 |
|---|---:|---:|---:|---:|---:|---:|
| run 1 cold | 93/93 | 25 | 6 | 8 | 8.245 с | 15.985 с |
| run 2 | 93/93 | 24 | 7 | 10 | 8.394 с | 19.320 с |
| run 3 | 93/93 | 23 | 8 | 9 | 8.800 с | 15.917 с |
| **всего/aggregate** | **279/279** | **72** | **21** | **27** | **8.394 с** | **17.592 с** |

Целевые ordered SKU были одинаковыми во всех трёх полных Preview-прогонах:

- pump: `2459900, 53843, 9168934, VRS.254.18.0, VRS.256.13.0`;
- PPR: `VTp.700.FB20.25`;
- BASE ВР/ВР: `VT.217.N.04, VT.214.N.04, VT.245.N.04`;
- external sewer show: `220010, 1491056`;
- ordinal fact после pump cards: первая карточка `2459900`, owner `v2`, 3/3.

В целевых сценариях:

- повторных вопросов о уже подтверждённых `pipe_service` и `connection_pattern`: 0;
- неправильных PPR-карточек в sewer show: 0;
- selection-заглушек вместо карточек: 0;
- Legacy fallback не засчитывался как V2 success.

Сравнение, quantity/cost, комплекты и multi-category ходы в полной матрице всё ещё дают Legacy fallback или V2-заглушки; они не считались успехом и остаются вне этого этапа.

## Телеметрические доказательства

По всем собранным новым selection events:

- selection events: 130;
- `shown`: 57;
- Shadow visible-state updates: 0;
- non-shown/failed phantom state updates: 0;
- `shown` с проваленным outcome-gate: 0;
- карточек с SKU вне source snapshot: 0;
- расхождений price/currency/stock/URL/image URL с snapshot: 0.

Пример UI/Preview selection delivery:

- owner: `v2`;
- action: explicit show;
- category/product kind/contract: `pumps / circulation_pump / pump.circulation.v1`;
- hard facts: `duty_point_flow_l_h=1500`, `duty_point_head_m=4`;
- candidates before/after filters: `8/8`;
- ordered SKU: `2459900, 53843, 9168934, VRS.254.18.0, VRS.256.13.0`;
- status: `shown`;
- outcome gate: passed;
- reason: `verified_cards_delivered`;
- customer-visible state updated: true;
- catalog/source revision: `ba3eebcd8c8023fb2ee4010f07ce0d91c45668c10869a308d5b2a83cabe7255d`.

Следующий UI ordinal direct-fact ход:

- owner: `v2`;
- product reference kind: `ordinal`, raw reference: `1`;
- canonical SKU: `2459900`;
- predicate: `installation_length_mm`;
- embedding model: `openai/text-embedding-3-small`;
- vector index present, 1393 chunks;
- document scope/source: `Циркуляционные_насосы_Wilo_Star_RS_с_мокрым_ротором.pdf`, пункт 5.2;
- verifier: accepted;
- evidence gate: accepted;
- evidence: `Монтажная длина 130 мм / 180 мм`;
- delivered value: `130–180 мм`.

## UI-smoke настоящего виджета

- публичный `/widget-demo` отправил запрос без QA mode и получил Legacy-ответ;
- protected local Preview показал 5 карточек насоса с ценой, наличием, URL и изображением;
- следующий вопрос `Какая у первого монтажная длина?` разрешился в реально первую карточку `2459900`;
- ответ: `130–180 мм`, паспорт Wilo, пункт 5.2;
- console/runtime errors: 0;
- неверный QA-токен с `qa_mode=v2_preview`: HTTP 403;
- временная QA-страница после smoke удалена.

## До / после

| Сценарий | До | После в защищённом Preview |
|---|---|---|
| Pump show | план/обещание либо Legacy cards | V2 cards + stable ordered SKU |
| Pump ordinal | требовал Legacy setup или мог вернуться в funnel | V2 cards → ordinal → V2 evidence, 3/3 |
| PPR heating | повторный вопрос «для чего труба?» | `heating` сохранён; точный `VTp.700.FB20.25` |
| BASE ВР/ВР | V2 теряла connection pattern | только internal/internal SKU |
| Sewer бытовой | риск PPR-карточек | sewer contract, без PPR |
| Bare «Нужна труба» | случайный/повторный путь | один `pipe_service` question, без карточек |
| Exact full name | мог конфликтовать с designation facts | строго одна карточка или ambiguity |
| Shadow | риск скрытой мутации scope | 0 visible-state updates |

## Оставшиеся P0/P1

### Для ограниченного этапа

P0 не осталось: все обязательные targeted criteria пройдены.

P1:

1. Preview P95 по трём полным матрицам — 17.592 с; Shadow в одном прогоне достиг P95 24.947 с.
2. Семантическая анкета всё ещё отклоняется на 3–6 из 31 ходов; Preview использовал Legacy fallback на 6–8 ходах из 31.
3. Рабочая точка насоса применяется как customer constraint, но feed не доказывает гидравлическую кривую для каждого кандидата; карточки честно помечаются предварительными. Инженерное подтверждение кривой — отдельный evidence-этап.
4. Текст ответа дублирует данные, которые затем отображаются widget cards; функционально верно, но UX можно сократить.
5. В одном внецелевом PPR/fitting сценарии explicit show разрешает предварительные трубы при неизвестном назначении; policy для таких промежуточных запросов нужно уточнить отдельным этапом.

### Блокеры публичного rollout

1. Полноценное сравнение всё ещё не реализовано: на compare-ходах возможны обещания/заглушки.
2. Quantity/cost, compatibility, комплекты, котельная и multi-category solution plan остаются незавершёнными.
3. Радиаторный сценарий и часть прямых вопросов всё ещё уходят в Legacy fallback.
4. Нужна отдельная работа по снижению semantic rejection rate и P95 latency.
5. До публичной канарейки нужен новый release gate уже для следующего функционального этапа, а не расширение текущего scope задним числом.

## Финальный вывод

Ограниченный этап можно принять: V2 теперь имеет собственный, проверенный и типизированный путь от достаточно определённого однокатегорийного запроса до настоящих карточек виджета и следующего direct-fact хода. Это не означает готовность всего V2 или публичного переключения.

V2 оставлена только в Shadow и защищённом Preview. Публичный Legacy-маршрут, публичная канарейка, feed, паспорта и секреты не изменялись.
