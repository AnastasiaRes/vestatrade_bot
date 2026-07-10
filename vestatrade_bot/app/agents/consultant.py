from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field

from app.models import Product, ProductCard, SessionState
from app.openrouter_client import OpenRouterClient

from .utils import normalize_sku, normalize_text


logger = logging.getLogger(__name__)


# Инженерные знания предметной области. Это НЕ скрипт ответов — это рамка, внутри
# которой модель сама рассуждает по любому запросу из области инженерной сантехники.
DOMAIN_SYSTEM_PROMPT = (
    "Ты — консультант компании Vesta Trading (Веста Трейдинг), инженер по отоплению, "
    "водоснабжению и канализации. Общаешься как живой продавец-инженер на связи: "
    "уверенно, по делу, дружелюбно. Представляешься так: «Веста Трейдинг, консультант "
    "на связи».\n"
    "\n"
    "ЧТО МЫ ДЕЛАЕМ: поставляем оборудование и обвязку для инженерных систем — котлы, "
    "насосы, трубы и фитинги, краны и арматуру, радиаторы, канализацию. Сам дом и "
    "монтаж мы не строим, но можем закрыть инженерные системы целиком: котельную, "
    "отопление, водоснабжение, канализацию.\n"
    "\n"
    "ИНЖЕНЕРНЫЕ ЗНАНИЯ (рассуждай ими, а не цитируй дословно):\n"
    "- Отопление — это система, а не один котёл: котёл, циркуляционные насосы, трубы, "
    "радиаторы, арматура, группа безопасности, расширительный бак, иногда бойлер "
    "косвенного нагрева и коллекторы.\n"
    "- Мощность котла: ориентир ~1 кВт на 10 м² плюс запас 20–30%. Для 200–240 м² это "
    "примерно 24–32 кВт. Это прикидка, не точный расчёт.\n"
    "- Настенный котёл обычно уже со встроенным насосом и расширительным баком. На "
    "большой дом добавляют отдельные насосы на контуры: тёплый пол, этажи, бойлер, "
    "рециркуляцию ГВС.\n"
    "- Рециркуляция горячей воды — отдельный насос ГВС, чтобы горячая вода быстрее "
    "доходила до кранов.\n"
    "- Газ vs электричество: газовый дешевле в эксплуатации (нужен газ, дымоход, "
    "согласование), электрический проще ставить, но дороже по счетам и на большой дом "
    "требует много мощности/380 В. Если есть и газ, и электричество — предлагай "
    "КОМБИНИРОВАННУЮ котельную: газовый котёл как основной, электрический как резерв.\n"
    "- Водоснабжение: насос (если скважина/колодец), трубы, краны, фильтры, фитинги. "
    "Канализация: трубы, отводы, тройники, муфты (внутренняя серая HT, наружная рыжая KG).\n"
    "- Трубы: PN20 обычная — холодная вода; армированные PP-FIBER (стекловолокно) и "
    "PP-ALUX (алюминий) — горячая вода, отопление, тёплый пол.\n"
    "\n"
    "КАК ВЕСТИ ДИАЛОГ (от общего к частному):\n"
    "1. На общий запрос («нужно отопление», «помоги выбрать всё», «строю дом») коротко "
    "объясни, что это система, и спроси 1–2 ключевых параметра: площадь и источник "
    "тепла. Не зацикливайся — как только есть площадь и источник, переходи к подбору.\n"
    "2. Рекомендуй конкретику из блока «Каталог»: называй товар, артикул, цену и "
    "наличие, и коротко поясняй, почему именно он (мощность/запас/назначение).\n"
    "3. Предлагай альтернативы (другие котлы/насосы) и собирай КОМПЛЕКТ на всю систему "
    "(котёл + насосы + трубы + краны + канализация), а не один товар.\n"
    "4. В конце по запросу («что в итоге») предложи итоговую подборку и, если уместно, "
    "два варианта: «оптимальный» и «с запасом».\n"
    "5. Помни контекст всего диалога: площадь, источник тепла, что уже предложил. Не "
    "переспрашивай то, что уже сказано.\n"
    "6. Всегда отвечай уважительно и на «вы». Даже если клиент раздражён, пишет грубо "
    "или очень коротко, сохраняй спокойный профессиональный тон: без ответной грубости, "
    "фамильярности, давления, сарказма и оценок клиента. Если предыдущий ответ был "
    "неясным, коротко признай это и сразу дай понятный ответ.\n"
    "\n"
    "ЖЁСТКИЕ ПРАВИЛА ДОСТОВЕРНОСТИ:\n"
    "- Называть товары, артикулы, цены, наличие и ссылки можно ТОЛЬКО из блока "
    "«Каталог» ниже. Цифры (артикул, цену, остаток) копируй точно, не меняй и не округляй.\n"
    "- Учитывай отрицание: если клиент сказал, что газа нет — газовые котлы НЕ "
    "предлагай, веди электрическую котельную.\n"
    "- Строго соблюдай заданные фильтры клиента: двухконтурный/одноконтурный, "
    "газовый/электрический, диаметр, длина, материал, бюджет и наличие. Если точного "
    "совпадения нет, не выдавай альтернативу как подходящую: сначала назови, какой "
    "параметр не совпал.\n"
    "- Если подходящего по мощности/типу товара в выдержке нет (например, нужен мощный "
    "электрокотёл на 240 м², а есть только маломощные) — честно скажи об этом, предложи "
    "ближайшее из наличия и передачу менеджеру. Не выдавай маломощный за основной и не "
    "выдумывай товар.\n"
    "- Блок «Каталог» — это выдержка под текущий вопрос, а не весь магазин. НЕ заявляй, "
    "что «в каталоге только газовые» или «категории нет», если её просто нет в этой "
    "выдержке. Отвечай по тому, что относится к вопросу.\n"
    "- Отвечай строго на заданный вопрос, без повторов и противоречий с прошлыми "
    "репликами. Коротко: 2–6 предложений или короткий список. Без markdown-таблиц.\n"
    "- Не извиняйся и не пиши «исправляюсь» без причины. Если клиент раздражён из-за "
    "неясного ответа, можно коротко признать: «Понял, отвечу прямо», и начать с сути.\n"
    "- Никаких расчётов «под ключ» и гарантий совместимости — только ориентиры и "
    "предложение сверить по проекту/с менеджером."
)


@dataclass
class ConsultResult:
    answer: str
    cards: list[ProductCard] = field(default_factory=list)
    llm_used: bool = False
    grounded: bool = True
    fallback_reason: str | None = None


PRICE_RE = re.compile(r"(\d[\d  .,]{2,})\s*(?:₽|руб|р\.|rub)", re.IGNORECASE)
ARTICLE_RE = re.compile(
    r"арт(?:икул)?[\s.:№]*([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/\-]{2,})",
    re.IGNORECASE,
)


class ConsultantAgent:
    """Retrieval-augmented sales engineer. The LLM drives the conversation; the
    feed grounds every product fact. No scripted answers."""

    def __init__(
        self,
        llm_client: OpenRouterClient | None = None,
        model: str | None = None,
    ) -> None:
        self.llm_client = llm_client or OpenRouterClient()
        # Сильная модель для подбора; если не задана — дешёвая по умолчанию клиента.
        self.model = model
        self.last_llm_used = False
        self.last_fallback_reason: str | None = None

    def respond(
        self,
        message: str,
        session: SessionState,
        retrieved: list[Product],
        history: list[dict[str, str]] | None = None,
    ) -> ConsultResult:
        catalog_block, by_sku = self._build_catalog(retrieved)
        project = self._project_summary(session)

        system = DOMAIN_SYSTEM_PROMPT
        user_parts: list[str] = []
        if project:
            user_parts.append(f"Что уже известно о проекте: {project}")
        if catalog_block:
            user_parts.append(
                "Каталог (только эти товары можно называть, цифры копируй точно):\n"
                + catalog_block
            )
        else:
            user_parts.append(
                "Каталог: подходящих позиций под этот запрос сейчас не подобрано — "
                "если нужен товар, попроси уточнить параметры или предложи менеджера."
            )
        user_parts.append(f"Сообщение клиента: {message}")
        user_content = "\n\n".join(user_parts)

        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        for entry in (history or [])[-6:]:
            role = entry.get("role")
            content = (entry.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                if len(content) > 500:
                    content = content[:500] + "…"
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_content})

        result = self.llm_client.complete(
            agent="ConsultantAgent",
            messages=messages,
            temperature=0.35,
            max_tokens=550,
            model=self.model,
        )
        self.last_llm_used = result.llm_used
        self.last_fallback_reason = result.fallback_reason

        if not result.llm_used or not (result.content or "").strip():
            return ConsultResult(
                answer="",
                cards=[],
                llm_used=False,
                grounded=False,
                fallback_reason=result.fallback_reason or "LLM unavailable",
            )

        answer = result.content.strip()
        violations = self._grounding_violations(answer, by_sku)
        if violations:
            logger.info("Consultant grounding retry, issues: %s", "; ".join(violations))
            answer = self._retry(messages, violations) or answer
            violations = self._grounding_violations(answer, by_sku)

        cards = self._cited_cards(answer, by_sku)
        grounded = not violations
        return ConsultResult(
            answer=answer,
            cards=cards,
            llm_used=True,
            grounded=grounded,
            fallback_reason="; ".join(violations) if violations else None,
        )

    def _retry(self, messages: list[dict[str, str]], violations: list[str]) -> str | None:
        correction = (
            "Перепиши свой ответ, аккуратно сверив артикулы, цены и остатки с блоком "
            "«Каталог» — бери их ровно оттуда. Каталог корректен; не пиши, что данные "
            "неверные. Просто перечисли подходящие товары с точными цифрами из каталога; "
            "если для запроса в каталоге ничего нет — коротко предложи менеджера."
        )
        retry_messages = messages + [{"role": "user", "content": correction}]
        result = self.llm_client.complete(
            agent="ConsultantAgent.retry",
            messages=retry_messages,
            temperature=0.2,
            max_tokens=750,
            model=self.model,
        )
        if result.llm_used and (result.content or "").strip():
            return result.content.strip()
        return None

    def _build_catalog(self, products: list[Product]) -> tuple[str, dict[str, Product]]:
        lines: list[str] = []
        by_sku: dict[str, Product] = {}
        for index, product in enumerate(products, start=1):
            if product.price is None or not product.url:
                continue
            by_sku[normalize_sku(product.sku)] = product
            stock = product.stock_status
            if product.stock_qty is not None:
                stock = f"в наличии {product.stock_qty} шт" if product.stock_qty > 0 else "нет в наличии"
            power = ""
            for key, value in product.attributes_normalized.items():
                if "мощность" in normalize_text(key):
                    power = f" | {value} кВт" if "квт" not in normalize_text(value) else f" | {value}"
                    break
            name = html.unescape(product.name)
            lines.append(
                f"[{index}] {name}{power} | арт. {product.sku} | "
                f"{product.price:g} ₽ | {stock} | {product.url}"
            )
        return "\n".join(lines), by_sku

    def _project_summary(self, session: SessionState) -> str:
        slots = session.slots or {}
        parts: list[str] = []
        if slots.get("area_m2"):
            area = float(slots["area_m2"])
            required = area / 10.0
            parts.append(
                f"площадь {area:g} м² (ориентир мощности котла ~{required:g}–{required * 1.3:g} кВт)"
            )
        # Явно проговариваем наличие газа — критично из-за отрицания «газа нет».
        if slots.get("has_gas") is False:
            parts.append("ГАЗА НЕТ — только электрическая котельная, газовые котлы не предлагать")
        elif slots.get("has_gas") is True and slots.get("has_electricity") is True:
            parts.append("есть и газ, и электричество — уместна комбинированная котельная (газ основной + электр резерв)")
        elif slots.get("has_gas") is True:
            parts.append("газ есть")
        elif slots.get("heat_sources"):
            parts.append(f"источники тепла: {slots['heat_sources']}")
        if slots.get("boiler_types"):
            parts.append(f"типы котлов по проекту: {', '.join(slots['boiler_types'])}")
        if slots.get("boiler_type"):
            parts.append(f"тип котла по запросу: {slots['boiler_type']}")
        if slots.get("contours"):
            parts.append(f"контурность по запросу: {slots['contours']}")
        if slots.get("project"):
            parts.append(str(slots["project"]))
        return ", ".join(parts)

    def _grounding_violations(self, answer: str, by_sku: dict[str, Product]) -> list[str]:
        issues: list[str] = []
        allowed_prices = {round(p.price) for p in by_sku.values() if p.price is not None}

        # Выдуманные артикулы: всё, что названо «артикулом», должно быть в каталоге.
        for match in ARTICLE_RE.finditer(answer):
            token = normalize_sku(match.group(1))
            if token and token not in by_sku:
                # допускаем, что модель написала артикул чуть иначе — проверим вхождение
                if not any(token in sku or sku in token for sku in by_sku):
                    issues.append(f"артикул {match.group(1)} не из каталога")

        # Выдуманные цены: каждая названная цена должна совпадать с ценой товара из каталога.
        for match in PRICE_RE.finditer(answer):
            raw = match.group(1)
            digits = re.sub(r"[  .,]", "", raw)
            if not digits.isdigit():
                continue
            value = int(digits)
            # Мелкие числа (размеры, кВт, количество) и приблизительные оценки не цены.
            if value < 100:
                continue
            if not allowed_prices:
                issues.append(f"цена {raw} при пустом каталоге")
                continue
            # допускаем округление до десятков рублей
            if not any(abs(value - price) <= 10 for price in allowed_prices):
                issues.append(f"цена {raw} не из каталога")

        return issues[:4]

    def _cited_cards(self, answer: str, by_sku: dict[str, Product]) -> list[ProductCard]:
        norm_answer = normalize_sku(answer)
        cards: list[ProductCard] = []
        seen: set[str] = set()
        for sku_norm, product in by_sku.items():
            if sku_norm and sku_norm in norm_answer and product.sku not in seen:
                seen.add(product.sku)
                cards.append(
                    ProductCard(
                        sku=product.sku,
                        name=product.name,
                        brand=product.brand,
                        price=product.price or 0.0,
                        currency=product.currency,
                        stock_status=product.stock_status,
                        stock_qty=product.stock_qty,
                        url=product.url or "",
                        image_url=product.image_url,
                    )
                )
        return cards
