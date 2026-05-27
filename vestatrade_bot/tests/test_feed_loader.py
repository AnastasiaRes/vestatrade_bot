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
