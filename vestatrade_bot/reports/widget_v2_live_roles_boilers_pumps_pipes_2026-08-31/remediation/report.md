# Capability-aware V2 remediation report

Date: 2026-08-31

## Environment

- Branch: `qa/live-evaluation-and-fixes-2026-08-22`
- Starting HEAD: `3b4b3acd97bd1087b29e7ef1816541f4a08f2503`
- Source tree: dirty before this work; existing user files and artifacts were preserved.
- Protected Preview: `http://127.0.0.1:8010/widget-v2-preview`
- Feed: 100 products, file source, digest `425986b66a098f34fc74030c2099a1c53f9b220432fa99b40a8fe07d6eeeec61`.
- Semantic model: `qwen/qwen3-vl-8b-instruct` through OpenRouter.
- Embeddings: `baai/bge-m3` through OpenRouter.
- Passport index: ready, 1742 chunks, source digest `674fbdfec811b1675e54df9cfbf2d4659920fbb7ccda4fe3270c809fc7c41860`.
- Public routing was not changed by this remediation; Preview was started with live delivery disabled and public canary at 0%.

## Root cause

The observed failures were not one broken catalogue and did not require a
second bot.  V2 understood most product facts, but ownership was sometimes
decided too late or an accepted typed fact was lost between semantic repair,
the reducer, seller policy and the outcome gate.  Legacy then received a
fragment with no V2 state and produced a plausible but unrelated answer.

The remediation therefore adds typed seams around the existing V2 services:

1. capability ownership and Legacy-result validation;
2. deterministic, registry-backed semantic anchors;
3. goal-scoped selection preferences;
4. catalog-bound named/SKU references;
5. grounded Compare/ProductFact reuse;
6. explicit controlled relaxation instead of a hidden weaker match.

No second state, router, catalogue, SKU resolver, Passport Agent, index,
ranking system or renderer-side fact extraction was introduced.

## Implemented behavior

### Capability-aware Legacy fallback

- The boundary registry identifies the typed V2 capability before Legacy is
  allowed to own a turn.
- A pump request containing Q/H cannot be reinterpreted as a generic fitting
  list merely because it contains several numbers.
- Legacy products can cross back only through `LegacyScopeBridge`, where SKU,
  price, stock and URL are checked against the current source revision.
- A rejected Legacy result becomes a typed boundary; it is not imported into
  V2 state and does not create phantom customer-visible cards.

### Semantic and state continuity

- Spoken pump flow/head and sewer dimensions are normalized with evidence.
- `длиной три метра` becomes `length_mm=3000`; a noncanonical LLM string is
  replaced field-by-field.
- `нужно три метра трубы` without a typed length question or an explicit
  product-length phrase is not silently converted into the size of one pipe.
- Explicit brand clearing, stock filtering and lower-price preferences remain
  attached to their goal rather than leaking to the next topic.

### Grounded product actions

- Explicit named pairs such as Arderia SB28/SB32 are recovered as Compare,
  resolved through the current catalog and compared without a second seller
  agent.
- Compare canonicalizes `installation_length_mm` through the existing catalog
  registry to `mounting_length_mm`, so pump mounting length is no longer lost.
- ProductFact keeps separately gated predicates instead of letting one missing
  optional field erase accepted facts.
- Numeric, slash, partial and named references continue to use the shared
  catalog resolver.

### Customer-authorized shorter sewer pipe

The final live chain was:

1. Customer: `Нужна наружная канализационная труба DN110 длиной три метра, только в наличии.`
2. V2: retained `DN110`, `sewer_scope=external`, `length_mm=3000` and honestly
   reported no exact in-stock match.
3. Customer: `Трёхметровой нет? Тогда возьмём ближайшую короче из наличия.`
4. V2: returned SKU `220010`, stated `1000` instead of requested `3000`, kept
   diameter/outdoor scope/stock hard and labelled the card a preliminary,
   explicitly authorised length substitution.

Telemetry for the second turn:

- semantic action: `find`;
- preferences: `length_nearest_shorter=3000`, `stock_required=true`;
- planner candidate: `220010`;
- next action: `present_controlled_analog`;
- reason: `customer_authorized_nearest_shorter_analog`;
- response mode: `v2_primary`;
- latency: 12.7 s.

An earlier attempt exposed two independent issues and was not counted as a
pass: the LLM string `"три метра"` was not canonical, then the old `SELECT`
task tried to send a controlled relaxation through exact recommendation.  The
normalizer and seller-policy seam were fixed; the outcome gate itself was not
weakened.

## Other live confirmations from this remediation

- Pump Q/H selection produced five V2 cards; Compare of the first and second
  showed price, confirmed maximum head and mounting length `180 mm` for both.
- Named Arderia SB28/SB32 comparison ran in V2 and showed source-backed price,
  stock, declared area and power.  One pre-final attempt fell to Legacy before
  the deterministic named-pair repair; the fresh post-restart diagnostic run
  passed.
- A live E.C.A. passport recheck was not repeated because the tool safety
  boundary blocked sending private PDF fragments to the external LLM.  This
  is recorded as not executed, not as a pass.  Local contract/evidence tests
  and index readiness remain green.

## Tests

- Final focused regression set: **579 passed**.
- Targeted semantic/catalog/Compare/readiness set after the last repair:
  **222 passed**.
- Full pytest executed earlier in this remediation: **2894 passed, 67 skipped,
  44 failed**.  The remaining failures are old Legacy dialogue/wording zones;
  the complete failure-ID equivalence audit is not claimed here.
- One separate historical wording assertion still expects the literal phrase
  `максимальный напор`; the current renderer asks the same typed fact in more
  natural wording.  It is not evidence of lost pump state.

## UI and runtime

- `/ready`: 200, 100 products, passport index ready.
- `/widget-v2-preview`: 200, HTML returned.
- Automated visual interaction with the already open localhost tab was
  blocked by the in-app browser URL security policy.  No alternate browser
  automation was used.  Therefore visual UI-smoke is **blocked/not executed**,
  not passed.
- During one live call the external provider returned one transient 429 and
  one TLS handshake timeout before its configured retry succeeded.  No answer
  was fabricated, but this confirms the known latency/reliability P1.

## Decisions

- Capability-aware fallback and the targeted pump/pipe/Compare remediation:
  **accept for protected V2 Preview**.
- Passport readiness: **accept**; external live evidence replay for E.C.A. is
  still unexecuted under the current data-transfer restriction.
- Full functional/TZ acceptance: **block** until the remaining targeted
  dialogue gaps and visual UI-smoke are closed.
- Public rollout: **block**.  This work is not evidence for changing public
  Legacy routing or canary percentage.

## Remaining P1 work

1. Finish the failure-ID audit for the 44 full-suite failures and migrate only
   behaviorally valid requirements to protected V2 gates.
2. Add/confirm the remaining missing V2 capabilities identified in the live
   report: irrigation/well project flow, warm-floor/project lists, commercial
   topics and handoff, rather than allowing unvalidated textual fallback.
3. Run a human UI-smoke of the open protected Preview tab.
4. Address semantic/audit latency and transient provider retries as a separate
   performance stage; this remediation deliberately did not optimize them.
