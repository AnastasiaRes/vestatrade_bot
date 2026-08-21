# Конкурентный live-LLM QA после исправлений

API: `http://127.0.0.1:8000`.
Запросов: **100**, независимых сессий: **10**.
Параллельных workers: **10**.
Ошибок инвариантов: **68**.
Утечек между сессиями: **0**.
Latency p50/p95/max: **10.532 / 20.33 / 27.068 с**.
LLM-метрики: `{"llm_requested": 88, "llm_transport_succeeded": 88, "llm_output_accepted": 72, "response_llm_output_accepted": 72, "safe_llm_rejections": 14}`.
Источники финальных ответов: `{"deterministic": 28, "response_llm": 72}`.

## Проблемы

- characteristics/live-multi-1784569864-1: потерян свой SKU VRS.256.18.0; got=[]
- characteristics/live-multi-1784569864-1: ответ не называет свой SKU VRS.256.18.0
- characteristics/live-multi-1784569864-2: потерян свой SKU 2202210; got=[]
- characteristics/live-multi-1784569864-2: ответ не называет свой SKU 2202210
- characteristics/live-multi-1784569864-3: потерян свой SKU 3300867; got=[]
- characteristics/live-multi-1784569864-3: ответ не называет свой SKU 3300867
- characteristics/live-multi-1784569864-4: потерян свой SKU VRS.129G.15.0; got=[]
- characteristics/live-multi-1784569864-4: ответ не называет свой SKU VRS.129G.15.0
- characteristics/live-multi-1784569864-5: потерян свой SKU VT.226.N.05; got=[]
- characteristics/live-multi-1784569864-5: ответ не называет свой SKU VT.226.N.05
- characteristics/live-multi-1784569864-7: ответ не называет свой SKU VT.KIT.3.0
- characteristics/live-multi-1784569864-9: ответ не называет свой SKU VT.AC674.V.0
- link/live-multi-1784569864-0: потерян свой SKU VT.217.N.04; got=[]
- link/live-multi-1784569864-1: потерян свой SKU VRS.256.18.0; got=[]
- link/live-multi-1784569864-2: потерян свой SKU 2202210; got=[]
- link/live-multi-1784569864-3: потерян свой SKU 3300867; got=[]
- link/live-multi-1784569864-4: потерян свой SKU VRS.129G.15.0; got=[]
- link/live-multi-1784569864-5: потерян свой SKU VT.226.N.05; got=[]
- link/live-multi-1784569864-6: потерян свой SKU APZ9-550M; got=[]
- link/live-multi-1784569864-7: потерян свой SKU VT.KIT.3.0; got=[]
- link/live-multi-1784569864-8: потерян свой SKU VTc.701.NE.05; got=[]
- link/live-multi-1784569864-9: потерян свой SKU VT.AC674.V.0; got=[]
- price/live-multi-1784569864-0: потерян свой SKU VT.217.N.04; got=[]
- price/live-multi-1784569864-0: ответ не называет свой SKU VT.217.N.04
- price/live-multi-1784569864-6: потерян свой SKU APZ9-550M; got=[]
- price/live-multi-1784569864-6: ответ не называет свой SKU APZ9-550M
- price/live-multi-1784569864-7: потерян свой SKU VT.KIT.3.0; got=[]
- price/live-multi-1784569864-7: ответ не называет свой SKU VT.KIT.3.0
- price/live-multi-1784569864-8: потерян свой SKU VTc.701.NE.05; got=[]
- price/live-multi-1784569864-8: ответ не называет свой SKU VTc.701.NE.05
- description/live-multi-1784569864-0: потерян свой SKU VT.217.N.04; got=[]
- description/live-multi-1784569864-0: ответ не называет свой SKU VT.217.N.04
- description/live-multi-1784569864-1: потерян свой SKU VRS.256.18.0; got=[]
- description/live-multi-1784569864-1: ответ не называет свой SKU VRS.256.18.0
- description/live-multi-1784569864-2: потерян свой SKU 2202210; got=[]
- description/live-multi-1784569864-2: ответ не называет свой SKU 2202210
- description/live-multi-1784569864-3: потерян свой SKU 3300867; got=[]
- description/live-multi-1784569864-3: ответ не называет свой SKU 3300867
- description/live-multi-1784569864-4: потерян свой SKU VRS.129G.15.0; got=[]
- description/live-multi-1784569864-4: ответ не называет свой SKU VRS.129G.15.0
- description/live-multi-1784569864-5: потерян свой SKU VT.226.N.05; got=[]
- description/live-multi-1784569864-5: ответ не называет свой SKU VT.226.N.05
- description/live-multi-1784569864-6: потерян свой SKU APZ9-550M; got=[]
- description/live-multi-1784569864-6: ответ не называет свой SKU APZ9-550M
- description/live-multi-1784569864-7: потерян свой SKU VT.KIT.3.0; got=[]
- description/live-multi-1784569864-7: ответ не называет свой SKU VT.KIT.3.0
- description/live-multi-1784569864-8: потерян свой SKU VTc.701.NE.05; got=[]
- description/live-multi-1784569864-8: ответ не называет свой SKU VTc.701.NE.05
- description/live-multi-1784569864-9: потерян свой SKU VT.AC674.V.0; got=[]
- description/live-multi-1784569864-9: ответ не называет свой SKU VT.AC674.V.0
- name/live-multi-1784569864-2: потерян свой SKU 2202210; got=[]
- name/live-multi-1784569864-2: ответ не называет свой SKU 2202210
- name/live-multi-1784569864-2: ResponseComposer не запросил LLM
- name/live-multi-1784569864-2: LLM transport не сработал
- name/live-multi-1784569864-3: потерян свой SKU 3300867; got=[]
- name/live-multi-1784569864-3: ответ не называет свой SKU 3300867
- name/live-multi-1784569864-3: ResponseComposer не запросил LLM
- name/live-multi-1784569864-3: LLM transport не сработал
- summary_link/live-multi-1784569864-0: потерян свой SKU VT.217.N.04; got=[]
- summary_link/live-multi-1784569864-1: потерян свой SKU VRS.256.18.0; got=[]
- summary_link/live-multi-1784569864-2: потерян свой SKU 2202210; got=[]
- summary_link/live-multi-1784569864-3: потерян свой SKU 3300867; got=[]
- summary_link/live-multi-1784569864-4: потерян свой SKU VRS.129G.15.0; got=[]
- summary_link/live-multi-1784569864-5: потерян свой SKU VT.226.N.05; got=[]
- summary_link/live-multi-1784569864-6: потерян свой SKU APZ9-550M; got=[]
- summary_link/live-multi-1784569864-7: потерян свой SKU VT.KIT.3.0; got=[]
- summary_link/live-multi-1784569864-8: потерян свой SKU VTc.701.NE.05; got=[]
- summary_link/live-multi-1784569864-9: потерян свой SKU VT.AC674.V.0; got=[]

## Ходы

| Фаза | Сессия | Ожидаемый SKU | Сек. | Результат |
|---|---|---|---:|---|
| setup | `live-multi-1784569864-0` | `VT.217.N.04` | 0.421 | PASS |
| setup | `live-multi-1784569864-1` | `VRS.256.18.0` | 0.355 | PASS |
| setup | `live-multi-1784569864-2` | `2202210` | 0.421 | PASS |
| setup | `live-multi-1784569864-3` | `3300867` | 0.42 | PASS |
| setup | `live-multi-1784569864-4` | `VRS.129G.15.0` | 0.355 | PASS |
| setup | `live-multi-1784569864-5` | `VT.226.N.05` | 0.42 | PASS |
| setup | `live-multi-1784569864-6` | `APZ9-550M` | 0.421 | PASS |
| setup | `live-multi-1784569864-7` | `VT.KIT.3.0` | 0.421 | PASS |
| setup | `live-multi-1784569864-8` | `VTc.701.NE.05` | 0.421 | PASS |
| setup | `live-multi-1784569864-9` | `VT.AC674.V.0` | 0.42 | PASS |
| characteristics | `live-multi-1784569864-0` | `VT.217.N.04` | 20.33 | PASS |
| characteristics | `live-multi-1784569864-1` | `VRS.256.18.0` | 17.087 | потерян свой SKU VRS.256.18.0; got=[]; ответ не называет свой SKU VRS.256.18.0 |
| characteristics | `live-multi-1784569864-2` | `2202210` | 13.503 | потерян свой SKU 2202210; got=[]; ответ не называет свой SKU 2202210 |
| characteristics | `live-multi-1784569864-3` | `3300867` | 9.165 | потерян свой SKU 3300867; got=[]; ответ не называет свой SKU 3300867 |
| characteristics | `live-multi-1784569864-4` | `VRS.129G.15.0` | 14.103 | потерян свой SKU VRS.129G.15.0; got=[]; ответ не называет свой SKU VRS.129G.15.0 |
| characteristics | `live-multi-1784569864-5` | `VT.226.N.05` | 21.261 | потерян свой SKU VT.226.N.05; got=[]; ответ не называет свой SKU VT.226.N.05 |
| characteristics | `live-multi-1784569864-6` | `APZ9-550M` | 16.311 | PASS |
| characteristics | `live-multi-1784569864-7` | `VT.KIT.3.0` | 13.11 | ответ не называет свой SKU VT.KIT.3.0 |
| characteristics | `live-multi-1784569864-8` | `VTc.701.NE.05` | 11.191 | PASS |
| characteristics | `live-multi-1784569864-9` | `VT.AC674.V.0` | 18.517 | ответ не называет свой SKU VT.AC674.V.0 |
| link | `live-multi-1784569864-0` | `VT.217.N.04` | 9.732 | потерян свой SKU VT.217.N.04; got=[] |
| link | `live-multi-1784569864-1` | `VRS.256.18.0` | 3.464 | потерян свой SKU VRS.256.18.0; got=[] |
| link | `live-multi-1784569864-2` | `2202210` | 12.871 | потерян свой SKU 2202210; got=[] |
| link | `live-multi-1784569864-3` | `3300867` | 2.129 | потерян свой SKU 3300867; got=[] |
| link | `live-multi-1784569864-4` | `VRS.129G.15.0` | 6.053 | потерян свой SKU VRS.129G.15.0; got=[] |
| link | `live-multi-1784569864-5` | `VT.226.N.05` | 10.838 | потерян свой SKU VT.226.N.05; got=[] |
| link | `live-multi-1784569864-6` | `APZ9-550M` | 7.402 | потерян свой SKU APZ9-550M; got=[] |
| link | `live-multi-1784569864-7` | `VT.KIT.3.0` | 4.642 | потерян свой SKU VT.KIT.3.0; got=[] |
| link | `live-multi-1784569864-8` | `VTc.701.NE.05` | 8.658 | потерян свой SKU VTc.701.NE.05; got=[] |
| link | `live-multi-1784569864-9` | `VT.AC674.V.0` | 12.076 | потерян свой SKU VT.AC674.V.0; got=[] |
| identity | `live-multi-1784569864-0` | `VT.217.N.04` | 16.149 | PASS |
| identity | `live-multi-1784569864-1` | `VRS.256.18.0` | 19.298 | PASS |
| identity | `live-multi-1784569864-2` | `2202210` | 17.463 | PASS |
| identity | `live-multi-1784569864-3` | `3300867` | 14.56 | PASS |
| identity | `live-multi-1784569864-4` | `VRS.129G.15.0` | 12.358 | PASS |
| identity | `live-multi-1784569864-5` | `VT.226.N.05` | 10.488 | PASS |
| identity | `live-multi-1784569864-6` | `APZ9-550M` | 25.206 | PASS |
| identity | `live-multi-1784569864-7` | `VT.KIT.3.0` | 21.33 | PASS |
| identity | `live-multi-1784569864-8` | `VTc.701.NE.05` | 23.03 | PASS |
| identity | `live-multi-1784569864-9` | `VT.AC674.V.0` | 27.068 | PASS |
| price | `live-multi-1784569864-0` | `VT.217.N.04` | 9.674 | потерян свой SKU VT.217.N.04; got=[]; ответ не называет свой SKU VT.217.N.04 |
| price | `live-multi-1784569864-1` | `VRS.256.18.0` | 12.53 | PASS |
| price | `live-multi-1784569864-2` | `2202210` | 16.286 | PASS |
| price | `live-multi-1784569864-3` | `3300867` | 14.59 | PASS |
| price | `live-multi-1784569864-4` | `VRS.129G.15.0` | 15.39 | PASS |
| price | `live-multi-1784569864-5` | `VT.226.N.05` | 13.885 | PASS |
| price | `live-multi-1784569864-6` | `APZ9-550M` | 11.628 | потерян свой SKU APZ9-550M; got=[]; ответ не называет свой SKU APZ9-550M |
| price | `live-multi-1784569864-7` | `VT.KIT.3.0` | 16.931 | потерян свой SKU VT.KIT.3.0; got=[]; ответ не называет свой SKU VT.KIT.3.0 |
| price | `live-multi-1784569864-8` | `VTc.701.NE.05` | 13.043 | потерян свой SKU VTc.701.NE.05; got=[]; ответ не называет свой SKU VTc.701.NE.05 |
| price | `live-multi-1784569864-9` | `VT.AC674.V.0` | 10.575 | PASS |
| stock | `live-multi-1784569864-0` | `VT.217.N.04` | 7.853 | PASS |
| stock | `live-multi-1784569864-1` | `VRS.256.18.0` | 8.596 | PASS |
| stock | `live-multi-1784569864-2` | `2202210` | 1.565 | PASS |
| stock | `live-multi-1784569864-3` | `3300867` | 3.617 | PASS |
| stock | `live-multi-1784569864-4` | `VRS.129G.15.0` | 6.643 | PASS |
| stock | `live-multi-1784569864-5` | `VT.226.N.05` | 7.923 | PASS |
| stock | `live-multi-1784569864-6` | `APZ9-550M` | 2.333 | PASS |
| stock | `live-multi-1784569864-7` | `VT.KIT.3.0` | 5.959 | PASS |
| stock | `live-multi-1784569864-8` | `VTc.701.NE.05` | 2.964 | PASS |
| stock | `live-multi-1784569864-9` | `VT.AC674.V.0` | 4.81 | PASS |
| description | `live-multi-1784569864-0` | `VT.217.N.04` | 9.689 | потерян свой SKU VT.217.N.04; got=[]; ответ не называет свой SKU VT.217.N.04 |
| description | `live-multi-1784569864-1` | `VRS.256.18.0` | 12.347 | потерян свой SKU VRS.256.18.0; got=[]; ответ не называет свой SKU VRS.256.18.0 |
| description | `live-multi-1784569864-2` | `2202210` | 13.225 | потерян свой SKU 2202210; got=[]; ответ не называет свой SKU 2202210 |
| description | `live-multi-1784569864-3` | `3300867` | 11.579 | потерян свой SKU 3300867; got=[]; ответ не называет свой SKU 3300867 |
| description | `live-multi-1784569864-4` | `VRS.129G.15.0` | 8.885 | потерян свой SKU VRS.129G.15.0; got=[]; ответ не называет свой SKU VRS.129G.15.0 |
| description | `live-multi-1784569864-5` | `VT.226.N.05` | 11.221 | потерян свой SKU VT.226.N.05; got=[]; ответ не называет свой SKU VT.226.N.05 |
| description | `live-multi-1784569864-6` | `APZ9-550M` | 10.259 | потерян свой SKU APZ9-550M; got=[]; ответ не называет свой SKU APZ9-550M |
| description | `live-multi-1784569864-7` | `VT.KIT.3.0` | 11.997 | потерян свой SKU VT.KIT.3.0; got=[]; ответ не называет свой SKU VT.KIT.3.0 |
| description | `live-multi-1784569864-8` | `VTc.701.NE.05` | 14.491 | потерян свой SKU VTc.701.NE.05; got=[]; ответ не называет свой SKU VTc.701.NE.05 |
| description | `live-multi-1784569864-9` | `VT.AC674.V.0` | 14.139 | потерян свой SKU VT.AC674.V.0; got=[]; ответ не называет свой SKU VT.AC674.V.0 |
| price_stock | `live-multi-1784569864-0` | `VT.217.N.04` | 16.278 | PASS |
| price_stock | `live-multi-1784569864-1` | `VRS.256.18.0` | 13.941 | PASS |
| price_stock | `live-multi-1784569864-2` | `2202210` | 11.583 | PASS |
| price_stock | `live-multi-1784569864-3` | `3300867` | 12.245 | PASS |
| price_stock | `live-multi-1784569864-4` | `VRS.129G.15.0` | 10.599 | PASS |
| price_stock | `live-multi-1784569864-5` | `VT.226.N.05` | 13.105 | PASS |
| price_stock | `live-multi-1784569864-6` | `APZ9-550M` | 3.319 | PASS |
| price_stock | `live-multi-1784569864-7` | `VT.KIT.3.0` | 17.021 | PASS |
| price_stock | `live-multi-1784569864-8` | `VTc.701.NE.05` | 15.514 | PASS |
| price_stock | `live-multi-1784569864-9` | `VT.AC674.V.0` | 14.696 | PASS |
| name | `live-multi-1784569864-0` | `VT.217.N.04` | 3.377 | PASS |
| name | `live-multi-1784569864-1` | `VRS.256.18.0` | 1.433 | PASS |
| name | `live-multi-1784569864-2` | `2202210` | 0.225 | потерян свой SKU 2202210; got=[]; ответ не называет свой SKU 2202210; ResponseComposer не запросил LLM; LLM transport не сработал |
| name | `live-multi-1784569864-3` | `3300867` | 0.224 | потерян свой SKU 3300867; got=[]; ответ не называет свой SKU 3300867; ResponseComposer не запросил LLM; LLM transport не сработал |
| name | `live-multi-1784569864-4` | `VRS.129G.15.0` | 8.23 | PASS |
| name | `live-multi-1784569864-5` | `VT.226.N.05` | 4.322 | PASS |
| name | `live-multi-1784569864-6` | `APZ9-550M` | 9.946 | PASS |
| name | `live-multi-1784569864-7` | `VT.KIT.3.0` | 5.61 | PASS |
| name | `live-multi-1784569864-8` | `VTc.701.NE.05` | 7.059 | PASS |
| name | `live-multi-1784569864-9` | `VT.AC674.V.0` | 12.395 | PASS |
| summary_link | `live-multi-1784569864-0` | `VT.217.N.04` | 12.549 | потерян свой SKU VT.217.N.04; got=[] |
| summary_link | `live-multi-1784569864-1` | `VRS.256.18.0` | 8.297 | потерян свой SKU VRS.256.18.0; got=[] |
| summary_link | `live-multi-1784569864-2` | `2202210` | 2.983 | потерян свой SKU 2202210; got=[] |
| summary_link | `live-multi-1784569864-3` | `3300867` | 4.395 | потерян свой SKU 3300867; got=[] |
| summary_link | `live-multi-1784569864-4` | `VRS.129G.15.0` | 10.963 | потерян свой SKU VRS.129G.15.0; got=[] |
| summary_link | `live-multi-1784569864-5` | `VT.226.N.05` | 13.1 | потерян свой SKU VT.226.N.05; got=[] |
| summary_link | `live-multi-1784569864-6` | `APZ9-550M` | 5.754 | потерян свой SKU APZ9-550M; got=[] |
| summary_link | `live-multi-1784569864-7` | `VT.KIT.3.0` | 6.987 | потерян свой SKU VT.KIT.3.0; got=[] |
| summary_link | `live-multi-1784569864-8` | `VTc.701.NE.05` | 9.526 | потерян свой SKU VTc.701.NE.05; got=[] |
| summary_link | `live-multi-1784569864-9` | `VT.AC674.V.0` | 2.184 | потерян свой SKU VT.AC674.V.0; got=[] |
