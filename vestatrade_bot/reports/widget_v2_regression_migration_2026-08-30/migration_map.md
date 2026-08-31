# V2 migration map for the 52 historical dialogue failures

Date: 2026-08-30

## Stage-1 verification

- focused V2 continuity/cutover/SKU/capability gates: **134 passed**;
- pending-question, correction, progress and one-question V2 contracts:
  **243 passed**;
- full suite after the changes: **2752 passed, 52 failed, 67 skipped**.

The 52 failures are the unchanged historical Legacy baseline; no new failure
ID was introduced.  The public route and canary configuration were not
changed.

## Stage-2 protected Preview checks: pending answers and monotonic facts

The following checks extend the continuity gate through the protected V2
delivery boundary. They use structured semantic frames only to isolate
memory/reducer behaviour from LLM variance; the real V2 controller, selection
assembly, source gate, renderer and session commit still execute.

- **pending boiler → valves → explicit return to boiler**: the answer
  `ГВС не нужна` binds to the reactivated boiler goal, not to the newer valve
  task; boiler facts and valve facts remain separate and the boiler selection
  is delivered by V2;
- **explicit area correction**: correcting `150 м²` to `240 м²` replaces only
  `area_m2`, retains fuel and circuit facts, and refreshes the delivered scope
  from the smaller boiler list to the source-backed larger model.

The executable checks are
`tests/test_v2_historical_continuity_gate.py::test_returned_goal_receives_its_pending_answer_without_valve_fact_leakage`
and
`tests/test_v2_historical_continuity_gate.py::test_explicit_correction_replaces_only_the_named_goal_fact_in_preview`.

The more specialised Legacy requirements below retain their current migration
status until each has its own V2 behavioural assertion. The two checks above
are a shared guard for the reducer/delivery seam, not a reason to silently
mark an unrelated well, warm-floor or Project requirement as complete.

## Meaning of this map

The 52 failing Legacy node IDs are an archive of buyer-facing requirements,
not the acceptance result for V2. Every item is assigned to exactly one path:

- covered_now — asserted by a V2 gate in the current stage;
- next_v2_gate — must be migrated to a V2 behavioral test before its
  capability is considered complete;
- future_capability — retains its requirement, but depends on a capability
  not yet in V2;
- shared_safety — checked before Legacy/V2 routing;
- stale_test_review — verify real behavior first, then update only an
  obsolete test assertion.

The Legacy suite remains a regression fence: it may not acquire a new failure
ID. A V2 scenario is accepted only when the protected Preview response owner
is V2; a Legacy fallback never counts as a pass.

## Covered now: goal, scope and offer-fact continuity

| Legacy node ID | V2 invariant | Status |
| --- | --- | --- |
| test_dialog_scenarios.py::test_topic_change_resets_old_slots | A new topic suspends its predecessor; an explicit return restores the predecessor rather than its slots leaking into the new topic. | covered_now |
| test_dialog_scenarios.py::test_exact_sku_pronoun_price_followup_keeps_card | A direct offer fact preserves a delivered selection instead of replacing it with one card. | covered_now |
| test_live_architecture_regressions_2026_08_24.py::test_link_word_does_not_erase_requested_product_attributes | A direct offer action does not discard goal-bound facts. | covered_now |
| test_live_dialog_context_memory_regressions.py::test_return_to_warm_floor_recalls_summary_without_mutating_well_goal | Return is goal-specific and must not mutate a different goal. The generic mechanism is covered; the warm-floor capability itself is future work. | future_capability |
| test_live_catalog_goal_switch_regressions.py::test_radiator_type_correction_and_explicit_stock_relaxation_do_not_mix | Facts stay bound to their goal; stock relaxation belongs to that goal only. | next_v2_gate |
| test_live_catalog_goal_switch_regressions.py::test_natural_stock_relaxation_removes_previous_strict_filter | A deliberate relaxation replaces only the stock requirement. Protected Preview gate: `test_preview_explicit_stock_relaxation_replaces_only_its_goal_filter`. | covered_now |
| test_pump_intent_context_regressions.py::test_explicit_switch_from_pending_pump_to_boiler_is_respected | An explicit switch changes active goal without deleting the suspended pump task. | next_v2_gate |
| test_engineering_dialog_state_regressions.py::test_pending_question_has_stable_id_and_does_not_loop_verbatim | A pending question is typed, stable and advances after a useful answer. | next_v2_gate |
| test_engineering_dialog_state_regressions.py::test_total_volume_correction_clears_assumed_flow | An explicit correction replaces the conflicting fact and retracts derived assumptions. | next_v2_gate |
| test_pipe_clarification_loop_regressions.py::test_repeating_a_known_fact_advances_the_repeat_counter | Repeating a known fact cannot restart the same funnel. | next_v2_gate |
| test_pipe_clarification_loop_regressions.py::test_pipe_question_stops_repeating_after_two_useless_replies | Progress guard changes strategy instead of looping. | next_v2_gate |
| test_pump_refusal_funnel_regressions.py::test_known_head_and_length_clear_stale_deferrals_and_shape_next_question | New confirmed pump facts clear obsolete deferrals and determine one next question. | next_v2_gate |

The executable gate for the first three requirements is:
tests/test_v2_historical_continuity_gate.py. It sends a protected Preview
conversation through the real chat orchestrator:

1. pump selection;
2. valve selection;
3. explicit return to pump;
4. price of the second pump.

It asserts V2 ownership, both preserved selection scopes, the reactivated
pump goal, SKU PUMP-TWO and its source-backed price.

The same protected-Preview gate also covers the exact-identity boundary found
in the feed sweep:

- `11677 есть в наличии?` delivers SKU `11677` with its actual zero-stock
  status; it is not silently filtered away as an `in_stock_only` search;
- `Сколько стоит 68/2/8?` delivers the exact slash-shaped SKU through
  `OfferFact`, with the source-snapshot price;
- a currency amount such as `Сколько стоит 53843 рублей?` remains a value,
  not a product reference.

The shared `CatalogSkuAnchorResolver` accepts weak numeric/slash shapes in a
direct offer-fact question only after exact snapshot resolution. It never
uses partial matching for those shapes.

## Semantic and entity binding

| Legacy node ID | V2 target | Status |
| --- | --- | --- |
| test_engineering_context_and_terms_regressions.py::test_hydraulic_accumulator_is_not_a_pump_or_random_cheap_product | Product-kind anchor and category exclusion. | next_v2_gate |
| test_engineering_context_and_terms_regressions.py::test_complex_heating_context_keeps_hot_water_and_warm_floor_negation | Negated context must remain a fact, not a product category. | next_v2_gate |
| test_llm_safety_regressions.py::test_elongated_no_gas_typo_overrides_gas_keyword | Typo-tolerant negation is a semantic-gate invariant. | next_v2_gate |
| test_llm_safety_regressions.py::test_well_typo_keeps_source_and_does_not_ask_it_twice | Typo repair retains a valid fact and its provenance. | next_v2_gate |
| test_persona_qa_regressions_2026_07_21.py::test_negated_category_correction_is_respected | A correction changes only the named category/goal. | next_v2_gate |
| test_complectation_dialog_regressions.py::test_quoted_model_name_answers_the_pending_pump_question | A model mention binds to the pending pump question, not a new unrelated task. | next_v2_gate |
| test_complectation_dialog_regressions.py::test_pronoun_referring_to_part_from_previous_reply_is_resolved | Pronouns need a typed entity reference; this becomes a Compatibility/Project input. | future_capability |
| test_live_architecture_regressions_2026_08_24.py::test_named_shown_card_is_resolved_and_water_only_excludes_antifreeze | Named shown-card resolution and an explicit exclusion both survive. | next_v2_gate |
| test_full_feed_product_dialogue_regressions_2026_08_24.py::test_radiator_followup_keeps_both_cards_and_pressure_boundary | Direct fact follows the named/shown radiator without losing the selection. | next_v2_gate |
| test_full_feed_product_dialogue_regressions_2026_08_24.py::test_drainage_problem_frame_hard_excludes_head_below_vertical_lift | A stated engineering boundary is a hard filter, never a ranking hint. | next_v2_gate |

## Existing single-category Selection

| Legacy node ID | V2 target | Status |
| --- | --- | --- |
| test_assisted_unknown_selection_regressions_2026_08_24.py::test_spoken_area_and_first_turn_unknowns_start_preliminary_radiator_flow | Preliminary radiator cards preserve their stated unknowns. Protected Preview gate: `test_preview_radiator_room_area_is_only_a_source_backed_preliminary_proxy`. | covered_now |
| test_boiler_power_priority_regressions.py::test_arbitrary_power_pages_have_truthful_stock_and_alternative_notes | Stock and availability analogue are distinct, source-backed outcomes. Protected Preview gate: `test_preview_boiler_availability_analog_keeps_fuel_and_circuits_hard`. | covered_now |
| test_boiler_safety_context_regressions.py::test_electric_choice_with_repeated_constraints_keeps_context_and_does_not_loop | Boiler fuel and circuit facts persist through a repeated answer. Protected Preview gate: `test_preview_repeated_electric_choice_does_not_forget_area_or_reask_fuel`. | covered_now |
| test_dialog_scenarios.py::test_unknown_boiler_flow_asks_voltage_after_no_gas | The next boiler question follows known fuel and does not restart selection. | next_v2_gate |
| test_dialog_scenarios.py::test_sewer_dialog_accumulates_slots_and_asks_only_missing | Sewer facts accumulate and produce one relevant question. Protected Preview gate: `test_preview_sewer_facts_accumulate_across_turns_before_selection`. | covered_now |
| test_dialog_scenarios.py::test_sewer_dialog_uses_collected_slots_for_final_search | Collected sewer facts are applied to the final search. Protected Preview gate: `test_preview_sewer_facts_accumulate_across_turns_before_selection`. | covered_now |
| test_dialog_scenarios.py::test_oversized_boiler_is_only_presented_as_nearest_assortment_option | A more powerful boiler is explicitly an availability analogue, never a confirmed fit. Protected Preview gate: `test_preview_boiler_availability_analog_keeps_fuel_and_circuits_hard`. | covered_now |
| test_dialog_scenarios.py::test_boiler_consultation_remembers_shorthand_area_and_uses_passport | Spoken area remains available to selection and a later grounded fact. | next_v2_gate |
| test_dialog_scenarios.py::test_more_boilers_does_not_reset_pending_type_and_area | A show command does not clear known boiler facts. Protected Preview gate: `test_preview_more_boilers_keeps_known_type_and_area_until_circuits_answered`. | covered_now |
| test_engineering_requirements_regressions.py::test_well_pump_components_without_calculated_head_do_not_unlock_products | Missing safety-critical borehole inputs block cards. V2 asks the first concrete input required by the shared deterministic calculation; it never asks the buyer to invent a calculated head. Protected Preview gate: `test_preview_borehole_pump_asks_for_pipe_before_calculating_head`. | covered_now_v2_only |
| test_engineering_requirements_regressions.py::test_well_pump_flow_and_head_are_hard_filters | V2 derives a preliminary required head with the existing Legacy Darcy–Weisbach normalizer, then applies required head/flow as hard lower bounds against card maxima. A result remains preliminary until a Q/H curve is checked. Protected Preview gates: `test_preview_borehole_pump_derives_preliminary_head_and_filters_ratings`, `test_preview_borehole_explicit_duty_stays_preliminary_not_engineering_match`. | covered_now_v2_only |
| test_live_catalog_goal_switch_regressions.py::test_plain_radiator_type_without_size_still_requires_size | An insufficient radiator request asks one relevant question. Protected Preview gate: `test_preview_bare_radiator_starts_with_physical_size_not_material`. | covered_now |
| test_live_dialogue_regressions_2026_08_24.py::test_d10_ordinary_request_is_not_filtered_away | A valid small-diameter request is not rejected as noise. | next_v2_gate |
| test_live_dialog_context_memory_regressions.py::test_irrigation_well_dialog_keeps_context_and_estimates_standard_hose | Irrigation/well selection needs a dedicated contract before migration. | future_capability |
| test_live_dialog_context_memory_regressions.py::test_irrigation_well_accepts_several_facts_in_one_natural_turn | Multi-fact irrigation extraction awaits that contract. | future_capability |
| test_live_dialog_context_memory_regressions.py::test_warm_floor_nonexpert_yes_to_room_control_advances_live_dialog | Warm-floor solution planning is future Project work. | future_capability |

## Compatibility and multi-product work

| Legacy node ID | V2 target | Status |
| --- | --- | --- |
| test_assisted_unknown_selection_regressions_2026_08_24.py::test_missing_head_opening_is_not_mistaken_for_complectation | Unknown valve/head interface requests evidence, not a guessed kit. | next_v2_gate |
| test_passport_compatibility_regressions.py::test_pump_union_valves_are_selected_only_after_system_side_size | Interface facts bind to a named port before accessory selection. | next_v2_gate |
| test_pdf_dialog_evaluator.py::test_pump_connection_can_be_confirmed_by_model_not_union_thread | A model-specific passport fact can answer the stated interface predicate. | next_v2_gate |
| test_selection_contracts_nlu_p0.py::test_thermostatic_head_does_not_treat_half_inch_as_head_interface | Pipe thread size cannot substitute for thermostatic-head interface. | next_v2_gate |
| test_persona_qa_regressions_2026_07_21.py::test_complectation_question_with_sku_routes_to_complectation_not_exact_sku | A compatibility/complectation action has priority over SKU lookup. | future_capability |
| test_persona_qa_regressions_2026_07_21.py::test_project_cart_uses_shutoff_valve_not_water_meter_check_valve | Bundle selection requires Project and endpoint roles. | future_capability |
| test_project_cart_overreach_regressions.py::test_project_scope_does_not_latch_onto_later_product_requests | A project task must not capture a later independent request. | future_capability |

## Shared safety

### Stage-3 audit result

The seven historical safety cases were exercised before and after protected
V2-mode coverage.  Six water-heater failures were stale test expectations:
the guard had already stopped routing, cleared customer/product state and
delivered no cards, but the renderer retained bounded private
``_safety_*`` de-duplication metadata.  The tests now explicitly prohibit
foreign customer/product slots while allowing only that private,
non-customer-visible trace.

One behavioral defect was real.  A source-backed 380 V boundary for a known
electric boiler was lost on a repeated follow-up such as ``«А через
переходник?»`` because the generic repeat template replaced the model-specific
answer.  The shared safety renderer now preserves a source-backed 380 V fact
through the repetition.  Protected tests also prove that the water-heater
safety boundary is applied before Legacy, Shadow and V2 Preview routing.

| Legacy node ID | V2 target | Status |
| --- | --- | --- |
| test_safety_handoff_edge_regressions.py::test_electrical_followup_stays_safe_but_passport_question_is_not_hijacked | Safety intercepts only the risky instruction; a safe factual request remains reachable. | shared_safety |
| test_water_heater_operational_safety_regressions.py::test_relief_valve_drain_block_is_stopped_before_routing[plug-relief-valve] | Block unsafe relief-valve drainage instruction before routing. | shared_safety |
| test_water_heater_operational_safety_regressions.py::test_relief_valve_drain_block_is_stopped_before_routing[plug-relief-drain] | Block unsafe relief-valve drainage instruction before routing. | shared_safety |
| test_water_heater_operational_safety_regressions.py::test_relief_valve_drain_block_is_stopped_before_routing[block-relief-outlet] | Block unsafe relief-valve drainage instruction before routing. | shared_safety |
| test_water_heater_operational_safety_regressions.py::test_dry_start_is_stopped_before_routing[empty-heater] | Block an empty water-heater start before routing. | shared_safety |
| test_water_heater_operational_safety_regressions.py::test_dry_start_is_stopped_before_routing[unfilled-boiler] | Block an empty water-heater start before routing. | shared_safety |
| test_water_heater_operational_safety_regressions.py::test_dry_start_is_stopped_before_routing[power-without-water] | Block an empty water-heater start before routing. | shared_safety |

## Test-review candidates

The water-heater audit is complete.  The six slot assertions were stale only
with respect to bounded private repeat metadata; their safety behavior was
already correct.  The electrical follow-up found a real loss of a
model-specific 380 V boundary and is now protected by regression tests.  These
cases remain ``shared_safety`` because they must hold independently of the
selected answer owner.

The following complex Legacy-only scenarios remain requirements but cannot
truthfully be marked V2-ready until their capability exists:

- warm-floor dialogue and return;
- irrigation/well solution flow;
- project cart and complectation planning.

## Immediate gate sequence

1. Keep the new continuity gate green.
2. Add V2 behavioral tests for the pending-question and correction group.
3. Add V2 Selection tests for boiler, sewer, radiator and well-pump hard
   constraints.
4. Audit the seven shared safety scenarios. **Completed:** retain the shared
   boundary and its V2-mode regression coverage.
5. Only then design the structured Legacy-to-V2 owner bridge.

## Borehole-pump transfer — 31 August

The two historical borehole requirements are now covered in protected V2
Preview without changing the public Legacy route.

- V2 uses an adapter over the existing deterministic
  `normalize_engineering_slots()` calculation. It accepts typed level, lift,
  horizontal run, pressure, flow and discharge-pipe inputs; it does not use an
  LLM to calculate a head.
- Until those inputs are complete, V2 asks for the first concrete missing
  value. With otherwise complete geometry it asks for discharge-pipe diameter
  (and accepts outer PE diameter plus SDR), rather than asking a customer to
  invent a system head.
- The derived `required_head_m` and customer `required_flow_l_h` are hard
  lower bounds against separate feed ratings `max_head_m` / `max_flow_l_h`.
  The route and pipe facts are never catalogue filters.
- Both candidate ratings must exist in the current source snapshot. A card
  with only maximum head is rejected, not shown as an unverified preliminary
  candidate. This matters for feed SKU `11677`: its current card has a
  confirmed maximum head but no confirmed maximum-flow field, so V2 correctly
  declines to present it for a calculated head-and-flow request.
- Even when both ratings are present, the selection is explicitly
  preliminary: maximum ratings are not a Q/H curve and do not prove a working
  point. The renderer states that the manufacturer curve still needs checking.

Covered by:

- `test_preview_borehole_pump_asks_for_pipe_before_calculating_head`;
- `test_preview_borehole_pump_derives_preliminary_head_and_filters_ratings`;
- `test_preview_borehole_explicit_duty_stays_preliminary_not_engineering_match`;
- `test_preview_borehole_pump_does_not_show_card_without_confirmed_flow_rating`.

## Capability-aware remediation — 31 August

The live boiler, pump and pipe dialogues exposed failures at ownership seams,
not a need to copy the Legacy orchestrator into V2.  The following Legacy
requirements now have explicit V2 owners:

| Requirement inherited from Legacy | V2 owner | Enforcement |
| --- | --- | --- |
| A pump message with Q/H must not become a fitting item list. | `cutover_v2.capability_registry` + item-list boundary | Each list row needs a product noun; active typed pump facts block the unrelated item-list capability. |
| Legacy may help only with a capability V2 does not own. | `CapabilityBoundaryDecision` + `LegacyScopeBridge` | Fallback is decided before execution; accepted Legacy products are revalidated against the current source snapshot. |
| «Есть в наличии?» is not «показывай только в наличии». | `OfferFactService` and typed Selection preferences | A direct stock question keeps the exact product visible, including zero stock; only an explicit selection constraint filters candidates. |
| Spoken Q/H, DN and sewer length survive short and full turns. | `SemanticInterpreter` deterministic anchors + `SemanticTurnDeltaV1` | Registry-backed units and exact evidence override a noncanonical LLM field; reducer merge remains monotonic. |
| «Бренд не важен» clears an older brand preference only in this goal. | goal-scoped `SelectionPreferenceSignal` | `brand_any` neutralizes required/preferred brand without touching technical facts or other goals. |
| Explicit named models can be compared from the first turn. | named-product resolver + Grounded Compare | Both products must resolve exactly in the current snapshot; LLM may propose references, but catalog identity and comparison evidence decide. |
| Product-fact aliases used in Compare have one authority. | `ProductContractRegistry.canonical_fact_name` | `installation_length_mm` resolves to catalog `mounting_length_mm`; Compare does not keep a second alias dictionary. |
| A multi-fact passport question must not silently drop one predicate. | existing `ProductFactEvidenceService` bundle | Each predicate is retrieved and gated separately; an unsupported optional field does not erase accepted sibling facts. |
| «Ближайшая короче» is allowed only after explicit consent. | `length_nearest_shorter` preference + catalog planner + seller policy | Only sewer length may change, diameter/scope/stock stay hard; result is `present_controlled_analog`, never an exact recommendation. |
| Passport retrieval must fail closed on an incompatible index. | existing passport readiness/index contract | Model, source digest and chunk metadata are checked before evidence retrieval; no second index was added. |

The public route, Legacy state and canary percentage are intentionally outside
this migration.  Protected Preview can exercise the typed seam; an unsupported
future capability remains a boundary or a validated Legacy fallback rather
than an unverified V2 claim.
