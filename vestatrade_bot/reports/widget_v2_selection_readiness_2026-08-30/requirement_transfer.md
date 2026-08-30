# Selection/readiness: transfer map from Legacy requirements to V2

Date: 2026-08-30
Scope: single-category product selection and its safe preliminary/no-match
outcomes. Public `/chat` remains Legacy; every listed V2 delivery is exercised
only in protected Preview or in a typed V2 contract test.

## Verification of the current transfer

- protected-Preview Selection/readiness gate: **10 passed**;
- combined V2 Selection, OfferFact, Compare, Calculate, Compatibility,
  registry/planner and pipe-service gates: **232 passed**.
- full pytest after the transfer: **2762 passed, 52 known Legacy-only failures,
  67 skipped**.  The failure IDs match the recorded baseline; the ten new V2
  gates did not introduce a new failure.

## How to read this document

This is a requirements migration map, not a claim that Legacy is now removed.
The old test names preserve the buyer problem that was discovered earlier.
The V2 column identifies the typed layer that now owns the same rule. No
Legacy response text or mutable Legacy slots are used by the V2 tests.

Statuses:

- **transferred_and_gated** — an executable V2 test currently enforces it;
- **partially_transferred** — the generic V2 mechanism exists, but the
  category-specific scenario still needs a dedicated V2 test;
- **not_transferred_yet** — the required V2 contract/capability is absent;
- **future_capability** — explicitly outside the single-category Selection
  stage.

## Requirements transferred and gated now

| Legacy requirement source | V2 invariant | V2 layer | Executable V2 coverage | Status |
| --- | --- | --- | --- | --- |
| `test_dialog_scenarios.py::test_sewer_dialog_accumulates_slots_and_asks_only_missing` | A bare pipe request returns one critical question and no random cards. | `pipe.ppr.v1` readiness + renderer + outcome gate | `test_v2_selection_readiness_gate.py::test_preview_bare_pipe_asks_one_critical_question_without_random_cards`; `test_v2_selection_characterization.py::test_bare_pipe_yields_one_typed_critical_question` | transferred_and_gated |
| `test_dialog_scenarios.py::test_sewer_dialog_uses_collected_slots_for_final_search` | External sewer requirements do not produce PPR cards; a no-match is safer than cross-category substitution. | registry/normalization → `pipe.sewer.v1` → selection outcome gate | `test_v2_selection_readiness_gate.py::test_preview_external_sewer_selection_never_substitutes_ppr`; `test_v2_selection_characterization.py::test_external_sewer_request_returns_typed_no_match_not_ppr_cards` | transferred_and_gated |
| Persona defect: `ППР 25, стекловолокно, радиаторная магистраль, 90 °C` was asked again about purpose | Confirmed pipe service, diameter, reinforcement and temperature reach V2 Selection together; service stays a pipe fact, not `radiator` product kind. | semantic repair → reducer → `pipe.ppr.v1` | `test_v2_selection_readiness_gate.py::test_preview_ppr_selection_uses_confirmed_facts_without_repeat_question`; `test_v2_selection_characterization.py::test_radiator_main_canonicalizes_pipe_service_with_provenance`; `test_pipe_service_v2.py::test_ppr_offline_pipeline_progresses_without_relaxing_hard_facts` | transferred_and_gated |
| `test_dialog_scenarios.py::test_unknown_boiler_flow_asks_voltage_after_no_gas` | Boiler fuel/circuits are hard facts; the next question follows already known facts rather than restarting the funnel. | boiler contract/readiness + typed question | `test_v2_selection_readiness_gate.py::test_preview_boiler_area_flow_keeps_facts_until_safe_preliminary_cards`; `test_catalog_v2_contracts_planner.py::test_boiler_area_first_asks_fuel_then_circuits` | transferred_and_gated |
| `test_dialog_scenarios.py::test_oversized_boiler_is_only_presented_as_nearest_assortment_option` | Exact requested conditions are searched first. A higher-power in-stock boiler may be shown only as an explicit preliminary availability analogue, with changed facts recorded. | `availability_analog` SelectionResult + source/outcome gates | `test_v2_selection_characterization.py::test_boiler_out_of_stock_exact_offers_only_safe_higher_power_analog`; `test_catalog_v2_contracts_planner.py::test_boiler_power_relaxes_only_when_preferred_and_fuel_circuits_stay_hard` | transferred_and_gated |
| `test_boiler_power_priority_regressions.py::test_arbitrary_power_pages_have_truthful_stock_and_alternative_notes` | An exact product in stock is never replaced by an analogue; stock status and product question remain separate from `in_stock_only`. | source snapshot + Selection/OfferFact/stock constraint | `test_v2_selection_readiness_gate.py::test_preview_boiler_availability_analog_keeps_fuel_and_circuits_hard`; `test_v2_selection_characterization.py::test_boiler_exact_in_stock_never_turns_into_availability_analog`; `test_catalog_v2_contracts_planner.py::test_stock_question_keeps_exact_out_of_stock_candidate_with_honest_status`; `test_catalog_v2_contracts_planner.py::test_explicit_stock_relaxation_removes_filter_for_same_goal` | transferred_and_gated |
| `test_live_catalog_goal_switch_regressions.py::test_radiator_type_correction_and_explicit_stock_relaxation_do_not_mix` | A stock filter is a typed fact of exactly one goal; another goal cannot inherit or clear it. | goal-scoped reducer + contract planner | `test_catalog_v2_contracts_planner.py::test_stock_requirement_is_scoped_to_its_product_goal`; `test_v2_goal_scoped_context.py::test_explicit_return_restores_goal_scoped_cards_before_ordinal_resolution` | transferred_and_gated (generic V2 gate) |
| `test_engineering_requirements_regressions.py::test_well_pump_components_without_calculated_head_do_not_unlock_products` | A circulation pump is not shown before both working-point facts are known. | pump readiness/outcome gate | `test_v2_selection_characterization.py::test_circulation_pump_does_not_show_before_both_duty_point_facts`; `test_catalog_v2_contracts_planner.py::test_pump_duty_point_requires_curve_and_is_never_compared_to_maxima` | transferred_and_gated for circulation pumps |
| `test_engineering_requirements_regressions.py::test_well_pump_flow_and_head_are_hard_filters` | Confirmed duty-point values are hard constraints, not ranking preferences. | pump contract/planner | `test_v2_selection_characterization.py::test_pump_show_command_produces_verified_structured_cards`; `test_catalog_v2_contracts_planner.py::test_exact_pump_plan_enforces_every_hard_constraint` | transferred_and_gated for circulation pumps |
| `test_boiler_safety_context_regressions.py::test_electric_choice_with_repeated_constraints_keeps_context_and_does_not_loop` | Repeating the fuel answer does not erase the already known area or re-ask fuel; V2 continues with the one still-critical question about DHW. | reducer → boiler readiness → renderer | `test_v2_selection_readiness_gate.py::test_preview_repeated_electric_choice_does_not_forget_area_or_reask_fuel` | transferred_and_gated |
| `test_dialog_scenarios.py::test_more_boilers_does_not_reset_pending_type_and_area` | `Какие ещё котлы есть?` preserves fuel and area. It cannot bypass the still-required one-/two-circuit decision and show an unsafe mixed boiler list. | boiler contract/readiness + renderer | `test_v2_selection_readiness_gate.py::test_preview_more_boilers_keeps_known_type_and_area_until_circuits_answered` | transferred_and_gated |
| `test_assisted_unknown_selection_regressions_2026_08_24.py::test_spoken_area_and_first_turn_unknowns_start_preliminary_radiator_flow` — initial card scope | A stated room area is compared only with the explicit manufacturer field `площадь обогрева`; cards below that declared coverage are not shown. The result stays preliminary and is not a heat-loss calculation. | shared `area_m2` → `declared_heated_area_m2` source projection → `radiator.v1` → selection outcome gate | `test_v2_selection_readiness_gate.py::test_preview_radiator_room_area_is_only_a_source_backed_preliminary_proxy` | transferred_and_gated for the initial preliminary shortlist |
| `test_live_catalog_goal_switch_regressions.py::test_plain_radiator_type_without_size_still_requires_size` | A bare radiator request asks one physical mounting dimension and never searches all radiators. | `radiator.v1` readiness ordering + renderer | `test_v2_selection_readiness_gate.py::test_preview_bare_radiator_starts_with_physical_size_not_material` | transferred_and_gated |
| `test_dialog_scenarios.py::test_boiler_consultation_remembers_shorthand_area_and_uses_passport` — Selection continuity portion | A boiler displayed by V2 remains the authoritative customer-visible scope for the next ordinal direct fact; the fact response preserves, rather than replaces, the selection. | delivered selection scope → existing ProductFact path → cutover telemetry | `test_v2_selection_readiness_gate.py::test_preview_boiler_selection_keeps_scope_for_following_direct_fact` | transferred_and_gated for selection → card-backed ProductFact continuity |

## Requirements deliberately not claimed as migrated yet

| Legacy requirement source | Why it is not simply reused | Required V2 destination | Status |
| --- | --- | --- | --- |
| `test_dialog_scenarios.py::test_boiler_consultation_remembers_shorthand_area_and_uses_passport` — passport range portion | Card-backed Selection → ProductFact continuity is covered, but the exact Legacy question about a passport-only *power range* still needs a dedicated document-scoped V2 test on one safely mapped boiler. | boiler Selection → passport-backed ProductFact V2 gate | partially_transferred |
| Continuing explanations in `test_assisted_unknown_selection_regressions_2026_08_24.py::test_spoken_area_and_first_turn_unknowns_start_preliminary_radiator_flow` | The initial V2 shortlist is now covered, but the Legacy-only long answers about central-system pressure, thermal sizing and the meaning of manufacturer area are not a Selection capability. They need a bounded Rationale/educational-answer contract instead of copying Legacy prose. | future `RATIONALE` with source-backed card facts | future_capability |
| `test_live_dialogue_regressions_2026_08_24.py::test_d10_ordinary_request_is_not_filtered_away` | Requires an explicit typed meaning for `D10` in the relevant product contracts; a global text exception would be unsafe. | category-specific diameter normalizer + contract test | not_transferred_yet |
| `test_live_dialog_context_memory_regressions.py::test_irrigation_well_dialog_keeps_context_and_estimates_standard_hose` | This is a multi-step engineering solution, not a single product selection. | Project/well-pump capability | future_capability |
| `test_live_dialog_context_memory_regressions.py::test_irrigation_well_accepts_several_facts_in_one_natural_turn` | Requires the same well-pump/project contract. | Project/well-pump capability | future_capability |
| `test_live_dialog_context_memory_regressions.py::test_warm_floor_nonexpert_yes_to_room_control_advances_live_dialog` | Requires a multi-product solution plan and controller facts. | Project/warm-floor capability | future_capability |

## Boundary retained from the first V2 migration stage

Selection does not take ownership of direct facts, arithmetic, comparison or
compatibility merely because they follow a selection:

| Customer need | V2 owner | Existing executable gate |
| --- | --- | --- |
| price / stock / link of a shown card | `OfferFactService` | `test_v2_historical_continuity_gate.py`, `test_grounded_v2_offer_fact.py` |
| quantity × source-backed price | `CalculationService` | `test_grounded_v2_calculation.py` |
| differences between shown cards | `ComparisonResult` | `test_grounded_v2_comparison.py` |
| proof-based interface verdict | `CompatibilityResult` | `test_grounded_v2_compatibility.py` |

This prevents a Selection fallback from masking a missing Compare, Calculate,
Compatibility or ProductFact capability.

## Changes made after observing the Preview gates

The gates above revealed two contract decisions that are now explicit in the
single V2 registry rather than hidden in Legacy code:

| Requirement | Where it was transferred | Why this is safe |
| --- | --- | --- |
| A boiler list must not collapse one- and two-circuit models into one preliminary answer. | `app/catalog_v2/registry.py` → `CIRCUITS.preliminary_allowed_without=False`. | The current renderer does not present separate circuit groups. Fuel and circuits therefore remain hard; area still permits a preliminary *after* both are known. |
| A room area can be used to narrow a radiator list only against a manufacturer-declared coverage. | `app/catalog_v2/registry.py` → shared `DECLARED_HEATED_AREA` fields and `radiator.v1` projection from `area_m2`. | It carries source provenance, excludes cards below the stated area, and always marks the result preliminary. It is never converted to watts or a heat-loss verdict. |
| A bare radiator query needs an installation dimension first. | `radiator.v1.preliminary_identity_fact_groups` ordering. | This changes only the one typed next question; it does not invent a size or show random cards. |
| A direct fact after Selection must remain auditable as V2 and preserve ordinal scope. | `ProductFactDelivery` on `V2TurnCandidate`, surfaced as `cutover_v2.product_fact_delivery`. | Telemetry now records canonical SKU, predicate, value, source, verifier result and the fact that the delivered selection scope was preserved. It does not create a second ProductFact service. |

## Next narrow implementation unit

Port the remaining passport-specific portion of the two-turn boiler scenario:
shorthand area and category facts must survive a later document-scoped question
about a shown boiler's power range.  It must exercise the existing ProductFact
evidence gate, not a Legacy response composer.  The well-pump and warm-floor
scenarios remain out of this stage and must not be forced through generic
Selection.
