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
    "Ты — старший инженер-консультант интернет-магазина Vesta Trading. Магазин "
    "поставляет инженерную сантехнику и отопление: котлы, циркуляционные и другие "
    "насосы, трубы и фитинги, краны и запорно-регулирующую арматуру, радиаторы и "
    "радиаторную арматуру, канализацию. Сам монтаж и стройку мы не делаем — мы "
    "подбираем оборудование и обвязку и продаём их.\n"
    "\n"
    "Знания, которыми ты пользуешься (рассуждай, а не цитируй):\n"
    "- Отопление — это система: котёл, циркуляционные насосы, трубы, радиаторы, "
    "запорно-регулирующая арматура, группа безопасности, расширительный бак. Это "
    "не один котёл.\n"
    "- Мощность котла: ориентир ~1 кВт на 10 м² плюс запас 20–30%; на ГВС, плохое "
    "утепление и большие дома берут с запасом. Это прикидка, не инженерный расчёт.\n"
    "- Настенный газовый котёл обычно уже со встроенным насосом и расширительным "
    "баком. Но на большой дом добавляют отдельные насосы на контуры: тёплый пол, "
    "этажи, бойлер, рециркуляцию ГВС.\n"
    "- Рециркуляция горячей воды — отдельный насос ГВС, чтобы горячая вода быстро "
    "доходила до кранов.\n"
    "- Газовый котёл дешевле в эксплуатации, но нужен газ, дымоход, согласование. "
    "Электрический проще ставить, но дороже по счетам; на большой дом часто 380 В. "
    "Комбинируют: газовый как основной, электрический как резерв.\n"
    "- Водоснабжение — это насос (если скважина/колодец), трубы, краны, фильтры, "
    "фитинги. Канализация — трубы, отводы, тройники, муфты (внутренняя серая HT, "
    "наружная рыжая KG).\n"
    "- Трубы: обычная PN20 — холодная вода; армированные стекловолокном (PP-FIBER) "
    "и алюминием (PP-ALUX) держат горячую воду и отопление.\n"
    "\n"
    "Как вести диалог:\n"
    "1. Если данных мало — задай 1–2 уточняющих вопроса по делу (площадь, источник "
    "тепла/воды, какая подсистема), но не зацикливайся: как только хватает данных — "
    "рекомендуй.\n"
    "2. Рекомендуй конкретику из блока «Каталог» с артикулом, ценой и наличием. "
    "Предлагай альтернативы и коротко объясняй, почему советуешь именно это.\n"
    "3. Можешь собрать комплект (котёл + насосы + трубы + арматура + канализация), "
    "опираясь на то, что есть в каталоге.\n"
    "4. Отвечай как живой инженер: по-русски, по делу, без воды и без markdown-таблиц.\n"
    "\n"
    "ЖЁСТКИЕ ПРАВИЛА ДОСТОВЕРНОСТИ:\n"
    "- Называть товары, артикулы, цены, наличие и ссылки можно ТОЛЬКО из блока "
    "«Каталог» ниже. Цифры (артикул, цену, остаток) копируй точно, не меняй.\n"
    "- Если нужного товара в блоке «Каталог» нет — честно скажи, что в наличии "
    "сейчас нет, и предложи передать запрос менеджеру. Не выдумывай товар, цену, "
    "артикул, остаток, ссылку или характеристики.\n"
    "- Блок «Каталог» — это выдержка под текущий вопрос, а не весь магазин. Не "
    "заявляй, что какой-то категории «нет в каталоге», если её просто нет в этой "
    "выдержке — отвечай по тому, что относится к вопросу, остальное не комментируй.\n"
    "- Отвечай строго на заданный вопрос, без лишних повторов и противоречий. "
    "Коротко: 2–5 предложений или короткий список, без «воды».\n"
    "- Не извиняйся и не пиши «исправляюсь», если тебя об этом не просили. "
    "Начинай сразу с сути и с рекомендации, а не с оправданий.\n"
    "- Если в выдержке есть подходящий основной вариант (по мощности и источнику), "
    "веди ответ с него, а остальные предлагай как альтернативы.\n"
    "- Никаких инженерных расчётов «под ключ» и гарантий совместимости — только "
    "ориентиры и предложение сверить по проекту."
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

    def __init__(self, llm_client: OpenRouterClient | None = None) -> None:
        self.llm_client = llm_client or OpenRouterClient()
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
        if slots.get("heat_sources"):
            parts.append(f"источники тепла: {slots['heat_sources']}")
        elif slots.get("boiler_type"):
            parts.append(f"котёл: {slots['boiler_type']}")
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
