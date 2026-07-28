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
    "Ты — AI-консультант компании Vesta Trading (Веста Трейдинг), специалист по отоплению, "
    "водоснабжению и канализации. Общайся естественно, уверенно, по делу и дружелюбно, "
    "но никогда не выдавай себя за человека. Если клиент спрашивает, человек ты или бот, "
    "прямо ответь, что ты AI-консультант. Представляешься так: «Веста Трейдинг, "
    "AI-консультант на связи».\n"
    "\n"
    "ЧТО МЫ ДЕЛАЕМ: поставляем оборудование и обвязку для инженерных систем — котлы, "
    "водонагреватели, насосы, трубы и фитинги, краны и арматуру, радиаторы, канализацию. Сам дом и "
    "монтаж мы не строим, но можем закрыть инженерные системы целиком: котельную, "
    "отопление, водоснабжение, канализацию.\n"
    "\n"
    "ИНЖЕНЕРНЫЕ ЗНАНИЯ (рассуждай ими, а не цитируй дословно):\n"
    "- Отопление — это система, а не один котёл: котёл, циркуляционные насосы, трубы, "
    "радиаторы, арматура, группа безопасности, расширительный бак, иногда бойлер "
    "косвенного нагрева и коллекторы.\n"
    "- Мощность котла: ориентир ~1 кВт на 10 м² плюс запас 20–30%. Диапазон каждый раз "
    "считай только из площади, которую назвал клиент; не подставляй площадь из примеров "
    "или прошлых диалогов. Это прикидка, не точный расчёт. Если минимальная модель из "
    "выдержки заметно мощнее этого диапазона, называй её только ближайшей позицией из "
    "выдержки, а не подходящей или оптимальной: избыточную мощность нужно проверить по "
    "теплопотерям и диапазону модуляции.\n"
    "- Настенный котёл обычно уже со встроенным насосом и расширительным баком. На "
    "большой дом добавляют отдельные насосы на контуры: тёплый пол, этажи, бойлер, "
    "рециркуляцию ГВС.\n"
    "- Рециркуляция горячей воды — отдельный насос ГВС, чтобы горячая вода быстрее "
    "доходила до кранов.\n"
    "- Газ vs электричество: стоимость эксплуатации зависит от региональных тарифов, "
    "цены подключения и режима работы. Газ часто дешевле по текущим расходам, но нужен "
    "газ, дымоход и соблюдение местных требований; электрический обычно проще ставить, "
    "но на большой дом требует достаточной выделенной мощности и часто 380 В. Не обещай, "
    "что согласования точно не нужны. Если есть и газ, и электричество — предлагай "
    "КОМБИНИРОВАННУЮ котельную: газовый котёл как основной, электрический как резерв.\n"
    "- Водоснабжение: насос (если скважина/колодец), трубы, краны, фильтры, фитинги. "
    "Канализация: трубы, отводы, тройники, муфты (внутренняя серая HT, наружная рыжая KG).\n"
    "- Водонагреватель и отопительный котёл — разные товарные категории. Для "
    "водонагревателя учитывай объём в литрах, накопительный/проточный тип, источник "
    "нагрева (электрический, газовый, косвенный или комбинированный), монтаж и "
    "ориентацию. Не подменяй его котлом, ТЭНом, анодом или другим аксессуаром.\n"
    "- Трубы: допустимость PPR для холодной/горячей воды и отопления проверяют по паспорту "
    "конкретной трубы; армирование снижает температурное удлинение. PPR можно применять "
    "на подводящих магистралях, но НЕ выдавай жёсткую PPR за трубу петли тёплого пола. "
    "Для контура водяного пола обычно нужна предназначенная для него гибкая труба PEX, "
    "PE-RT или металлопластик.\n"
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
    "газовый/электрический, объём и тип водонагревателя, источник нагрева, монтаж, "
    "диаметр, длина, материал, бюджет и наличие. Если точного "
    "совпадения нет, не выдавай альтернативу как подходящую: сначала назови, какой "
    "параметр не совпал.\n"
    "- Если подходящего по мощности/типу товара в выдержке нет, честно скажи об этом, предложи "
    "ближайшее из наличия и передачу менеджеру. Не выдавай маломощный за основной и не "
    "выдумывай товар.\n"
    "- Не называй существенно более мощный котёл подходящим только потому, что слабее в "
    "выдержке нет. Сначала назови ориентировочный диапазон для площади и явно предупреди "
    "об отличии мощности.\n"
    "- Если клиент спрашивает, хватит ли конкретной мощности на конкретную площадь, "
    "начни с прямого ответа «скорее не хватит» или «предварительно хватит» и покажи "
    "ориентир. Не называй мощность ниже нижней границы достаточной и не говори, что у неё "
    "есть запас.\n"
    "- У электрического котла нет камеры сгорания. Никогда не приписывай электрическому "
    "котлу открытую/закрытую камеру, дымоход или другие признаки сжигания топлива.\n"
    "- Блок «Каталог» — это выдержка под текущий вопрос, а не весь магазин. НЕ заявляй, "
    "что «в каталоге только газовые» или «категории нет», если её просто нет в этой "
    "выдержке. Отвечай по тому, что относится к вопросу.\n"
    "- Клиенту не нужно знать про фид, выдержку или внутренний каталог. В ответе говори "
    "«в ассортименте», «из найденных моделей», «по карточке товара» или «по техническому "
    "паспорту».\n"
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
POWER_RE = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*квт\b", re.IGNORECASE)
ARTICLE_RE = re.compile(
    r"\b(?:артикул|арт)\b\.?[\s:№]*"
    r"([A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._/\-]{2,})",
    re.IGNORECASE,
)
PRODUCT_BRAND_RE = re.compile(
    r"\b(?:[Кк]от[её]л(?:ы|а|ов)?|[Вв]одонагревател(?:ь|и|я|ей)?|"
    r"[Бб]ойлер(?:ы|а|ов)?|[Нн]асос(?:ы|а|ов)?|[Тт]руб(?:а|ы|у)|"
    r"[Кк]ран(?:ы|а|ов)?|[Рр]адиатор(?:ы|а|ов)?)\s+"
    r"(?:[а-яё-]+\s+){0,3}([A-ZА-Я][A-Za-zА-Яа-я0-9.&-]{2,})"
)
RECOMMENDED_PRODUCT_RE = re.compile(
    r"\b(?:[Рр]екомендую|[Сс]оветую|[Пп]редлагаю|[Вв]ыберите|[Вв]озьмите)\s+"
    r"(?:(?:взять|модель|товар|кот[её]л|водонагреватель|бойлер|"
    r"насос|трубу|кран|радиатор)\s+)?"
    r"([A-ZА-ЯЁ0-9][A-Za-zА-Яа-яЁё0-9.&/\-]*"
    r"(?:\s+[A-ZА-ЯЁ0-9][A-Za-zА-Яа-яЁё0-9.&/\-]*){0,3})"
)
GROUNDING_LATIN_EXCLUSIONS = {
    "ai",
    "dn",
    "gvs",
    "ht",
    "http",
    "https",
    "kg",
    "pe",
    "pert",
    "pex",
    "ppr",
    "rub",
    "trading",
    "url",
    "vesta",
}
GROUNDING_RECOMMENDATION_EXCLUSIONS = {
    "вам",
    "для",
    "обратиться",
    "сначала",
    "уточнить",
}


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
        self.last_llm_requested = False
        self.last_llm_output_accepted = False
        self.last_llm_rejection_reason: str | None = None
        self.last_fallback_reason: str | None = None

    def respond(
        self,
        message: str,
        session: SessionState,
        retrieved: list[Product],
        history: list[dict[str, str]] | None = None,
    ) -> ConsultResult:
        self.last_llm_requested = True
        self.last_llm_output_accepted = False
        self.last_llm_rejection_reason = None
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
            self.last_llm_rejection_reason = result.fallback_reason or "empty_llm_output"
            return ConsultResult(
                answer="",
                cards=[],
                llm_used=False,
                grounded=False,
                fallback_reason=result.fallback_reason or "LLM unavailable",
            )

        answer = result.content.strip()
        violations = self._grounding_violations(answer, by_sku, session=session)
        if violations:
            logger.info("Consultant grounding retry, issues: %s", "; ".join(violations))
            answer = self._retry(messages, violations) or answer
            violations = self._grounding_violations(answer, by_sku, session=session)

        cards = self._cited_cards(answer, by_sku)
        grounded = not violations
        self.last_llm_output_accepted = grounded
        self.last_llm_rejection_reason = "; ".join(violations) if violations else None
        return ConsultResult(
            answer=answer,
            cards=cards,
            llm_used=True,
            grounded=grounded,
            fallback_reason="; ".join(violations) if violations else None,
        )

    def _retry(self, messages: list[dict[str, str]], violations: list[str]) -> str | None:
        correction = (
            "Перепиши ответ и исправь перечисленные ошибки: "
            + "; ".join(violations)
            + ". Аккуратно сверь артикулы, цены и остатки с блоком "
            "«Каталог» — бери их ровно оттуда. Каталог корректен; не пиши, что данные "
            "неверные. Не приписывай электрическому котлу камеру сгорания и не называй "
            "недостаточную мощность достаточной или имеющей запас. Перечисли подходящие "
            "товары с точными цифрами из каталога; "
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
        self.last_llm_used = self.last_llm_used or result.llm_used
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
            passport_power_range = ""
            for key, value in product.attributes_normalized.items():
                key_norm = normalize_text(key)
                if "мощность" in key_norm and not power:
                    power = f" | {value} кВт" if "квт" not in normalize_text(value) else f" | {value}"
                if "диапазон мощности отопления по паспорту" in key_norm:
                    passport_power_range = f" | по техпаспорту отопление: {value}"
            name = html.unescape(product.name)
            lines.append(
                f"[{index}] {name}{power}{passport_power_range} | арт. {product.sku} | "
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

    def _grounding_violations(
        self,
        answer: str,
        by_sku: dict[str, Product],
        session: SessionState | None = None,
    ) -> list[str]:
        issues: list[str] = []
        allowed_urls = {p.url.rstrip("/.,)") for p in by_sku.values() if p.url}
        product_mentions = self._product_mentions(answer, by_sku)

        # Выдуманные артикулы: всё, что названо «артикулом», должно быть в каталоге.
        for match in ARTICLE_RE.finditer(answer):
            raw_token = match.group(1).rstrip(".,;:!?")
            token = normalize_sku(raw_token)
            if token and token not in by_sku:
                issues.append(f"артикул {raw_token} не из каталога")

        # A price/stock value being present somewhere in retrieval is not enough:
        # it must belong to the concrete product named next to that fact.  Otherwise
        # swapping two catalog rows would incorrectly pass grounding.
        for match in PRICE_RE.finditer(answer):
            raw = match.group(1)
            digits = re.sub(r"[  .,]", "", raw)
            if not digits.isdigit():
                continue
            value = int(digits)
            # Мелкие числа (размеры, кВт, количество) и приблизительные оценки не цены.
            if value < 100:
                continue
            owner = self._fact_owner(answer, match.span(), product_mentions, by_sku)
            if owner is None:
                suffix = "при пустом каталоге" if not by_sku else "не привязана к товару"
                issues.append(f"цена {raw} {suffix}")
                continue
            product = by_sku[owner]
            if product.price is None or abs(value - round(product.price)) > 10:
                issues.append(f"цена {raw} не соответствует товару {product.sku}")

        for url in re.findall(r"https?://[^\s)>]+", answer):
            if url.rstrip("/.,)") not in allowed_urls:
                issues.append("ссылка не из каталога")

        for match in re.finditer(r"(\d+)\s*шт\.?", answer, re.IGNORECASE):
            owner = self._fact_owner(answer, match.span(), product_mentions, by_sku)
            if owner is None:
                issues.append(f"остаток {match.group(1)} шт. не привязан к товару")
                continue
            product = by_sku[owner]
            if product.stock_qty is None or int(match.group(1)) != product.stock_qty:
                issues.append(
                    f"остаток {match.group(1)} шт. не соответствует товару {product.sku}"
                )

        # Prices and stock were already tied to the concrete product, but power
        # was not.  A live model therefore managed to call the real E9/E12 cards
        # "24 kW" models.  Treat every product-owned kW value as a grounded fact
        # and compare it with all power/range values actually present in that
        # product's name and structured feed fields.
        for match in POWER_RE.finditer(answer):
            owner = self._fact_owner(answer, match.span(), product_mentions, by_sku)
            if owner is None:
                continue
            claimed = float(match.group(1).replace(",", "."))
            product = by_sku[owner]
            allowed = self._product_power_values(product)
            if allowed and not any(abs(claimed - value) <= 0.05 for value in allowed):
                issues.append(
                    f"мощность {claimed:g} кВт не соответствует товару {product.sku}"
                )

        allowed_product_text = normalize_text(
            " ".join(f"{product.brand} {product.name}" for product in by_sku.values())
        )
        for match in PRODUCT_BRAND_RE.finditer(answer):
            candidate = normalize_text(match.group(1))
            if candidate and candidate not in allowed_product_text:
                issues.append(f"бренд/модель {match.group(1)} не из каталога")

        issues.extend(self._unknown_product_issues(answer, by_sku))

        # A real product can still be described with the wrong energy source.  Tie
        # explicit "gas/electric boiler" claims to a unique brand/model mentioned
        # in the same short answer segment and compare the claim with feed fields.
        answer_segments = [
            normalize_text(segment)
            for segment in re.split(r"[\n.!?]+", answer)
            if segment.strip()
        ]
        brand_owners: dict[str, set[str]] = {}
        for sku, product in by_sku.items():
            brand = normalize_text(product.brand or "")
            if brand:
                brand_owners.setdefault(brand, set()).add(sku)
        for sku, product in by_sku.items():
            product_text = normalize_text(
                " ".join(
                    [
                        product.category_path or "",
                        product.name or "",
                        *[
                            f"{key} {value}"
                            for key, value in (product.attributes_normalized or {}).items()
                        ],
                    ]
                )
            )
            expected = None
            is_water_heater = bool(
                "водонагрев" in product_text
                or re.search(r"\bбойлер\w*\s+косвенн", product_text)
            )
            if "газов" in product_text:
                expected = "газовый"
                wrong_pattern = (
                    r"(?:электрическ\w*(?:\s+\w+){0,2}\s+"
                    r"(?:водонагрев\w*|колонк\w*|бойлер\w*)|"
                    r"(?:водонагрев\w*|колонк\w*|бойлер\w*)"
                    r"(?:\s+\w+){0,2}\s+электрическ\w*)"
                    if is_water_heater
                    else r"(?:электрическ\w*\s+кот\w*|кот\w*\s+электрическ\w*)"
                )
            elif "электр" in product_text:
                expected = "электрический"
                wrong_pattern = (
                    r"(?:газов\w*(?:\s+\w+){0,2}\s+"
                    r"(?:водонагрев\w*|колонк\w*|бойлер\w*)|"
                    r"(?:водонагрев\w*|колонк\w*|бойлер\w*)"
                    r"(?:\s+\w+){0,2}\s+газов\w*)"
                    if is_water_heater
                    else r"(?:газов\w*\s+кот\w*|кот\w*\s+газов\w*)"
                )
            if not expected:
                continue
            anchors = set(self._model_tokens(product.name))
            brand = normalize_text(product.brand or "")
            if brand and brand_owners.get(brand) == {sku}:
                anchors.add(brand)
            sku_text = normalize_text(product.sku or "")
            if sku_text:
                anchors.add(sku_text)
            if any(
                any(anchor in segment for anchor in anchors)
                and re.search(wrong_pattern, segment)
                for segment in answer_segments
            ):
                equipment = "водонагревателя" if is_water_heater else "котла"
                issues.append(
                    f"тип {equipment} {product.sku} противоречит фиду: "
                    f"ожидается {expected}"
                )

            # Связываем утверждения в следующем предложении с названной моделью.
            # В live-QA модель писала «электрический Arderia E12. Он также имеет
            # закрытую камеру», что не ловилось проверкой одного предложения.
            if expected == "электрический":
                for index, segment in enumerate(answer_segments):
                    if not any(anchor in segment for anchor in anchors):
                        continue
                    window = " ".join(answer_segments[index : index + 2])
                    mentions_chamber = re.search(
                        r"(?:камер\w* сгоран|закрыт\w* камер|открыт\w* камер)",
                        window,
                    )
                    denies_chamber = re.search(
                        r"(?:нет|не\s+имеет|не\s+оснащ\w*|без)[^.]{0,35}камер"
                        r"|камер[^.]{0,35}(?:нет|отсутств\w*|не\s+предусмотр\w*)",
                        window,
                    )
                    if mentions_chamber and not denies_chamber:
                        issues.append(
                            f"электрическому котлу {product.sku} приписана камера сгорания"
                        )
                        break

        area_m2 = None
        if session and session.slots.get("area_m2"):
            try:
                area_m2 = float(session.slots["area_m2"])
            except (TypeError, ValueError):
                area_m2 = None
        if area_m2:
            required_kw = area_m2 / 10.0
            underpowered: list[Product] = []
            for product in by_sku.values():
                power_kw = self._product_power_kw(product)
                if not power_kw or power_kw + 0.4 >= required_kw:
                    continue
                underpowered.append(product)
                anchors = set(self._model_tokens(product.name))
                sku_text = normalize_text(product.sku or "")
                if sku_text:
                    anchors.add(sku_text)
                for index, segment in enumerate(answer_segments):
                    if not any(anchor in segment for anchor in anchors):
                        continue
                    window = " ".join(answer_segments[index : index + 2])
                    positive = any(
                        marker in window
                        for marker in [
                            "будет достаточно",
                            "достаточно для",
                            "хватит для",
                            "есть запас",
                            "имеет запас",
                            "с запасом",
                            "приемлем",
                            "подходящ",
                            "подойдет",
                            "подойдёт",
                            "оптимальн",
                            "рекоменд",
                            "совет",
                            "лучший вариант",
                            "лучше взять",
                            "выберите",
                            "выбирайте",
                        ]
                    )
                    negative = any(
                        marker in window
                        for marker in [
                            "не хват",
                            "недостаточ",
                            "без запас",
                            "впритык",
                            "не подойдет",
                            "не подойдёт",
                            "не рекоменд",
                            "не могу рекоменд",
                            "не как основн",
                            "только как резерв",
                            "не закрывает",
                            "маломощ",
                        ]
                    )
                    if positive and not negative:
                        issues.append(
                            f"мощность {power_kw:g} кВт недостаточна для {area_m2:g} м²"
                        )
                        break

            # Models often refer back to a list collectively ("эти модели"),
            # without repeating the SKU/model anchor.  Validate that claim too;
            # otherwise the per-product check above cannot assign an owner.
            collective_positive = re.search(
                r"\b(?:эти|обе|оба|все\s+(?:эти\s+)?)\s+"
                r"(?:модел\w*|вариант\w*|котл\w*)[^.!?]{0,100}"
                r"(?:достаточ\w*|подход\w*|хват\w*|с\s+запас\w*)",
                normalize_text(answer),
            )
            if collective_positive:
                for product in underpowered:
                    issues.append(
                        f"коллективная рекомендация включает недостаточный котёл "
                        f"{product.sku} для {area_m2:g} м²"
                    )

        return issues[:4]

    @staticmethod
    def _product_power_values(product: Product) -> set[float]:
        """Return every authoritative kW value/range endpoint for a product."""
        values: set[float] = set()
        sources = [product.name]
        sources.extend(
            str(value)
            for key, value in (product.attributes_normalized or {}).items()
            if "мощност" in normalize_text(str(key))
        )
        for source in sources:
            for raw in re.findall(r"\d+(?:[.,]\d+)?", source):
                try:
                    values.add(float(raw.replace(",", ".")))
                except ValueError:
                    continue
        return values

    def _product_mentions(
        self,
        answer: str,
        by_sku: dict[str, Product],
    ) -> list[tuple[int, int, str]]:
        """Locate unambiguous catalog-product anchors in normalized answer text."""
        normalized = normalize_text(answer)
        anchors_by_sku: dict[str, set[str]] = {}
        owners: dict[str, set[str]] = {}
        for sku, product in by_sku.items():
            anchors = {
                normalize_text(product.sku),
                normalize_text(product.name),
                *self._model_tokens(product.name),
                *self._distinctive_name_tokens(product),
            }
            brand = normalize_text(product.brand or "")
            if brand:
                anchors.add(brand)
            anchors = {anchor for anchor in anchors if len(anchor) >= 2}
            anchors_by_sku[sku] = anchors
            for anchor in anchors:
                owners.setdefault(anchor, set()).add(sku)

        mentions: list[tuple[int, int, str]] = []
        for sku, anchors in anchors_by_sku.items():
            for anchor in anchors:
                if owners.get(anchor) != {sku}:
                    continue
                pattern = rf"(?<![a-zа-я0-9]){re.escape(anchor)}(?![a-zа-я0-9])"
                for match in re.finditer(pattern, normalized):
                    mentions.append((match.start(), match.end(), sku))
        return mentions

    def _fact_owner(
        self,
        answer: str,
        raw_span: tuple[int, int],
        mentions: list[tuple[int, int, str]],
        by_sku: dict[str, Product],
    ) -> str | None:
        if not by_sku:
            return None
        if len(by_sku) == 1:
            return next(iter(by_sku))

        # Mention offsets are in normalized text; map the raw fact offset into
        # the same coordinate space without relying on punctuation widths.
        fact_start = len(normalize_text(answer[: raw_span[0]]))
        fact_end = len(normalize_text(answer[: raw_span[1]]))
        distances: dict[str, int] = {}
        for start, end, sku in mentions:
            if end <= fact_start:
                distance = fact_start - end
            elif start >= fact_end:
                # Prefer a label before its fact when both are equally close.
                distance = start - fact_end + 8
            else:
                distance = 0
            if distance <= 180:
                distances[sku] = min(distance, distances.get(sku, distance))
        if not distances:
            return None
        nearest = min(distances.values())
        owners = [sku for sku, distance in distances.items() if distance == nearest]
        return owners[0] if len(owners) == 1 else None

    def _unknown_product_issues(
        self,
        answer: str,
        by_sku: dict[str, Product],
    ) -> list[str]:
        """Reject named/recommended products that retrieval did not supply.

        This intentionally does not depend on a phrase such as ``котёл BRAND``:
        models often write ``Рекомендую Ariston CLAS`` or just a model token.
        """
        allowed_text = normalize_text(
            " ".join(
                f"{product.sku} {product.brand or ''} {product.name}"
                for product in by_sku.values()
            )
        )
        allowed_skus = set(by_sku)
        scrubbed = re.sub(r"https?://[^\s)>]+", " ", answer)
        candidates: list[str] = []

        for match in RECOMMENDED_PRODUCT_RE.finditer(scrubbed):
            candidate = match.group(1)
            first_word = normalize_text(candidate).split()[0]
            if first_word not in (
                GROUNDING_RECOMMENDATION_EXCLUSIONS | GROUNDING_LATIN_EXCLUSIONS
            ):
                candidates.append(candidate)

        # Model-like tokens (SB24, CMSR02CA28) are strong product evidence even
        # when the answer omits both the product type and the word "артикул".
        candidates.extend(
            re.findall(
                r"\b[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9./\-]*\d"
                r"[A-Za-zА-Яа-яЁё0-9./\-]*\b",
                scrubbed,
            )
        )

        # Latin title-case/all-caps words in an otherwise Russian answer are
        # normally brands or model families.  Keep a small domain/identity
        # exclusion set so PEX/PPR and Vesta Trading are not treated as goods.
        for token in re.findall(r"\b[A-Z][A-Za-z]{2,}\b", scrubbed):
            if token.lower() not in GROUNDING_LATIN_EXCLUSIONS:
                candidates.append(token)

        issues: list[str] = []
        seen: set[str] = set()
        for raw_candidate in candidates:
            candidate = normalize_text(raw_candidate)
            candidate_sku = normalize_sku(raw_candidate)
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            if candidate in allowed_text or candidate_sku in allowed_skus:
                continue
            # A multi-token recommendation is grounded when its distinctive
            # brand/model portion appears in a retrieved product name.
            parts = [part for part in candidate.split() if len(part) >= 2]
            if parts and all(part in allowed_text for part in parts):
                continue
            issues.append(f"товар/модель {raw_candidate} не из каталога")
        return issues

    def _distinctive_name_tokens(self, product: Product) -> set[str]:
        brand = normalize_text(product.brand or "")
        result: set[str] = set()
        for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9./\-]+", product.name):
            normalized = normalize_text(token)
            if normalized == brand or (
                len(token) >= 3
                and any(char.isalpha() for char in token)
                and (
                    any(char.isdigit() for char in token)
                    or token.isupper()
                    or (token.isascii() and token[0].isupper())
                )
            ):
                result.add(normalized)
        return result

    def _product_power_kw(self, product: Product) -> float | None:
        # The commercial name usually carries the nominal model power and is
        # safer than taking the first feed attribute, which may be a minimum
        # modulation or electrical-consumption value.
        name_match = re.search(r"(\d+(?:[,.]\d+)?)\s*квт", normalize_text(product.name))
        if name_match:
            return float(name_match.group(1).replace(",", "."))

        preferred: list[str] = []
        fallback: list[str] = []
        for key, value in (product.attributes_normalized or {}).items():
            key_norm = normalize_text(key)
            if "мощност" not in key_norm:
                continue
            if any(marker in key_norm for marker in ["миним", "потреб", "электрическ"]):
                continue
            target = preferred if key_norm in {"мощность", "мощность квт", "номинальная мощность"} else fallback
            target.append(str(value))
        for value in [*preferred, *fallback]:
            match = re.search(r"\d+(?:[,.]\d+)?", str(value))
            if match:
                return float(match.group(0).replace(",", "."))
        return None

    def _cited_cards(self, answer: str, by_sku: dict[str, Product]) -> list[ProductCard]:
        norm_answer = normalize_sku(answer)
        answer_text = normalize_text(answer)
        cards: list[ProductCard] = []
        seen: set[str] = set()
        token_owners: dict[str, set[str]] = {}
        for product in by_sku.values():
            for token in self._model_tokens(product.name):
                token_owners.setdefault(token, set()).add(product.sku)
        for sku_norm, product in by_sku.items():
            mentions_sku = bool(sku_norm and sku_norm in norm_answer)
            mentions_unique_model = any(
                token in answer_text and token_owners.get(token) == {product.sku}
                for token in self._model_tokens(product.name)
            )
            if not (mentions_sku or mentions_unique_model) or product.sku in seen:
                continue
            seen.add(product.sku)
            cards.append(
                ProductCard(
                    sku=product.sku,
                    name=html.unescape(product.name),
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

    def _model_tokens(self, name: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-zа-я0-9./\-]+", normalize_text(name))
            if len(token) >= 2 and re.search(r"[a-zа-я]", token) and re.search(r"\d", token)
        }
