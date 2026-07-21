from __future__ import annotations

from app.feed_loader import FeedLoader


def test_parse_unixml_offer_fields() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <yml_catalog date="2026-05-26 18:19">
      <shop>
        <offers>
          <offer id="212">
            <name>Термоголовка</name>
            <url>https://example.test/product</url>
            <currencyId>RUB</currencyId>
            <category>Арматура для радиаторов</category>
            <vendorCode>VT.1500.0.0</vendorCode>
            <price>1044</price>
            <quantity>26</quantity>
            <picture>https://example.test/image.jpg</picture>
            <vendor>VALTEC</vendor>
            <description><![CDATA[Описание товара]]></description>
            <param name="Артикул">VT.1500.0.0</param>
            <param name="Материал">Латунь</param>
          </offer>
        </offers>
      </shop>
    </yml_catalog>
    """.encode("utf-8")
    products = FeedLoader().parse_xml(xml)

    assert len(products) == 1
    product = products[0]
    assert product.sku == "VT.1500.0.0"
    assert product.url == "https://example.test/product"
    assert product.price == 1044
    assert product.stock_qty == 26
    assert product.stock_status == "в наличии"
    assert product.attributes_normalized["материал"] == "Латунь"


def test_parse_fallback_article_from_param() -> None:
    xml = """<root><item id="fallback-1">
      <name>Товар без vendorCode</name>
      <url>https://example.test/fallback</url>
      <price>10.50</price>
      <quantity>0</quantity>
      <param name="Артикул">ABC-1</param>
    </item></root>""".encode("utf-8")

    product = FeedLoader().parse_xml(xml)[0]

    assert product.sku == "ABC-1"
    assert product.stock_status == "нет в наличии"


def test_parse_recovers_valid_param_article_from_placeholder_vendor_code() -> None:
    xml = """<root><item id="placeholder-with-article">
      <name>Товар с валидным внутренним артикулом</name>
      <vendorCode>?</vendorCode>
      <param name="Артикул">27700002</param>
    </item></root>""".encode("utf-8")

    product = FeedLoader().parse_xml(xml)[0]

    assert product.sku == "27700002"
    assert product.attributes_normalized["артикул"] == "27700002"


def test_parse_skips_placeholder_and_conflicting_skus() -> None:
    xml = """<root>
      <item id="placeholder"><name>Без артикула</name><vendorCode>?</vendorCode></item>
      <item id="one"><name>Труба канализационная</name><vendorCode>DUP-1</vendorCode></item>
      <item id="two"><name>Водонагреватель</name><vendorCode>DUP-1</vendorCode></item>
      <item id="good"><name>Насос циркуляционный</name><vendorCode>GOOD-1</vendorCode></item>
    </root>""".encode("utf-8")

    products = FeedLoader().parse_xml(xml)

    assert [product.sku for product in products] == ["GOOD-1"]


def test_parse_collapses_same_product_duplicate_and_prefers_stock() -> None:
    xml = """<root>
      <item id="one">
        <name>Насос циркуляционный</name><vendorCode>PUMP-1</vendorCode>
        <url>https://example.test/pump</url><price>100</price><quantity>0</quantity>
      </item>
      <item id="two">
        <name>Насос циркуляционный</name><vendorCode>PUMP-1</vendorCode>
        <url>https://example.test/pump</url><price>100</price><quantity>3</quantity>
      </item>
    </root>""".encode("utf-8")

    products = FeedLoader().parse_xml(xml)

    assert len(products) == 1
    assert products[0].sku == "PUMP-1"
    assert products[0].stock_qty == 3


def test_parse_removes_conflicting_internal_article_and_foreign_description() -> None:
    xml = """<root>
      <item id="boiler">
        <name>Котёл двухконтурный Model A</name><vendorCode>MODEL-A</vendorCode>
        <param name="Артикул">MODEL-B</param>
        <param name="Количество контуров">Двухконтурный</param>
        <description>Одноконтурный котёл, артикул MODEL-B.</description>
      </item>
    </root>""".encode("utf-8")

    products = FeedLoader().parse_xml(xml)

    assert len(products) == 1
    assert "артикул" not in products[0].attributes_normalized
    assert products[0].attributes_normalized["количество контуров"] == "Двухконтурный"
    assert products[0].description is None
