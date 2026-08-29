# Passport onboarding checkpoint — wave 1 (2026-08-28)

## Точка контроля

- Ветка: `qa/live-evaluation-and-fixes-2026-08-22`
- Базовый commit (до checkpoint): `f30cf43bd10684b72be7b2976b8b72a95a0ebe4d`
- Базовая стратегия: существующий pipeline и единый индекс паспортов
- Режим: не менялся, глобальные user-изменения не сбрасывались

## Feed и паспортная база (исходные)

- Feed источник: `vestatrade_bot/data/feed_showcase_100_2026-06-14.xml`
- Feed digest (SHA-256): `81ed35da3a188c88d5f000bb7d6df9c02c562047616f97f88d36ea6046a9384f`
- Записей в feed: `100`
- Passport index: `vestatrade_bot/app/data/passport_index.json`
- Passport index metadata: `version=1`, `model=openai/text-embedding-3-small`, `source_digest=d795fa563d8e5ee6c434f37df1dbbe2689af156ea3444d7355c9a96d1e2f90d4`
- Чанков в индексе: `1742`
- Активных документов в индексе (уникальные `document`): `28`

## Checkpoint-валидаторы для wave 1

- Проверка exact attachment 5 SKU в `tests/test_new_passport_wave1.py`:
  - `VT.5000.0.0` → `0962d51dab5c3219f584820a92d556aa.pdf`
  - `8216262000` → `63109b6ad4cd19.27758769.pdf`
  - `RBM-0210-050006` → `Rommer_pasport алюминиевые.pdf`
  - `RAL-1210-050006` → `Rommer_pasport алюминиевые.pdf`
  - `2202210` → `Руководство_электрические_котлы_ARDERIA_2023.pdf`
- Для всех пяти документов выполнялась проверка `docs_text`/`product.documents` и поиска ожидаемых anchor-фрагментов.
- Для `Rommer_pasport алюминиевые.pdf` проверено присутствие модели и тепловых данных по обеим строкам.

## Document scope / retrieval / evidence (wave 1)

- `product_docs_map.json` содержит привязку только к нужным SKU (или `enabled: false` для невалидных документов):
  - включены точные `skus` для 5 целевых SKU,
  - исключены `132779.pdf`, `Instrukciya.pdf`, `a93621c2b5b44dcdd178ce52c8155937.pdf`, `rommer stalnyie panelnyie_2.pdf`, `user-manual-pumps-grundfos-ups-25-40.pdf` как `enabled: false`.
- `passport_retrieval` + индекс содержат эти документы как источники и возвращают ожидаемые поля/фрагменты по их `docs_text`.

## Baseline → result → delta

- Baseline до wave-1: `2617 passed, 52 failed, 66 skipped` (исторические падения).
- После checkpoint-подготовки passport-onboarding: исторических падений сохранено `52` (без расширения набора регрессий), новых падений в документной части не добавлено.
- 5 целевых SKU для wave 1 проходят профильные проверки на точную привязку и доказательные проверки.

## Коммит-готовность

- Зафиксировано: digest feed, digest passport index, число активных документов, exact-SKU mapping wave 1,
  scope/evidence-проверки на уровне новых тестов и `product_docs_map.json`.
- Рекомендуемый следующий шаг: продолжать следующий функциональный этап без повторного полного persona-прогона.
