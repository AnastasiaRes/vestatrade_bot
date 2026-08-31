# V2 semantic continuity, capability boundary and targeted evidence completion

Дата: 2026-08-31
Исходный HEAD: `cc863d0` (`qa/live-evaluation-and-fixes-2026-08-22`)
Каталог: 100 товаров, feed SHA-256 `81ed35da3a188c88d5f000bb7d6df9c02c562047616f97f88d36ea6046a9384f`
Подключённые документы: 28 (проверено на временном QA-сервере).
Публичный маршрут не менялся; canary остаётся `0%`.

## Что переиспользовано

- Общие contracts/registry `catalog_v2`, существующий `DialogueStateV2` reducer и V2 planner — без второго состояния или поиска.
- Legacy-граница инженерного риска как формулировка и правило предметной безопасности; V2 использует собственный типизированный boundary, а не Legacy Response Composer.
- `ProductFactEvidenceService`, точный SKU resolver, passport retrieval, embeddings, verifier и document scope для `VT.5000.0.0`.
- Уже существующие outcome-gate и source snapshot validation. Исправлена только их трактовка для единственной точной карточки с нулевым остатком.

## Реализованные швы

1. **Канализация: контекстная длина.** В активной sewer-задаче `Длина 3 метра` детерминированно даёт `length_mm=3000`, `unit=mm`; единицы берутся из существующего registry conversion, а не из нового словаря.
2. **Радиаторная арматура.** `Прямая, 1/2, с термоголовкой` остаётся в одном активном radiator-valve goal. Факты `connection_size`, `valve_shape` и `thermostatic_head` связаны с той же задачей; фраза про головку больше не создаёт вторую конкурирующую цель.
3. **Котёл и неизвестные контуры.** `Количество контуров пока не знаю` записывает только typed `unknown` для `circuits`; `gas` и `24 kW` сохраняются. Для котла такое unknown не обещает несуществующие предварительные карточки: V2 объясняет решение «только отопление / ГВС».
4. **Точный товар без остатка.** Точная карточка без наличия допускается как честная выдача. Gate по-прежнему отклоняет смешение отсутствующего точного товара с in-stock availability analogue в одном selection scope. Renderer прямо сообщает об отсутствии.
5. **Гидравлический расчёт.** Явный расчёт сопротивления системы распознаётся как `PROJECT/hydraulic_system_calculation` до товарного Calculate. V2 отвечает границей компетенции, не спрашивает SKU, не считает цену и не придумывает метры/контуры.
6. **VT.5000.** Добавлены фразы predicate `thermostatic_head_thread`; единственный exact SKU + exact scoped document + verifier-approved quote `M30×1,5` разрешён как паспортное доказательство. Общее правило card/passport consensus для иных фактов не ослаблено.
7. **Preview transparency.** Для уже активной V2 задачи защищённый Preview может показать существующее проверенное детерминированное V2-уточнение вместо Legacy текста. Публичный и Shadow пути этим адаптером не пользуются.

## Проверка

Профильные регрессионные тесты:

```
335 passed
```

Набор включал semantic hardening, ProductFact evidence, Selection/readiness, Compare, Calculate, OfferFact, Compatibility, cutover delivery/policy и reducer policy.

Целевой живой прогон через настоящий `/chat`, `v2_preview`, реальную OpenRouter LLM, feed100 и 28 документов:

| Сценарий | Результат |
| --- | --- |
| Наружная канализация 50 мм → `Длина 3 метра` | V2 сохранила `3000 мм`; честный `no_match`, случайные карточки не показаны. |
| Радиаторная арматура → `Прямая, 1/2, с термоголовкой` | V2 сохранила размер, форму и требование компонента; спросила только посадочную резьбу. |
| Газовый котёл 24 кВт → контуры неизвестны | V2 сохранила газ и мощность, дала предметное объяснение выбора контура. |
| Затем двухконтурный + закрытая камера | V2 показала точный SKU `3636151`, цену и прямую URL; честно указала отсутствие в наличии. |
| `VT.5000`: посадочная резьба | V2 вернула `M30×1,5` с quote из `0962d51dab5c3219f584820a92d556aa.pdf`. |
| Гидравлическое сопротивление дома 250 м² | V2 объяснила недостающие инженерные данные; без цены, SKU и выдуманных чисел. |
| Насосы → Compare → первый | V2 показала 5 предварительных насосов, сравнила цену/диаметр/напор/монтажную длину и вернула для первого `VRS.254.18.0` доказанные `180 мм` из `VRS-0725.pdf`. |
| Неверный QA token | HTTP `403`; Preview не включился. |

## Изменённые зоны

- `app/agents/domain_ontology.py`
- `app/agents/semantic_interpreter.py`
- `app/semantic_v2/bridge.py`
- `app/dialogue_v2/seller_policy.py`
- `app/catalog_v2/selection.py`
- `app/cutover_v2/{contracts.py,engineering_boundary.py,preview_continuation.py,selection.py}`
- `app/agents/orchestrator.py`
- `app/product_fact_evidence.py`
- targeted QA runners и профильные тесты.

Пользовательское изменение `reports/widget_v2_regression_migration_2026-08-30/migration_map.md` не менялось и не является частью этапа.

## Решение

**V2 capability для этого ограниченного этапа: accept.** Целевые локальные разрывы закрыты без нового каталога, индекса, state или агента-продавца.

**Полная приёмка ТЗ и публичный rollout: block.** В этом этапе сознательно не повторялись полный persona-run/feed100 sweep, полный pytest baseline и latency evaluation. V2 остаётся только в Shadow и защищённом Preview; публичный `/chat` остаётся Legacy.

Остающиеся P1: latency реальных LLM ходов, более широкие paraphrase/series Compare, multi-category Project и общая Quote/смета.
