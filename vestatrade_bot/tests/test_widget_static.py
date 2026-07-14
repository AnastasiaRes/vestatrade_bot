from __future__ import annotations

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
