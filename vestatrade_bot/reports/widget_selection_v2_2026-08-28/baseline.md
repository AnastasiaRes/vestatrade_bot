# Baseline этапа V2 selection → cards

- Дата запуска: 2026-08-28, Europe/Moscow.
- Commit: `69475ad11bba07cda4584ff5819890d3c0c117eb`.
- Ветка: `qa/live-evaluation-and-fixes-2026-08-22`.
- Рабочее дерево до этапа уже содержало пользовательские и принятые изменения; reset/checkout не выполнялись.
- Публичные V2 routing/live/canary-флаги: выключены; canary percent: `0`.
- V2 доступна только через Shadow и защищённый QA Preview.
- Feed: `data/feed_showcase_100_2026-06-14.xml`, 100 товаров.
- SHA-256 feed: `81ed35da3a188c88d5f000bb7d6df9c02c562047616f97f88d36ea6046a9384f`.
- Паспортов PDF: 24.
- Aggregate SHA-256 паспортов: `aa9d96c55cd7dbcc20b0315582d25b9665cf22249c975f1efd93873c4535ebad`.
- LLM: OpenRouter, `qwen/qwen3-vl-8b-instruct`.
- Embeddings: `openai/text-embedding-3-small`, включены.
- Исходный полный pytest: `52 failed, 2571 passed, 66 skipped`.

Baseline-артефакты предыдущих этапов не изменялись.
