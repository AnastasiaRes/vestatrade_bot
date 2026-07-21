from __future__ import annotations

from app.agents.consultant import ConsultantAgent
from app.agents.guardrails import GuardrailsAgent
from app.agents.product_card import ProductCardAgent
from app.models import Product, ProductCard, SearchQuery
from app.agents.utils import normalize_sku


def test_product_without_url_is_not_carded() -> None:
    product = Product(
        sku="NOURL",
        name="Товар без ссылки",
        price=10,
        stock_status="в наличии",
    )

    card = ProductCardAgent().build_card(product, SearchQuery(original_text="товар"))

    assert card is None


def test_product_card_decodes_html_entities() -> None:
    product = Product(
        sku="HTML-NAME",
        name="Кран 3/4&quot; вн.-вн.",
        url="https://example.test/html-name",
        price=100,
        stock_status="в наличии",
    )

    card = ProductCardAgent().build_card(
        product,
        SearchQuery(original_text="кран", category="valves"),
    )

    assert card is not None
    assert "&quot;" not in card.name
    assert '3/4"' in card.name


def test_product_card_hides_conflicting_internal_sku() -> None:
    product = Product(
        sku="11096641001",
        name="Труба полипропиленовая",
        url="https://example.test/pipe",
        price=100,
        stock_status="в наличии",
        attributes_normalized={"артикул": "12662421001", "диаметр (мм)": "25"},
    )

    card = ProductCardAgent().build_card(
        product,
        SearchQuery(original_text="труба 25", category="pipes"),
    )

    assert card is not None
    assert "артикул" not in card.characteristics
    assert card.characteristics["диаметр (мм)"] == "25"


def test_guardrail_reads_boiler_power_from_unit_in_attribute_key() -> None:
    product = Product(
        sku="SOLO-3",
        name="Котёл электрический ZOTA Solo - 3",
        category_path="Котлы электрические",
        url="https://example.test/solo3",
        price=25000,
        stock_status="в наличии",
        attributes_normalized={"мощность, квт": "3"},
    )

    assert GuardrailsAgent()._extract_power_kw(product) == 3


def test_guardrails_reject_invented_characteristic(sample_products: list[Product]) -> None:
    product = sample_products[0]
    card = ProductCard(
        sku=product.sku,
        name=product.name,
        brand=product.brand,
        price=product.price or 0,
        currency=product.currency,
        stock_status=product.stock_status,
        stock_qty=product.stock_qty,
        url=product.url or "",
        characteristics={"мощность": "99 кВт"},
    )

    result = GuardrailsAgent().validate_cards(
        [card],
        [product],
        SearchQuery(original_text="кран", category="valves"),
    )

    assert not result.ok
    assert any("invented characteristic" in issue for issue in result.issues)


def test_context_guard_allows_model_reference_present_in_card() -> None:
    context = (
        "1. Адаптер для сервопривода (для VTc.589) (артикул VT.AC674.V.0)\n"
        "   Цена: 100 RUB. Наличие: в наличии."
    )

    result = GuardrailsAgent().validate_context_answer(
        "Артикул VT.AC674.V.0; адаптер предназначен для VTc.589.",
        context,
    )

    assert result.ok


def test_context_guard_rejects_reference_model_as_primary_article() -> None:
    context = (
        "1. Адаптер для сервопривода (для VTc.589) (артикул VT.AC674.V.0)\n"
        "   Цена: 100 RUB. Наличие: в наличии."
    )

    result = GuardrailsAgent().validate_context_answer(
        "Артикул: VTc.589.",
        context,
    )

    assert not result.ok
    assert "invented sku: vtc.589" in result.issues


def test_guardrails_reject_unsorted_cheap(sample_products: list[Product]) -> None:
    products = [product for product in sample_products if product.sku in {"PUMP-25-40", "PUMP-25-60"}]
    cards = [
        ProductCardAgent().build_card(product, SearchQuery(original_text="насос", category="pumps"))
        for product in reversed(products)
    ]

    result = GuardrailsAgent().validate_cards(
        [card for card in cards if card is not None],
        products,
        SearchQuery(original_text="насос подешевле", category="pumps", cheap=True),
    )

    assert not result.ok
    assert "cheap request was not sorted by ascending price" in result.issues


def test_complectation_requires_feed_confirmation(sample_products: list[Product]) -> None:
    product = next(product for product in sample_products if product.sku == "ARD-E9")

    result = GuardrailsAgent().validate_complectation_answer(product, ["насос", "бак"])

    assert not result.ok
    assert result.safe_message
    assert "Не вижу подтверждения комплектации" in result.safe_message


def test_indirect_pump_mention_does_not_confirm_builtin_pump() -> None:
    product = Product(
        sku="BOILER-INDIRECT",
        name="Котёл со встроенным трёхходовым клапаном",
        category_path="Котлы",
        url="https://example.test/boiler",
        price=10000,
        stock_status="в наличии",
        description=(
            "Встроенный трёхходовой клапан. Для подключения внешнего насоса "
            "используйте отдельный клеммный блок."
        ),
    )
    guardrails = GuardrailsAgent()

    result = guardrails.validate_complectation_answer(product, ["насос"])
    components = guardrails.list_builtin_components(product)

    assert result.ok is False
    assert "циркуляционный насос" not in components


def test_consultant_attaches_card_when_model_is_named_without_sku() -> None:
    product = Product(
        sku="2201375",
        name="Котёл газовый Arderia SB24 24 кВт",
        url="https://example.test/sb24",
        price=35000,
        stock_status="в наличии",
        stock_qty=2,
    )
    by_sku = {normalize_sku(product.sku): product}

    cards = ConsultantAgent()._cited_cards("Показываю Arderia SB24 за 35000 RUB.", by_sku)

    assert [card.sku for card in cards] == ["2201375"]


def test_consultant_rejects_invented_url_and_stock() -> None:
    product = Product(
        sku="2201375",
        name="Котёл газовый Arderia SB24 24 кВт",
        url="https://example.test/sb24",
        price=35000,
        stock_status="в наличии",
        stock_qty=2,
    )
    by_sku = {normalize_sku(product.sku): product}

    issues = ConsultantAgent()._grounding_violations(
        "Arderia SB24: 9 шт., https://fake.example/sb24",
        by_sku,
    )

    assert any("ссылка" in issue for issue in issues)
    assert any("остаток" in issue for issue in issues)


def test_consultant_rejects_product_brand_not_in_catalog() -> None:
    product = Product(
        sku="2201375",
        name="Котёл газовый Arderia SB24 24 кВт",
        brand="Arderia",
        url="https://example.test/sb24",
        price=35000,
        stock_status="в наличии",
        stock_qty=2,
    )
    by_sku = {normalize_sku(product.sku): product}

    issues = ConsultantAgent()._grounding_violations(
        "Котёл Ariston CLAS не в наличии. Котёл Arderia SB24 есть в каталоге.",
        by_sku,
    )

    assert any("Ariston" in issue for issue in issues)
    assert not any("Arderia" in issue for issue in issues)


def test_consultant_rejects_wrong_boiler_type_for_real_product() -> None:
    product = Product(
        sku="3301679",
        name="Котел газовый Ariston CLAS XC SYSTEM 24 FF NG",
        category_path="Котлы газовые",
        brand="Ariston",
        url="https://example.test/ariston-clas",
        price=78571,
        stock_status="нет в наличии",
        attributes_normalized={"тип котла": "Газовый"},
    )
    by_sku = {normalize_sku(product.sku): product}
    consultant = ConsultantAgent()

    wrong = consultant._grounding_violations(
        "Электрический котёл Ariston CLAS, артикул 3301679, цена 78571 RUB.",
        by_sku,
    )
    correct = consultant._grounding_violations(
        "Газовый котёл Ariston CLAS, артикул 3301679, цена 78571 RUB.",
        by_sku,
    )

    assert any("тип котла" in issue for issue in wrong)
    assert not any("тип котла" in issue for issue in correct)


def test_context_guard_rejects_compact_invented_sku() -> None:
    result = GuardrailsAgent().validate_context_answer(
        "Артикул CMSR99ZZ99, цена по запросу.",
        "Котёл электрический. Артикул CMSR02CA28.",
    )

    assert not result.ok
    assert any("invented sku" in issue for issue in result.issues)


def test_context_guard_rejects_prefix_of_real_sku() -> None:
    context = (
        "1. Насос тестовый (артикул ABC-12345-X)\n"
        "   Цена: 100 RUB. Наличие: в наличии, 1 шт."
    )
    guard = GuardrailsAgent()

    hyphenated = guard.validate_context_answer(
        "Артикул ABC-12345 — это показанный насос.",
        context,
    )
    compact = guard.validate_context_answer(
        "SKU: ABC12345.",
        context,
    )

    assert not hyphenated.ok
    assert not compact.ok
    assert any("invented sku" in issue for issue in hyphenated.issues)
    assert any("invented sku" in issue for issue in compact.issues)


def test_context_guard_accepts_valid_sku_with_sentence_period_and_card_word() -> None:
    context = (
        "Карточка товара. Артикул: VT.217.N.04.\n"
        "Цена: 100 RUB. Наличие: в наличии, 1 шт."
    )

    result = GuardrailsAgent().validate_context_answer(
        "По карточке проверьте назначение. Артикул: VT.217.N.04.",
        context,
    )

    assert result.ok, result.issues


def test_clarification_rewrite_must_keep_numeric_voltage_options() -> None:
    draft = "Какое питание доступно для котла: 220 или 380 В?"

    result = GuardrailsAgent().validate_response_text(
        draft,
        "К какому типу электросети подключается котёл?",
        mode="clarification",
    )

    assert not result.ok
    assert result.safe_message == draft
    assert any("220" in issue for issue in result.issues)
    assert any("380" in issue for issue in result.issues)


def test_context_guard_rejects_combustion_chamber_for_electric_boiler() -> None:
    guard = GuardrailsAgent()
    context = "Котёл электрический Arderia E12. Артикул ARD-E12."

    wrong = guard.validate_context_answer(
        "У Arderia E12 закрытая камера сгорания.",
        context,
    )
    correct = guard.validate_context_answer(
        "У электрического Arderia E12 нет камеры сгорания.",
        context,
    )

    assert not wrong.ok
    assert any("combustion chamber" in issue for issue in wrong.issues)
    assert correct.ok
