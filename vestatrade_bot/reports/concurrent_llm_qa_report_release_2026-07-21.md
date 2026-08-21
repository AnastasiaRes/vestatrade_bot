# Конкурентный live-LLM QA после исправлений

API: `http://127.0.0.1:8000`.
Запросов: **100**, независимых сессий: **10**.
Параллельных workers: **10**.
Ошибок инвариантов: **0**.
Утечек между сессиями: **0**.
Latency p50/p95/max: **3.166 / 20.409 / 27.588 с**.
LLM-метрики: `{"llm_requested": 60, "llm_transport_succeeded": 60, "llm_output_accepted": 18, "response_llm_output_accepted": 18, "response_llm_requested": 20, "response_llm_transport_succeeded": 20, "safe_llm_rejections": 2}`.
Источники финальных ответов: `{"deterministic": 82, "response_llm": 18}`.
Причины безопасных отклонений LLM: `{"invented measurement: 13 мм": 1, "repeated_previous_answer": 1}`.

## Проблемы

Автоматические инварианты прошли без ошибок.

## Ходы

| Фаза | Сессия | Ожидаемый SKU | Сек. | Результат |
|---|---|---|---:|---|
| setup | `live-multi-1784622831-0` | `VT.217.N.04` | 0.388 | PASS |
| setup | `live-multi-1784622831-1` | `VRS.256.18.0` | 0.288 | PASS |
| setup | `live-multi-1784622831-2` | `2202210` | 0.328 | PASS |
| setup | `live-multi-1784622831-3` | `3300867` | 0.388 | PASS |
| setup | `live-multi-1784622831-4` | `VRS.129G.15.0` | 0.388 | PASS |
| setup | `live-multi-1784622831-5` | `VT.226.N.05` | 0.341 | PASS |
| setup | `live-multi-1784622831-6` | `APZ9-550M` | 0.387 | PASS |
| setup | `live-multi-1784622831-7` | `VT.KIT.3.0` | 0.387 | PASS |
| setup | `live-multi-1784622831-8` | `VTc.701.NE.05` | 0.388 | PASS |
| setup | `live-multi-1784622831-9` | `VT.AC674.V.0` | 0.347 | PASS |
| characteristics | `live-multi-1784622831-0` | `VT.217.N.04` | 7.881 | PASS |
| characteristics | `live-multi-1784622831-1` | `VRS.256.18.0` | 1.643 | PASS |
| characteristics | `live-multi-1784622831-2` | `2202210` | 8.665 | PASS |
| characteristics | `live-multi-1784622831-3` | `3300867` | 3.165 | PASS |
| characteristics | `live-multi-1784622831-4` | `VRS.129G.15.0` | 6.421 | PASS |
| characteristics | `live-multi-1784622831-5` | `VT.226.N.05` | 5.487 | PASS |
| characteristics | `live-multi-1784622831-6` | `APZ9-550M` | 3.904 | PASS |
| characteristics | `live-multi-1784622831-7` | `VT.KIT.3.0` | 2.386 | PASS |
| characteristics | `live-multi-1784622831-8` | `VTc.701.NE.05` | 4.705 | PASS |
| characteristics | `live-multi-1784622831-9` | `VT.AC674.V.0` | 7.137 | PASS |
| advice | `live-multi-1784622831-0` | `VT.217.N.04` | 11.506 | PASS |
| advice | `live-multi-1784622831-1` | `VRS.256.18.0` | 12.539 | PASS |
| advice | `live-multi-1784622831-2` | `2202210` | 14.981 | PASS |
| advice | `live-multi-1784622831-3` | `3300867` | 21.245 | PASS |
| advice | `live-multi-1784622831-4` | `VRS.129G.15.0` | 13.742 | PASS |
| advice | `live-multi-1784622831-5` | `VT.226.N.05` | 20.171 | PASS |
| advice | `live-multi-1784622831-6` | `APZ9-550M` | 16.523 | PASS |
| advice | `live-multi-1784622831-7` | `VT.KIT.3.0` | 18.92 | PASS |
| advice | `live-multi-1784622831-8` | `VTc.701.NE.05` | 10.718 | PASS |
| advice | `live-multi-1784622831-9` | `VT.AC674.V.0` | 17.482 | PASS |
| link | `live-multi-1784622831-0` | `VT.217.N.04` | 0.306 | PASS |
| link | `live-multi-1784622831-1` | `VRS.256.18.0` | 0.305 | PASS |
| link | `live-multi-1784622831-2` | `2202210` | 0.305 | PASS |
| link | `live-multi-1784622831-3` | `3300867` | 0.305 | PASS |
| link | `live-multi-1784622831-4` | `VRS.129G.15.0` | 0.304 | PASS |
| link | `live-multi-1784622831-5` | `VT.226.N.05` | 0.304 | PASS |
| link | `live-multi-1784622831-6` | `APZ9-550M` | 0.304 | PASS |
| link | `live-multi-1784622831-7` | `VT.KIT.3.0` | 0.304 | PASS |
| link | `live-multi-1784622831-8` | `VTc.701.NE.05` | 0.303 | PASS |
| link | `live-multi-1784622831-9` | `VT.AC674.V.0` | 0.303 | PASS |
| price | `live-multi-1784622831-0` | `VT.217.N.04` | 9.174 | PASS |
| price | `live-multi-1784622831-1` | `VRS.256.18.0` | 4.017 | PASS |
| price | `live-multi-1784622831-2` | `2202210` | 8.218 | PASS |
| price | `live-multi-1784622831-3` | `3300867` | 3.167 | PASS |
| price | `live-multi-1784622831-4` | `VRS.129G.15.0` | 4.853 | PASS |
| price | `live-multi-1784622831-5` | `VT.226.N.05` | 2.343 | PASS |
| price | `live-multi-1784622831-6` | `APZ9-550M` | 1.538 | PASS |
| price | `live-multi-1784622831-7` | `VT.KIT.3.0` | 7.381 | PASS |
| price | `live-multi-1784622831-8` | `VTc.701.NE.05` | 6.518 | PASS |
| price | `live-multi-1784622831-9` | `VT.AC674.V.0` | 5.725 | PASS |
| stock | `live-multi-1784622831-0` | `VT.217.N.04` | 0.234 | PASS |
| stock | `live-multi-1784622831-1` | `VRS.256.18.0` | 0.234 | PASS |
| stock | `live-multi-1784622831-2` | `2202210` | 0.234 | PASS |
| stock | `live-multi-1784622831-3` | `3300867` | 0.234 | PASS |
| stock | `live-multi-1784622831-4` | `VRS.129G.15.0` | 0.233 | PASS |
| stock | `live-multi-1784622831-5` | `VT.226.N.05` | 0.233 | PASS |
| stock | `live-multi-1784622831-6` | `APZ9-550M` | 0.233 | PASS |
| stock | `live-multi-1784622831-7` | `VT.KIT.3.0` | 0.233 | PASS |
| stock | `live-multi-1784622831-8` | `VTc.701.NE.05` | 0.233 | PASS |
| stock | `live-multi-1784622831-9` | `VT.AC674.V.0` | 0.233 | PASS |
| description | `live-multi-1784622831-0` | `VT.217.N.04` | 2.224 | PASS |
| description | `live-multi-1784622831-1` | `VRS.256.18.0` | 7.945 | PASS |
| description | `live-multi-1784622831-2` | `2202210` | 5.374 | PASS |
| description | `live-multi-1784622831-3` | `3300867` | 7.104 | PASS |
| description | `live-multi-1784622831-4` | `VRS.129G.15.0` | 4.742 | PASS |
| description | `live-multi-1784622831-5` | `VT.226.N.05` | 3.039 | PASS |
| description | `live-multi-1784622831-6` | `APZ9-550M` | 4.189 | PASS |
| description | `live-multi-1784622831-7` | `VT.KIT.3.0` | 1.465 | PASS |
| description | `live-multi-1784622831-8` | `VTc.701.NE.05` | 6.184 | PASS |
| description | `live-multi-1784622831-9` | `VT.AC674.V.0` | 8.968 | PASS |
| price_stock | `live-multi-1784622831-0` | `VT.217.N.04` | 6.778 | PASS |
| price_stock | `live-multi-1784622831-1` | `VRS.256.18.0` | 7.518 | PASS |
| price_stock | `live-multi-1784622831-2` | `2202210` | 3.147 | PASS |
| price_stock | `live-multi-1784622831-3` | `3300867` | 5.886 | PASS |
| price_stock | `live-multi-1784622831-4` | `VRS.129G.15.0` | 9.16 | PASS |
| price_stock | `live-multi-1784622831-5` | `VT.226.N.05` | 8.344 | PASS |
| price_stock | `live-multi-1784622831-6` | `APZ9-550M` | 1.93 | PASS |
| price_stock | `live-multi-1784622831-7` | `VT.KIT.3.0` | 4.326 | PASS |
| price_stock | `live-multi-1784622831-8` | `VTc.701.NE.05` | 3.787 | PASS |
| price_stock | `live-multi-1784622831-9` | `VT.AC674.V.0` | 5.149 | PASS |
| recommendation | `live-multi-1784622831-0` | `VT.217.N.04` | 16.21 | PASS |
| recommendation | `live-multi-1784622831-1` | `VRS.256.18.0` | 10.489 | PASS |
| recommendation | `live-multi-1784622831-2` | `2202210` | 27.588 | PASS |
| recommendation | `live-multi-1784622831-3` | `3300867` | 21.563 | PASS |
| recommendation | `live-multi-1784622831-4` | `VRS.129G.15.0` | 14.675 | PASS |
| recommendation | `live-multi-1784622831-5` | `VT.226.N.05` | 26.034 | PASS |
| recommendation | `live-multi-1784622831-6` | `APZ9-550M` | 20.409 | PASS |
| recommendation | `live-multi-1784622831-7` | `VT.KIT.3.0` | 23.26 | PASS |
| recommendation | `live-multi-1784622831-8` | `VTc.701.NE.05` | 12.933 | PASS |
| recommendation | `live-multi-1784622831-9` | `VT.AC674.V.0` | 17.9 | PASS |
| summary_link | `live-multi-1784622831-0` | `VT.217.N.04` | 0.277 | PASS |
| summary_link | `live-multi-1784622831-1` | `VRS.256.18.0` | 0.289 | PASS |
| summary_link | `live-multi-1784622831-2` | `2202210` | 0.289 | PASS |
| summary_link | `live-multi-1784622831-3` | `3300867` | 0.288 | PASS |
| summary_link | `live-multi-1784622831-4` | `VRS.129G.15.0` | 0.289 | PASS |
| summary_link | `live-multi-1784622831-5` | `VT.226.N.05` | 0.288 | PASS |
| summary_link | `live-multi-1784622831-6` | `APZ9-550M` | 0.287 | PASS |
| summary_link | `live-multi-1784622831-7` | `VT.KIT.3.0` | 0.288 | PASS |
| summary_link | `live-multi-1784622831-8` | `VTc.701.NE.05` | 0.287 | PASS |
| summary_link | `live-multi-1784622831-9` | `VT.AC674.V.0` | 0.287 | PASS |
