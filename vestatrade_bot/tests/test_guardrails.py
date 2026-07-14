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
