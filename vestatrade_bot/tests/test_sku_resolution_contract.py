from __future__ import annotations

from pathlib import Path

from app.agents.feed_search import FeedSearchAgent
from app.agents.orchestrator import ChatOrchestrator
from app.agents.semantic_interpreter import (
    SemanticInterpretationResult,
    TurnUnderstanding,
)
from app.answer_v2.contracts import AnswerSourceSnapshot
from app.answer_v2.sources import build_answer_source_snapshot
from app.catalog_v2.contracts import (
    CatalogFact,
    CatalogProductRole,
    CatalogProductSnapshot,
    FactProvenance,
    FactStrength,
    ProductKind,
    ReadinessFact,
    ReadinessStatus,
    TaskReadinessAssessment,
)
from app.catalog_v2.planner import _make_search_plan
from app.catalog_v2.normalization import build_catalog_snapshot
from app.catalog_v2.registry import ProductContractRegistry
from app.dialogue_v2.contracts import TurnMetadata
from app.dialogue_v2.controller import DialogueControllerV2
from app.feed_loader import FeedLoader
from app.models import Product, SearchQuery
from app.sku_resolution import (
    SkuResolutionStatus,
    extract_explicit_sku_tokens,
    resolve_catalog_sku,
)


FEED100 = Path(__file__).parents[1] / "data/feed_showcase_100_2026-06-14.xml"


def _product(sku: str, name: str | None = None) -> Product:
    return Product(
        sku=sku,
        name=name or f"Товар {sku}",
        category_path="Арматура для радиаторов",
        price=100,
        stock_status="в наличии",
        stock_qty=5,
        url=f"https://example.test/{sku}",
        attributes_normalized={"тип товара": "термостатическая головка"},
    )


def test_partial_sku_resolver_distinguishes_exact_unique_and_ambiguous() -> None:
    products = [
        _product("VT.1500.0.0"),
        _product("VT.214.N.04"),
        _product("VT.214.N.05"),
    ]

    exact = resolve_catalog_sku("vt-1500-0-0", products)
    unique = resolve_catalog_sku("VT.1500", products)
    spoken = resolve_catalog_sku("вт 1500", products)
    ambiguous = resolve_catalog_sku("VT.214", products)

    assert exact.status == SkuResolutionStatus.EXACT
    assert exact.canonical_sku == "VT.1500.0.0"
    assert unique.status == SkuResolutionStatus.UNIQUE_PREFIX
    assert unique.canonical_sku == "VT.1500.0.0"
    assert spoken.status == SkuResolutionStatus.UNIQUE_PREFIX
    assert ambiguous.status == SkuResolutionStatus.AMBIGUOUS_PREFIX
    assert [item.sku for item in ambiguous.candidates] == [
        "VT.214.N.04",
        "VT.214.N.05",
    ]


def test_feed100_has_expected_unique_and_ambiguous_series_boundaries() -> None:
    products = FeedLoader().parse_xml(FEED100.read_bytes())

    unique = resolve_catalog_sku("VT.1500", products)
    ambiguous = resolve_catalog_sku("VT.214", products)

    assert len(products) == 100
    assert unique.status == SkuResolutionStatus.UNIQUE_PREFIX
    assert unique.canonical_sku == "VT.1500.0.0"
    assert ambiguous.status == SkuResolutionStatus.AMBIGUOUS_PREFIX
    assert {item.sku for item in ambiguous.candidates} == {
        "VT.214.N.04",
        "VT.214.N.05",
        "VT.214.N.06",
        "VT.214.N.07",
        "VT.214.N.09",
    }


def test_partial_sku_resolver_never_uses_character_prefix_or_plain_substring() -> None:
    products = [_product("VT.1500.0.0"), _product("ABC-12345-X")]

    assert resolve_catalog_sku("VT.15", products).status == SkuResolutionStatus.NONE
    assert resolve_catalog_sku("1500", products).status == SkuResolutionStatus.NONE
    assert resolve_catalog_sku("VT", products).status == SkuResolutionStatus.NONE
    assert (
        resolve_catalog_sku("ABC-12345", products).status
        == SkuResolutionStatus.NONE
    )


def test_explicit_sku_token_extraction_keeps_numeric_articles_out_of_measurements() -> None:
    assert extract_explicit_sku_tokens(
        "У котла Arderia E9 2202210 сколько контуров?"
    ) == ("2202210",)
    assert extract_explicit_sku_tokens("Дом 150 м2, котёл 9 кВт") == ()


def test_duplicate_exact_catalogue_identity_fails_closed() -> None:
    result = resolve_catalog_sku(
        "VT.1500.0.0",
        [_product("VT.1500.0.0"), _product("vt-1500-0-0")],
    )

    assert result.status == SkuResolutionStatus.AMBIGUOUS_PREFIX
    assert result.reason_code == "duplicate_exact_identity"


def test_feed_search_uses_unique_prefix_but_fails_closed_on_ambiguity() -> None:
    products = [
        _product("VT.1500.0.0"),
        _product("VT.214.N.04"),
        _product("VT.214.N.05"),
    ]
    search = FeedSearchAgent(products)

    unique = search.search(
        SearchQuery(original_text="VT.1500", sku="VT.1500", category="other")
    )
    ambiguous = search.search(
        SearchQuery(original_text="VT.214", sku="VT.214", category="other")
    )

    assert [item.sku for item in unique] == ["VT.1500.0.0"]
    assert ambiguous == []


def test_legacy_dialogue_explains_unique_prefix_and_lists_ambiguous_variants() -> None:
    products = [
        _product("VT.1500.0.0", "Головка термостатическая VT.1500"),
        _product("VT.214.N.04", "Кран VT.214 1/2"),
        _product("VT.214.N.05", "Кран VT.214 3/4"),
    ]
    bot = ChatOrchestrator(products=products)

    unique = bot.handle_chat("partial-sku-unique", "Покажи артикул VT.1500")
    ambiguous = bot.handle_chat("partial-sku-ambiguous", "Покажи артикул VT.214")

    assert [card.sku for card in unique.products] == ["VT.1500.0.0"]
    assert "однозначно" in unique.answer.casefold()
    assert ambiguous.products == []
    assert "VT.214.N.04" in ambiguous.answer
    assert "VT.214.N.05" in ambiguous.answer
    assert "неоднозначно" in ambiguous.answer.casefold()


def _catalog_snapshot(sku: str) -> CatalogProductSnapshot:
    provenance = FactProvenance(
        source="identity",
        source_field="sku",
        raw_value=sku,
        parser="catalog_identity",
    )
    return CatalogProductSnapshot(
        sku=sku,
        name=f"Товар {sku}",
        category="radiator_fittings",
        product_kind=ProductKind.THERMOSTATIC_HEAD,
        role=CatalogProductRole.COMPONENT,
        stock_status="в наличии",
        stock_qty=5,
        facts=(CatalogFact(name="sku", value=sku, provenance=provenance),),
    )


def _sku_assessment(value: str) -> TaskReadinessAssessment:
    return TaskReadinessAssessment(
        task_id="task-sku",
        goal_id="goal-sku",
        contract_id="radiator.thermostatic_head.v1",
        product_kind=ProductKind.THERMOSTATIC_HEAD,
        status=ReadinessStatus.EXACT_READY,
        confirmed_hard_facts=(
            ReadinessFact(
                name="sku",
                status="known",
                value=value,
                strength=FactStrength.HARD,
            ),
        ),
    )


def _semantic_sku(value: str) -> SemanticInterpretationResult:
    understanding = TurnUnderstanding.model_validate(
        {
            "schema_version": "1.0",
            "language": "ru",
            "operation": "new",
            "acts": ["find"],
            "products": [
                {
                    "text": "термоголовка",
                    "canonical_type": "thermostatic_head",
                    "category": "radiator_fittings",
                    "role": "target",
                    "evidence": "термоголовка",
                }
            ],
            "constraints": [
                {
                    "name": "sku",
                    "value": value,
                    "unit": None,
                    "status": "known",
                    "polarity": "required",
                    "applies_to_product": 0,
                    "evidence": value,
                }
            ],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "answers_pending_question": False,
            "confidence": 0.95,
        }
    )
    return SemanticInterpretationResult(
        status="accepted",
        requested=True,
        transport_succeeded=True,
        output_accepted=True,
        understanding=understanding,
    )


def test_v2_catalog_planner_uses_same_prefix_resolution_contract() -> None:
    contract = ProductContractRegistry().get("radiator.thermostatic_head.v1")
    assert contract is not None
    snapshot = (
        _catalog_snapshot("VT.1500.0.0"),
        _catalog_snapshot("VT.214.N.04"),
        _catalog_snapshot("VT.214.N.05"),
    )

    unique = _make_search_plan(_sku_assessment("VT.1500"), contract, snapshot)
    ambiguous = _make_search_plan(_sku_assessment("VT.214"), contract, snapshot)

    assert "sku_resolution_unique_prefix" in unique.reason_codes
    assert unique.hard_constraints[0].value == "VT.1500.0.0"
    assert unique.eligible_skus == ("VT.1500.0.0",)
    assert ambiguous.eligible_skus == ()
    assert "sku_resolution_ambiguous_prefix" in ambiguous.reason_codes
    assert "ambiguous_sku_prefix" in ambiguous.reason_codes


def test_v2_renders_and_validates_an_ambiguous_partial_sku_boundary() -> None:
    outcome = DialogueControllerV2().run(
        None,
        _semantic_sku("VT.214"),
        TurnMetadata(turn_id="partial-sku-v2"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=(
            _catalog_snapshot("VT.214.N.04"),
            _catalog_snapshot("VT.214.N.05"),
        ),
        answer_plan_enabled=True,
        response_renderer_enabled=True,
        response_grounding_enabled=True,
        answer_source_snapshot=AnswerSourceSnapshot(source_revision="test"),
    )

    assert outcome.stage5_error is None
    assert outcome.response_rendering is not None
    assert outcome.response_rendering.rendered_answer is not None
    assert "сокращение артикула" in (
        outcome.response_rendering.rendered_answer.text.casefold()
    )
    assert "уточните полный артикул" in (
        outcome.response_rendering.rendered_answer.text.casefold()
    )
    assert outcome.grounding_validation is not None
    assert outcome.grounding_validation.status == "accepted"


def test_v2_transparently_renders_unique_partial_sku_canonicalization() -> None:
    product = _product(
        "VT.1500.0.0",
        "Термоголовка VALTEC M30x1,5",
    )
    snapshot = build_catalog_snapshot([product])
    sources = build_answer_source_snapshot([product], snapshot)

    outcome = DialogueControllerV2().run(
        None,
        _semantic_sku("VT.1500"),
        TurnMetadata(turn_id="partial-sku-v2-unique"),
        product_contracts_enabled=True,
        catalog_planner_enabled=True,
        catalog_snapshot=snapshot,
        answer_plan_enabled=True,
        response_renderer_enabled=True,
        response_grounding_enabled=True,
        answer_source_snapshot=sources,
    )

    assert outcome.stage5_error is None
    assert outcome.response_rendering is not None
    assert outcome.response_rendering.rendered_answer is not None
    answer = outcome.response_rendering.rendered_answer.text.casefold()
    assert "vt.1500.0.0" in answer
    assert "сокращенный артикул однозначно" in answer.replace("ё", "е")
    assert outcome.grounding_validation is not None
    assert outcome.grounding_validation.status == "accepted"
