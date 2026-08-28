# Semantic/paraphrase/state hardening V2 — итоговый отчёт

Дата: 2026-08-28.

## Решение

- **Semantic hardening: `ACCEPT` для ограниченного этапа с зафиксированным отклонением от первоначального протокола приёмки.** По решению владельца проекта финальная полная paraphrase-матрица выполнялась один раз, без повторов. Она прошла 36/37; единственный сбой был локализован, исправлен и затем проверен отдельным живым сценарием 1/1: все 4 хода принадлежат V2, карточки относятся только к наружной канализации.
- **Публичный rollout: `BLOCK`.** Сравнение, расчёт количества/стоимости, полноценное обоснование, совместимость и project/комплектные сценарии ещё не реализованы. Доля Legacy-fallback в широкой persona-матрице также пока ненулевая.
- Публичный `/chat` остаётся Legacy; canary остаётся `0%`; новый semantic path доступен только в Shadow и защищённом Preview.
- `commit` и `push` не выполнялись.

`ACCEPT` относится только к semantic/paraphrase/state seam и не означает готовность всего V2 к публичному трафику.

## Исходная точка и неизменяемые данные

- HEAD: `ab5f4cc12c03be55d42adaeccd9a99ea0cd74d5a`.
- Ветка: `qa/live-evaluation-and-fixes-2026-08-22`.
- Существующие пользовательские изменения и QA-артефакты не перезаписывались.
- Публичные V2-флаги по умолчанию выключены; canary: `0%`; Preview защищён QA-токеном.
- Semantic и Passport LLM: `qwen/qwen3-vl-8b-instruct` через OpenRouter.
- Semantic prompt: `turn-understanding-v1.19`.
- Embedding: `openai/text-embedding-3-small`.
- Feed: `data/feed_showcase_100_2026-06-14.xml`, 100 товаров, SHA-256 `81ed35da3a188c88d5f000bb7d6df9c02c562047616f97f88d36ea6046a9384f`.
- Паспортов: 24 PDF; digest `aa9d96c55cd7dbcc20b0315582d25b9665cf22249c975f1efd93873c4535ebad`.
- Source revision: `ba3eebcd8c8023fb2ee4010f07ce0d91c45668c10869a308d5b2a83cabe7255d`.
- Registry version: `e752529500d2002cdf5b3e70be9ac9eb09f9968c834e205d995c4a5012c5be51`.
- Baseline pytest: `52 failed, 2585 passed, 66 skipped`.
- Исходный ручной paraphrase-аудит: 8/33 полностью корректных, 5/33 частичных, 20/33 неправильных либо с Legacy-fallback.

Полная исходная фиксация: `baseline.md` в этом каталоге.

## Архитектурная причина

Память V2 и reducer уже умели хранить принятые типизированные факты. Ошибки происходили раньше:

1. LLM нестабильно определяла сущность, action, predicate, число или единицу.
2. Разговорные формулировки, опечатки и словесные числа не имели высокоточных anchors.
3. Контекст применения мог подменить товар: «радиаторная разводка» превращала PPR-трубу в радиатор.
4. Короткая реплика могла не связаться с активной задачей или pending question.
5. Один неверный semantic field мог привести к потере остальных корректных фактов.
6. Старый paraphrase-runner проверял конечную карточку, но мог не заметить Legacy на промежуточном ходе или потерянную температуру.

Поэтому исправлен слой понимания и применения delta, а не ProductFact, каталог или renderer.

## Реализованный совместимый seam

Фактический путь:

`реплика + DialogueStateV2`
→ существующий LLM SemanticInterpreter и audit
→ детерминированные anchors и field-level repair
→ `SemanticTurnDeltaV1`
→ semantic-gate
→ адаптер к существующему `TurnUnderstanding`
→ существующий reducer/controller
→ существующий planner
→ существующие ProductFact или Selection.

Новый слой не ищет товары, не строит карточки, не читает паспорта и не формирует пользовательский текст.

Без ослабления сохранены:

- `ProductFactEvidenceService`, passport/embedding retrieval и verifier/evidence-gate;
- exact/partial SKU resolver;
- существующие каталог, ranking, source snapshot и `SelectionResult`;
- Selection outcome-gate и delivery/state gate;
- customer-visible product scope;
- cutover policy, Shadow isolation и защищённый Preview;
- публичное Legacy-поведение.

Вторые state, router, каталог, ranking, Passport Agent или индекс не создавались.

## SemanticTurnDeltaV1

Добавлен frozen/versioned контракт `semantic-turn-delta-v1`:

- metadata хода, session и registry version;
- `accepted/partial/ambiguous/rejected`;
- action candidates с confidence, evidence и downstream action;
- entity mentions с ролью, категорией, product kind и resolved reference;
- fact updates с `add/correct/retract`, raw/canonical value и unit, provenance и source turn;
- product references для exact/partial SKU, ordinal/deictic/current focus;
- relations, unresolved fragments, ambiguities, repair и rejection reason codes;
- отдельный `SemanticGateResult`.

Контракт допускает несколько сущностей. `COMPARE`, `CALCULATE`, `RATIONALE`, `COMPATIBILITY` и `PROJECT` сохраняются как отдельные actions и больше не подменяются Selection. Содержательное выполнение этих capability в этап не входило.

## Registry, anchors и канонизация

Расширен существующий `domain_ontology`, а не создан параллельный словарь. Из одного версионированного payload используются:

- product aliases;
- category/product-kind mappings;
- predicate и fact aliases;
- closed values и units;
- action aliases;
- registry digest.

Поддержаны целевые anchors:

- PPR/«ппэровская», «со стеклом», «на батареи», «радиаторная разводка»;
- канализационный сленг, наружный контекст и словесный размер «сто десятый»;
- «циркуляционник», «полтора куба», `Q=1.5`, `H=4`;
- «двадцать пятая» в подтверждённом трубном контексте;
- `G1/2`, `DN15`, `ВР/ВР`, «обе резьбы внутренние»;
- exact/partial SKU;
- ordinal/deictic references;
- варианты «монтажная длина», «по монтажу», «между присоединениями».

Канонизация product kind теперь использует registry и устраняет несовместимые варианты вроде `sewer pipe`/`sewer_pipe`, не добавляя fuzzy resolver.

## Semantic-gate и безопасный merge

Semantic-gate проверяет:

- учёт высокоточных anchors;
- evidence текущей реплики;
- допустимость canonical value/unit;
- согласованность category и product kind;
- binding факта к сущности;
- ordinal только по реально показанным карточкам;
- отсутствие выдуманных значений и ложных ограничений;
- сохранение unsupported future actions без подмены Selection.

Правила state merge:

- отсутствующий факт не удаляет сохранённый;
- новый факт нужен с evidence и успешной валидацией;
- конфликт не перетирает старое значение молча;
- rejected delta не изменяет состояние;
- короткий факт связывается с активной typed task/goal только по ограниченному предметному правилу;
- customer-visible product scope меняется только после реальной доставки;
- Shadow не меняет live/Legacy state;
- неверный product binding отделяется без выбора другого товара;
- слишком короткое evidence неизвестного параметра может быть расширено только точным фрагментом predicate из той же текущей реплики.

Новый дополнительный LLM repair-вызов не добавлялся. Исправления детерминированы и фиксируются reason codes; при непроходимом gate остаётся безопасный fallback.

## Исправления самого QA-gate

Runner теперь проверяет каждый целевой ход, а не только финальный ответ:

- корректный `ball_valve` не считается ошибкой;
- «Нужна ППР» не проходит при категории насоса;
- температура и остальные заданные факты обязательны, даже если карточка верная;
- ProductFact paraphrases используют один гарантированно рабочий setup;
- проверяются owner, fallback, state before/delta/after, provenance и source turn;
- промежуточный Legacy больше не маскируется успешной финальной карточкой;
- для наружной канализации проверяются scope, применённый факт и только допустимые SKU;
- словесный размер `110` проверяется в canonical state и применённых фильтрах.

Именно усиленный gate обнаружил скрытый Legacy-fallback, которого не показывали прежние 37/37.

## Изменённые файлы

- `app/agents/domain_ontology.py`;
- `app/agents/semantic_interpreter.py`;
- `app/semantic_v2/contracts.py`;
- `app/semantic_v2/bridge.py`;
- `app/semantic_v2/__init__.py`;
- `app/dialogue_v2/controller.py`;
- `app/agents/orchestrator.py`;
- `app/catalog_v2/selection.py`;
- `app/product_fact_evidence.py`;
- `scripts/run_widget_paraphrase_gate.py`;
- `scripts/run_widget_semantic_holdout.py`;
- `scripts/run_widget_product_fact_gate.py`;
- `scripts/run_widget_selection_gate.py`;
- `tests/test_semantic_hardening_v2.py`;
- `tests/test_v2_selection_characterization.py`.

Baseline-артефакты от предыдущих этапов не изменялись.

## Тесты и регрессионный паритет

Финальная локальная проверка semantic/contract/selection seam: `42 passed`.

Финальный полный pytest:

- `52 failed, 2610 passed, 66 skipped`, 129.54 с;
- baseline: `52 failed, 2585 passed, 66 skipped`;
- те же 52 исторических failure node IDs;
- новых регрессий: 0;
- успешных тестов относительно baseline: +25.

Две временно выявленные несовместимости старого semantic contract были исправлены: unknown mounting length снова сохраняется, а неверный product index безопасно отделяется. `git diff --check` проходит.

## ProductFact regression gate

Итог по режимам после точечного исправления Shadow: 18/18 сценариев.

- Preview: 6/6;
- Legacy: 6/6 — публичное поведение не изменено;
- Shadow: 6/6 — V2-кандидат рассчитан, видимый ответ остаётся Legacy.

Проверены:

- ordinal pump → V2, правильный насос и монтажная длина;
- PP-FIBER pressure → правильный predicate и `6 бар`;
- boiler rationale → без нерелевантной цитаты о давлении газа;
- partial SKU `VT.1500` → `VT.1500.0.0`, без недоказанного compatibility verdict;
- unknown fact → без выдуманного значения;
- ambiguous product → без глобального поиска по паспортам.

Артефакты: `acceptance_product_fact_all_modes/targeted_report.md` и `acceptance_product_fact_shadow_after_rationale_fix/targeted_report.md`.

## Selection regression gate

Все режимы: 18/18 сценариев, 78 проверок, 0 ошибок.

Preview доставляет ожидаемые карточки для PPR, насосов, BASE-кранов и наружной канализации; insufficient request не показывает случайные товары; named product разрешается строго. Legacy и Shadow сохраняют прежние правила видимого ответа и состояния.

P50/P95: 8.61/17.84 с по смешанным режимам.

Артефакт: `acceptance_selection_all_modes/report.md`.

## Единственная финальная строгая paraphrase-матрица

По решению владельца проекта полный строгий прогон выполнен один раз и не повторялся:

- 37 сценариев, 88 ходов;
- 36/37 полностью прошли;
- 496 проверок, 1 ошибка;
- P50/P95: 8.73/14.28 с;
- 87 ходов V2, 1 скрытый Legacy-fallback;
- неправильных финальных категорий и выдуманных значений нет.

Единственный дефект: первый ход `sewer_fragmented` был отклонён, потому что LLM предложила правильный тип наружной канализации, но evidence не было дословным фрагментом текущей реплики. Финальная карточка уже была правильной, однако усиленный gate справедливо не засчитал сценарий.

После узкого исправления выполнен только затронутый живой сценарий:

- 1/1 сценарий, 4/4 хода owner=`v2`;
- 13/13 проверок;
- semantic accepted 4/4;
- ordered SKU: `220010, 1491056`;
- P50/P95: 6.59/6.65 с;
- repair `product_evidence_rebound_to_current_message` подтверждён телеметрией.

Полная матрица после исправления **не повторялась по прямому указанию владельца проекта**. Поэтому итоговое `ACCEPT` использует один полный строгий прогон плюс точечное доказательство исправления единственного сбоя.

Артефакты:

- `acceptance_strict_paraphrase_single/report.md`;
- `acceptance_final_sewer_evidence_fix/report.md`.

Три более ранних прогона 37/37 подтверждают общую повторяемость, но не используются как строгая финальная приёмка: прежняя версия gate могла пропускать промежуточный Legacy.

## Holdout

Набор из 20 ранее не использованных сценариев прошёл 20/20 по структурным ожиданиям, но усиленный анализ обнаружил два промежуточных Legacy-fallback в коротких fragment turns. Они исправлены и проверены отдельно:

- BASE-факты по ходам: owner V2, корректный `female_female` и размер;
- бытовая наружная канализация со словесным «сто десятый»: owner V2, canonical `110 mm`, ordered SKU `220010, 1491056`.

Новый полный holdout не повторялся в соответствии с решением не делать повторные матрицы. Точечные артефакты: `acceptance_hidden_fallback_fix_holdout/report.md` и `acceptance_spoken_110_fix_holdout/report.md`.

## Широкая persona-матрица

До решения не повторять матрицы уже были выполнены три полных V2 Preview прогона по 31 ходу:

| Прогон | V2 owner | Legacy | P95 |
|---|---:|---:|---:|
| 1 | 27/31 | 4 | 23.7 с |
| 2 | 29/31 | 2 | 18.5 с |
| 3 | 28/31 | 3 | 15.8 с |

Они показывают, что принятые ProductFact/Selection сценарии работают, но широкая готовность ещё не достигнута. Основные остаточные случаи — неподдержанные Compare, Calculate, Compatibility, Project и некоторые multi-category/rationale переходы. Повторных persona-прогонов после указания пользователя не выполнялось.

## UI-smoke настоящего виджета

Через настоящую страницу виджета и настоящий `/chat` проверен защищённый Preview:

1. введён запрос циркуляционного насоса;
2. V2 показала реальные карточки;
3. следующий вопрос о первой карточке обработан V2 ProductFact;
4. ответ относится к фактически первой карточке и содержит доказанную монтажную длину `130–180 мм` из паспорта Wilo;
5. embedding-вызов успешен, verifier/evidence-gate принят;
6. ошибок и предупреждений console/runtime: 0.

Публичный режим при этом остался Legacy; неправильный QA-токен Preview не включает.

## Телеметрия и стоимость

Телеметрия отдельно фиксирует:

- LLM candidate и audit;
- deterministic anchors/repairs;
- `SemanticTurnDeltaV1` и semantic-gate;
- state before/delta/after;
- downstream action и настоящий response owner;
- selection candidates/outcome/delivery;
- passport/embedding/verifier events;
- fallback reason, latency, tokens и cost.

Для единственной строгой полной матрицы:

- 88 primary semantic completions;
- 88 audit completions;
- 6 PassportAnswerAgent completions;
- всего 182 LLM completion calls;
- измеренная стоимость: `$0.342035780`;
- средняя LLM latency: primary 4558.9 мс, audit 4308.0 мс, passport 2459.8 мс.

P95 матрицы 14.28 с против baseline 14.02 с: ухудшение около 1.9%, то есть внутри установленного лимита 10%. P50 8.73 с против 8.33 с: около 4.8%.

Секреты и QA-токены в отчёты не записывались.

## Before/after по исходным дефектам

| Дефект | Было | Стало |
|---|---|---|
| PPR 25/стекло/отопление/90 °C | терялись факты | все четыре факта в typed state |
| PPR по разным ходам | короткий ход стирал контекст | факты накапливаются монотонно |
| «ппэровская… на батареи» | Legacy/неверная категория | pipe + heating + diameter + reinforcement |
| «радиаторная разводка» | радиатор | применение отопления при товаре pipe |
| «полтора куба», «четыре метра» | терялись числа | 1500 l/h и 4 m |
| `Q=1.5`, `H=4` | нестабильно | те же canonical facts |
| BASE `G1/2 ВР/ВР` | терялось соединение | `1/2`, `female_female` |
| «обе резьбы внутренние» отдельным ходом | факт терялся | привязывается к активной задаче крана |
| канализационный сленг | PPR/Legacy | sewer scope либо предметное уточнение |
| «сто десятый» | строка и no-match | canonical 110 mm и правильные карточки |
| «по монтажу/между присоединениями» | не FACT | `installation_length_mm` |
| future actions | могли стать Selection | сохраняются отдельными action candidates |
| неверный optional field | мог обрушить ход | валидные facts сохраняются, bad binding отделяется |
| скрытый Legacy в gate | маскировался финальной карточкой | проверяется owner каждого целевого хода |

## Оставшиеся P0/P1

### P0 перед публичным rollout

1. Реализовать содержательный Compare вместо обещания сравнить.
2. Реализовать Calculate для количества и итоговой стоимости.
3. Реализовать Rationale для обоснования выбора/мощности на доказанных входных данных.
4. Реализовать Compatibility с доказательствами по обоим товарам.
5. Реализовать Project/комплекты и многокатегорийный план.
6. Снизить Legacy-fallback в широкой persona-матрице до согласованного порога.
7. Отдельно подтвердить производительность уже полного capability-набора.

### P1 для semantic слоя

1. Сократить постоянные два semantic LLM-вызова: audit/repair вызывать по reason-coded риску, не ослабляя gate.
2. Расширять aliases только через общий registry и holdout, без диалоговых regex-латок в renderer/planner.
3. Продолжить измерять короткие fragment turns и переключение между task/goal.
4. При необходимости добавить один узкий field-level LLM repair после конкретного gate failure и обязательный повторный gate.

## Что сознательно не сделано

- не реализованы Compare/Calculate/Rationale/Compatibility/Project;
- не создан новый каталог, ranking, Passport Agent или embedding index;
- не изменены feed, паспорта, секреты или production model;
- не ослаблены semantic/evidence/outcome/source/delivery gates;
- не изменён публичный `/chat`;
- не включён canary;
- не удалены Legacy или прежний SemanticInterpreter;
- не выполнены commit/push;
- после явного указания пользователя не выполнялись повторные полные матрицы.

## Финальный вывод

Этап semantic/paraphrase/state hardening завершён архитектурно совместимо. V2 лучше понимает целевые перефразирования, опечатки, разговорные числа и факты по нескольким ходам, а результат проходит versioned delta, semantic-gate и существующий reducer. ProductFact, Selection, паспортный/embedding-контур и customer-visible state не заменены и не ослаблены.

По скорректированному владельцем протоколу — один строгий полный прогон плюс точечная проверка единственного исправления — этап получает `ACCEPT`. Для публичного rollout решение остаётся безусловным `BLOCK`: semantic foundation готов, но бизнес-capability и широкая стабильность всего ассистента ещё не завершены.
