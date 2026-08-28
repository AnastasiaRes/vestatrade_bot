# Semantic hardening V2 — baseline

Дата фиксации: 2026-08-28.

## Репозиторий и режимы

- HEAD: `ab5f4cc12c03be55d42adaeccd9a99ea0cd74d5a`.
- Ветка: `qa/live-evaluation-and-fixes-2026-08-22`.
- Отслеживаемые файлы до начала этапа не изменены; обнаружены 181 существующий untracked QA-артефакт, они не удаляются и не перезаписываются.
- Публичные V2-флаги по умолчанию выключены, canary: `0%`.
- Preview остаётся доступен только через защищённый QA-режим.

## Модели и данные

- Semantic/основная LLM: `qwen/qwen3-vl-8b-instruct`.
- Semantic prompt: `turn-understanding-v1.19`.
- Passport embedding: `openai/text-embedding-3-small`.
- Feed: `data/feed_showcase_100_2026-06-14.xml`, 100 товаров.
- Feed SHA-256: `81ed35da3a188c88d5f000bb7d6df9c02c562047616f97f88d36ea6046a9384f`.
- Паспортов: 24 PDF.
- Совокупный digest паспортов: `aa9d96c55cd7dbcc20b0315582d25b9665cf22249c975f1efd93873c4535ebad`.
- Passport index: `app/data/passport_index.json`.
- Принятый catalog/source revision: `ba3eebcd8c8023fb2ee4010f07ce0d91c45668c10869a308d5b2a83cabe7255d`.

## Тестовый baseline

Полный `pytest`: `52 failed, 2585 passed, 66 skipped` за 84.57 с. Это совпадает с зафиксированным историческим baseline; новые отклонения до начала этапа отсутствуют.

Исходный ручной аудит paraphrase-gate: 8/33 полностью корректных, 5/33 частично полезных, 20/33 некорректных или ушедших в Legacy. Сам runner также содержит ложноположительные и ложноотрицательные проверки, поэтому сначала исправляется его turn-level контракт.

## Архитектурная исходная причина

`DialogueStateV2` и reducer уже сохраняют принятые типизированные факты монотонно. Потеря происходит раньше: естественная реплика не всегда превращается в корректные product/entity/fact/action candidates, а текущий bounded repair покрывает лишь несколько буквальных форм. Поэтому этап не заменяет V2, reducer, ProductFact или Selection. Добавляется совместимый версионированный semantic delta, высокоточные anchors и дополнительный semantic-gate перед существующим reducer.
