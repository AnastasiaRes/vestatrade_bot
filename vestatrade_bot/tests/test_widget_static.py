from __future__ import annotations

from collections import Counter

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_widget_loader_is_served() -> None:
    response = client.get("/widget-loader.js")

    assert response.status_code == 200
    assert "application/javascript" in response.headers["content-type"]
    assert "attachShadow" in response.text
    assert "/chat" in response.text
    assert "Подберите циркуляционный насос подешевле" in response.text
    assert "Подберите электрический котёл для дома площадью 100 м²" in response.text
    assert "Дайте ссылку на товар" in response.text
    assert "data.dialogueMode" in response.text
    assert "X-Dialogue-QA-Token" in response.text
    assert "requestBody.qa_mode" in response.text


def test_widget_demo_is_served() -> None:
    response = client.get("/widget-demo")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "/widget-loader.js" in response.text
    assert "Нужен шаровой кран BASE со стальной рукояткой, 1/2″, ВР/ВР" in response.text
    assert "Позовите консультанта" in response.text
    assert "Как связаться с менеджером?" in response.text

    misspellings = ("шаровои", "сталная", "пазови", "кансультанта", "свезаться", "минеджером")
    assert not any(misspelling in response.text.lower() for misspelling in misspellings)


def test_widget_demo_catalog_is_served() -> None:
    response = client.get("/static/widget-demo-catalog.json")

    assert response.status_code == 200
    catalog = response.json()

    products = catalog["products"]
    assert len(products) == catalog["total"] == 100
    assert len({product["sku"] for product in products}) == len(products)
    for product in products:
        assert product["name"] and product["sku"]
        assert product["url"].startswith("https://")
        assert product["image"].startswith("https://")
        assert isinstance(product["price"], (int, float)) and product["price"] > 0
        assert product["tags"]

    # Каждая позиция попала ровно в один раздел, и счётчики сайдбара считаются
    # по фактическому содержимому — «Все 99» при 100 карточках больше невозможно.
    counts = Counter(product["category"] for product in products)
    assert {category["id"] for category in catalog["categories"]} == set(counts)
    assert all(
        category["count"] == counts[category["id"]] for category in catalog["categories"]
    )
    assert sum(category["count"] for category in catalog["categories"]) == len(products)
    assert "other" not in counts


def test_widget_demo_catalog_matches_feed() -> None:
    """Собранная витрина не должна отставать от фида, из которого сделана."""
    from scripts.build_widget_demo_catalog import DEFAULT_FEED, DEFAULT_OUT, build_catalog, render

    assert DEFAULT_OUT.read_text(encoding="utf-8") == render(build_catalog(DEFAULT_FEED))
