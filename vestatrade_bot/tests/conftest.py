from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ["LLM_PROVIDER"] = "disabled"
# os.environ["OPENROUTER_API_KEY"] = ""  # старый OpenRouter-режим, оставлен для отката

from app.agents.orchestrator import ChatOrchestrator
from app.models import Product


@pytest.fixture
def sample_products() -> list[Product]:
    return [
        Product(
            sku="VT.228.N.04",
            name='Кран шаровой BASE угловой с полусгоном 1/2"',
            category_path="Краны шаровые",
            brand="VALTEC",
            url="https://example.test/vt228",
            price=805,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=4,
            attributes_normalized={
                "артикул": "VT.228.N.04",
                "назначение": "Вода, отопление",
                "диаметр": '1/2"',
                "тип присоединения": "с американкой",
            },
        ),
        Product(
            sku="VTp.700.0.020",
            name="Труба PPR 20 мм PN20",
            category_path="Трубы полипропиленовые",
            brand="VALTEC",
            url="https://example.test/pipe20",
            price=120,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=50,
            attributes_normalized={
                "артикул": "VTp.700.0.020",
                "материал": "Полипропилен",
                "назначение": "Водоснабжение, Отопление",
                "диаметр (мм)": "20",
            },
        ),
        Product(
            sku="HTEM-50-500",
            name="Труба канализационная внутренняя 50x500",
            category_path="Канализация внутренняя",
            brand="OSTENDORF",
            url="https://example.test/sewer50500",
            price=180,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=8,
            attributes_normalized={
                "артикул": "HTEM-50-500",
                "тип товара": "Труба",
                "диаметр (мм)": "50",
                "длина": "500 мм",
            },
        ),
        Product(
            sku="HTEM-50-1500",
            name="Труба канализационная внутренняя 50x1500",
            category_path="Канализация внутренняя",
            brand="OSTENDORF",
            url="https://example.test/sewer501500",
            price=286,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=73,
            attributes_normalized={
                "артикул": "HTEM-50-1500",
                "тип товара": "Труба",
                "диаметр (мм)": "50",
                "длина": "1500 мм",
            },
        ),
        Product(
            sku="OUT-110-1000",
            name="Труба наружная ПВХ 110x1000",
            category_path="Канализация наружная",
            brand="ХЕМКОР",
            url="https://example.test/sewer1101000",
            price=436,
            currency="RUB",
            stock_status="нет в наличии",
            stock_qty=0,
            attributes_normalized={
                "артикул": "OUT-110-1000",
                "тип товара": "Труба",
                "диаметр (мм)": "110",
                "длина": "1000 мм",
            },
        ),
        Product(
            sku="OUT-50-1000",
            name="Труба наружная ПВХ 50x1000",
            category_path="Канализация наружная",
            brand="ХЕМКОР",
            url="https://example.test/sewer501000out",
            price=210,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=6,
            attributes_normalized={
                "артикул": "OUT-50-1000",
                "тип товара": "Труба",
                "диаметр (мм)": "50",
                "длина": "1000 мм",
            },
        ),
        Product(
            sku="VALVE-20-ANGLE",
            name="Кран шаровый угловой для воды 20 мм",
            category_path="Краны шаровые",
            brand="VALTEC",
            url="https://example.test/valve20",
            price=500,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=7,
            attributes_normalized={
                "артикул": "VALVE-20-ANGLE",
                "назначение": "Вода",
                "диаметр (мм)": "20",
                "тип конструкции": "Угловой",
            },
        ),
        Product(
            sku="PUMP-25-40",
            name="Насос циркуляционный 25-40 180 мм",
            category_path="Насосы циркуляционные",
            brand="VESTA",
            url="https://example.test/pump2540",
            price=4300,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=3,
            attributes_normalized={
                "артикул": "PUMP-25-40",
                "тип товара": "Циркуляционный насос",
                "присоединение": "25",
                "монтажная длина": "180 мм",
                "напор": "4 м",
            },
        ),
        Product(
            sku="PUMP-25-60",
            name="Насос циркуляционный 25-60 180 мм",
            category_path="Насосы циркуляционные",
            brand="VESTA",
            url="https://example.test/pump2560",
            price=6100,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=2,
            attributes_normalized={
                "артикул": "PUMP-25-60",
                "тип товара": "Циркуляционный насос",
                "присоединение": "25",
                "монтажная длина": "180 мм",
                "напор": "6 м",
            },
        ),
        Product(
            sku="ARD-E9",
            name="Электрический котёл Arderia E9, 9 кВт",
            category_path="Котлы электрические",
            brand="ARDERIA",
            url="https://example.test/arde9",
            price=38000,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=2,
            attributes_normalized={
                "артикул": "ARD-E9",
                "мощность": "9 кВт",
                "тип котла": "Электрический",
            },
            description="Электрический котёл мощностью 9 кВт.",
        ),
        Product(
            sku="ECA-6",
            name="Электрический котёл E.C.A. Arceus ST, 6 кВт",
            category_path="Котлы электрические",
            brand="E.C.A",
            url="https://example.test/eca6",
            price=30000,
            currency="RUB",
            stock_status="в наличии",
            stock_qty=5,
            attributes_normalized={
                "артикул": "ECA-6",
                "мощность": "6 кВт",
                "тип котла": "Электрический",
            },
            description="Электрический котёл мощностью 6 кВт.",
        ),
    ]


@pytest.fixture
def orchestrator(sample_products: list[Product]) -> ChatOrchestrator:
    return ChatOrchestrator(products=sample_products)
