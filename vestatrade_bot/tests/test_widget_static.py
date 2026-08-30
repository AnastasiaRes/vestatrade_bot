from __future__ import annotations

import asyncio
from collections import Counter

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main
from app.main import app


client = TestClient(app)


def _preview_request(client_host: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/widget-v2-preview",
            "headers": [],
            "query_string": b"",
            "server": ("127.0.0.1", 8010),
            "client": (client_host, 34567),
            "scheme": "http",
        }
    )


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


def test_local_v2_preview_widget_is_loopback_only_and_no_store(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "dialogue_v2_local_preview_enabled", True)
    monkeypatch.setattr(main.settings, "dialogue_v2_qa_controls_enabled", True)
    monkeypatch.setattr(main.settings, "dialogue_v2_qa_control_token", "qa-test-token")

    response = asyncio.run(main.widget_v2_preview(_preview_request("127.0.0.1")))
    page = response.body.decode("utf-8")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert '"dialogueMode": "v2_preview"' in page
    assert '"qaToken": "qa-test-token"' in page
    assert 'data-title="AI-консультант — V2 Preview"' in page
    assert 'data-subtitle="Локальный защищённый режим"' in page
    assert "/widget-loader.js" in page

    with pytest.raises(main.HTTPException) as captured:
        asyncio.run(main.widget_v2_preview(_preview_request("198.51.100.10")))
    assert captured.value.status_code == 404


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
