# Grounded V2 Compare: visible pair + passport evidence

Дата: 31.08.2026
Режим: локальный защищённый `v2_preview`; публичный `/chat` не менялся, `canary=0`.

## Почему возникло противоречие

В прежнем отчёте два разных шага были описаны как один:

1. `Compare` уже умел детерминированно сопоставлять цену, наличие и
   канонические факты карточек из frozen source snapshot.
2. Последующий `ProductFact` действительно обращался к паспорту для ответа о
   монтажной длине.

Это не означало, что `Compare` самостоятельно делал паспортный retrieval по
явно запрошенной технической характеристике. Кроме того, фраза «первые два»
не сужала scope и приводила к сравнению всей выдачи.

## Реализованный совместимый шов

```text
V2-delivered cards
→ exact visible SKU pair / plural ordinal
→ ComparisonRequest
→ frozen source snapshot
→ [only explicit technical predicate missing from snapshot]
  ProductFactEvidenceService.evaluate_exact_product(SKU, predicate)
→ existing exact resolver + passport retrieval + embeddings + verifier
→ ComparisonResult with document / section / quote
→ comparison source + outcome gates
→ deterministic renderer
```

Обычное «сравните их» не запускает паспортный поиск и остаётся быстрым
snapshot-сравнением. Passport fallback разрешён только для заранее
зарегистрированного и явно названного предиката (сейчас: посадочная резьба
термоголовки, объём расширительного бака, встроенный циркуляционный насос) и
только для уже показанных пользователю точных SKU.

Если хотя бы у одной выбранной позиции нет подтверждения нужной
характеристики, результат — `not_comparable`, а не «сравнение по цене вместо
запрошенной резьбы». Расхождение карточки и паспорта — `source_conflict`.

## Изменения

- `app/v2_visible_products.py` — «первые два», «две первые», диапазон
  «с первого по второй» теперь разрешаются строго внутри customer-visible
  scope; выражение не спутывается с количеством/единицей.
- `app/answer_v2/contracts.py`, `app/answer_v2/sources.py` — snapshot хранит
  model-scoped список документов карточки.
- `app/product_fact_evidence.py` — узкий read-only exact-SKU фасад к уже
  существующему доказательному passport-контуру.
- `app/comparison_v2/*`, `app/cutover_v2/comparison.py` — паспортные source
  references, `source_conflict`, обязательный source gate и честный renderer.
- `app/agents/orchestrator.py` — передаёт уже существующий
  `ProductFactEvidenceService` в Compare; второго агента, индекса и каталога
  нет.

## Проверки

### Контрактные и регрессионные тесты

- `269 passed`:
  visible scope, grounded Compare, ProductFact evidence, presentation,
  Compatibility, Calculate, goal-scoped context, scope lifecycle, reducer и
  cutover/Preview tests.
- Добавлены проверки:
  - plural ordinal даёт ровно две карточки;
  - explicit predicate вызывает evidence только по отсутствующим точным SKU;
  - generic Compare не вызывает passport evidence;
  - отсутствие evidence не маскируется различием цены;
  - source conflict доставляется как безопасный typed result;
  - число `8` не принимается за часть значения `18` в паспортной цитате.
- Полный `pytest`: `2837 passed`, `67 skipped`, `48 failed` за 148 с.
  Это существующие падения старой диалоговой логики за пределами данного
  Compare seam; новые профильные тесты зелёные. Полную приёмку всего проекта
  этот результат не объявляет.

### Живой `/chat` в защищённом Preview

С реальной semantic LLM и `baai/bge-m3`:

1. «Подберите циркуляционные насосы для отопления: расход 1,2 м³/ч, напор 5 м»
   → V2 показала пять предварительных насосов.
2. «Чем отличаются первые два?»
   → V2 сравнила ровно `VRS.254.18.0` и `VRS.256.18.0`, показав цену
   `3989 ₽ / 4186 ₽` и максимальный напор `4,2 м / 6 м`.

Пробная живая выдача термоголовок по `М30×1,5` не создала cards: selection
путь неверно счёл это жёстким фильтром без подтверждённого соответствия.
Это отдельный P1 selection/semantic issue; Compare его не скрывает и не
обходит. Passport fallback покрыт изолированным контрактным путём с теми же
gates, что использует настоящий `ProductFactEvidenceService`.

## Решение

`accept` для ограниченного Compare seam: да, для Preview/Shadow.

`block` для публичного rollout: да. Остаются отдельные проблемы selection
семантики, исторические Legacy failures и latency; этот этап их не расширял и
не исправлял.

## Дополнение: произвольные пары показанных карточек

После обратной связи добавлен общий typed `product_references` набор внутри
`ComparisonRequest`. Он поддерживает:

- любые словесные позиции до границы реально показанного scope: «первый и
  третий», «второй с четвёртым»;
- короткую нумерацию только в явном Compare-контексте: «сравни 1 и 4
  варианта»;
- комбинацию позиции с текущим focus: «первый и этот»;
- exact/unique partial SKU и уникально названную *уже показанную* карточку;
- source-spanned semantic reference candidate от LLM.

LLM не получает право выбирать SKU: её `text` и `evidence` должны быть
дословными фрагментами текущего сообщения, после чего candidate разрешается
только среди `customer-visible` SKU текущего selection scope. Неразрешённая
или выходящая за scope ссылка даёт один предметный вопрос; она не расширяется
до всей выдачи и не запускает поиск.

Semantic prompt обновлён до `turn-understanding-v1.23` с явным требованием
перечислять каждую ссылку в Compare и оставлять неоднозначность, а не
превращать «самый мощный» или предположение модели в номер карточки.

### Проверки дополнения

- `273 passed` в scoped V2 suite, включая Compare, visible scope, ProductFact,
  Compatibility, Calculate, reducer и cutover/Preview.
- Полный `pytest` после добавления произвольных пар: `2842 passed`,
  `67 skipped`, `48 failed` за 156 с. Число падений совпадает с известным
  baseline Legacy/dialogue-набора; новых failure-ID от этого изменения нет.
- Живой защищённый Preview с реальной LLM:
  - «первый и третий» → `VRS.254.18.0` + `VRS.324.18.0`;
  - «второй с четвёртым» → `VRS.256.18.0` + `VRS.256.13.0`;
  - «1 и 4 варианта» → `VRS.254.18.0` + `VRS.256.13.0`;
  - «первый и шестой» → уточнение, без сравнения произвольных товаров.

Публичный маршрут и canary не менялись; временный Preview-сервер остановлен.
