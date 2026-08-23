"""Состав инженерного проекта: какие узлы нужны помимо уже выбранных.

Бот умел показать выбранное («покажи подборку»), но на вопрос «что ещё нужно?»
отвечать не мог. Здесь описан состав типового проекта — не как свободная
генерация, а как проверяемая таблица: узел, зачем он, норма расхода и условие,
при котором узел вообще нужен.

Почему таблицей, а не знаниями модели: перечень узлов LLM формулирует неплохо,
но количества и привязка к товару — это то, по чему покупатель поедет в
магазин. Свободно сгенерированное число звучит убедительно и не проверяется,
а таблицу можно прочитать глазами и она даёт одинаковый ответ от прогона к
прогону. Поэтому модель здесь не участвует: состав фиксирован, товары
подбираются обычным поиском по фиду, количества выдаются формулой («по два на
радиатор»), а не готовой цифрой, пока покупатель не назвал количество точек.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class SpecNode:
    """Один узел проекта."""

    key: str
    title: str
    # Зачем узел нужен — короткая инженерная причина, а не рекламная фраза.
    purpose: str
    # Категория бота для подбора; None — узла нет в ассортименте, и об этом
    # нужно сказать прямо, а не молчать.
    category: str | None
    # Слоты для поиска: сужают выдачу до подходящего исполнения.
    slots: dict[str, Any] = field(default_factory=dict)
    # Текст запроса для поиска. Без него подбор берёт самое дешёвое в
    # категории, и вместо фитинга в пример попадает заглушка, а вместо
    # радиаторной арматуры — защитный колпачок.
    query_text: str = ""
    # Норма расхода словами. Число не подставляется, пока покупатель не назвал
    # количество точек — иначе получится убедительная выдумка.
    rate: str = ""
    # Условие включения узла в состав проекта.
    applies: Callable[[dict[str, Any]], bool] | None = None


def _has_gas_boiler(facts: dict[str, Any]) -> bool:
    return "газов" in str(facts.get("boiler_type") or "").lower()


def _has_radiators(facts: dict[str, Any]) -> bool:
    return bool(facts.get("has_radiators"))


def _has_warm_floor(facts: dict[str, Any]) -> bool:
    return bool(facts.get("has_warm_floor"))


HEATING_PROJECT_NODES: tuple[SpecNode, ...] = (
    SpecNode(
        key="boilers",
        title="Котёл",
        purpose="источник тепла, от него считается вся обвязка",
        category="boilers",
    ),
    SpecNode(
        key="radiators",
        title="Радиаторы",
        purpose="теплоотдача в помещениях",
        category="radiators",
        applies=lambda facts: not _has_warm_floor(facts) or _has_radiators(facts),
    ),
    SpecNode(
        key="pipes",
        title="Трубы разводки",
        purpose="подача и обратка от котла к приборам",
        category="pipes",
        slots={"pipe_purpose": "отопление"},
        query_text="труба для отопления",
        rate="метраж считается по схеме разводки",
    ),
    SpecNode(
        key="fittings",
        title="Фитинги",
        purpose="повороты, ответвления и переходы на резьбу",
        category="fittings",
        slots={"element_type": "угольник", "trade_element": "угольник"},
        query_text="угольник для труб отопления",
        rate="уголки и тройники — по числу поворотов и ответвлений на схеме",
    ),
    SpecNode(
        key="radiator_fittings",
        title="Радиаторная арматура",
        purpose="регулировка и отключение каждого радиатора",
        category="radiator_fittings",
        slots={"product_kind": "thermostatic_valve"},
        query_text="термостатический клапан для радиатора",
        rate="по два клапана на радиатор: термостатический на подачу и запорный на обратку",
        applies=lambda facts: not _has_warm_floor(facts) or _has_radiators(facts),
    ),
    SpecNode(
        key="valves",
        title="Шаровые краны на обвязку котла",
        purpose="отсечь котёл для обслуживания без слива системы",
        category="valves",
        slots={
            "application": "отопление",
            "product_kind": "ball_valve",
            "thread_type": "ff",
        },
        query_text="шаровой кран 3/4 вн-вн для отопления",
        rate="минимум два: на подачу и на обратку",
    ),
    SpecNode(
        key="filters",
        title="Фильтр грубой очистки",
        purpose="защита котла и насоса от окалины и шлама",
        category="filters",
        query_text="фильтр грубой очистки",
        rate="один на обратке перед котлом",
    ),
    SpecNode(
        key="collector",
        title="Коллектор",
        purpose="раздача контуров при лучевой разводке и тёплом поле",
        category="fittings",
        slots={"element_type": "коллектор", "trade_element": "коллектор"},
        query_text="коллектор для тёплого пола",
        rate="один на группу контуров",
        applies=_has_warm_floor,
    ),
    SpecNode(
        key="pumps",
        title="Циркуляционный насос",
        purpose="проток теплоносителя, если насос не встроен в котёл",
        category="pumps",
        query_text="циркуляционный насос для отопления",
        rate="один на контур циркуляции",
        applies=lambda facts: bool(facts.get("needs_pump")),
    ),
    # Узлы, которых в этом каталоге нет. Молчать о них нельзя: без них система
    # не собирается, и покупатель должен знать, что искать их надо отдельно.
    SpecNode(
        key="chimney",
        title="Дымоход",
        purpose="отвод продуктов сгорания газового котла",
        category=None,
        applies=_has_gas_boiler,
    ),
    SpecNode(
        key="safety_group",
        title="Группа безопасности и расширительный бак",
        purpose="сброс давления и компенсация расширения теплоносителя",
        category=None,
        applies=lambda facts: not bool(facts.get("boiler_has_safety_group")),
    ),
)


def heating_project_nodes(facts: dict[str, Any]) -> list[SpecNode]:
    """Состав проекта отопления с учётом уже известных фактов."""

    return [
        node
        for node in HEATING_PROJECT_NODES
        if node.applies is None or node.applies(facts)
    ]


# Подключение сантехприбора — та же таблица, но для санузла. Каталог здесь
# заведомо неполный (это инженерная сантехника, а не магазин расходки), и
# отсутствующие позиции перечисляются прямо: покупатель должен знать, что
# крепёж и герметик придётся искать отдельно.
TOILET_INSTALLATION_NODES: tuple[SpecNode, ...] = (
    SpecNode(
        key="outlet",
        title="Выпуск в канализацию",
        purpose="соединение унитаза со стояком: манжета при прямом стыке, гофра при смещении",
        category="sewer",
        slots={"element_type": "манжета", "trade_element": "манжета"},
        query_text="манжета для унитаза 110",
        rate="одна на прибор",
    ),
    SpecNode(
        key="water_hose",
        title="Гибкая подводка воды",
        purpose="подвод холодной воды к бачку",
        category="sewer",
        slots={"element_type": "подводка", "trade_element": "подводка"},
        query_text="подводка гибкая для воды",
        rate="одна на бачок; длину берут с запасом от крана до бачка",
    ),
    SpecNode(
        key="shutoff",
        title="Запорный кран на подводку",
        purpose="перекрыть воду на бачок, не отключая весь санузел",
        category="valves",
        slots={
            "size_inch": "1/2",
            "product_kind": "ball_valve",
            "thread_type": "fm",
            "application": "вода",
        },
        query_text="шаровой кран 1/2 вн-нар для воды",
        rate="один на прибор",
    ),
    SpecNode(
        key="mounting",
        title="Крепёж, герметик и лента ФУМ",
        purpose="фиксация прибора к полу и уплотнение резьбовых соединений",
        category=None,
    ),
)


def toilet_installation_nodes() -> list[SpecNode]:
    """Состав подключения унитаза."""

    return list(TOILET_INSTALLATION_NODES)
