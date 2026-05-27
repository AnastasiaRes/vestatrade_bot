from __future__ import annotations

from app.models import ProductCard, SearchQuery
from app.openrouter_client import OpenRouterClient


class ResponseComposerAgent:
    def __init__(self, llm_client: OpenRouterClient | None = None) -> None:
        self.llm_client = llm_client or OpenRouterClient()
        self.last_llm_used = False
        self.last_llm_requested = False
        self.last_llm_fallback_reason: str | None = None
        self.last_draft: str | None = None

    def reset_usage(self) -> None:
        self.last_llm_used = False
        self.last_llm_requested = False
        self.last_llm_fallback_reason = None
        self.last_draft = None

    def compose_small_talk(self, message: str) -> str:
        normalized = message.lower().replace("ё", "е").strip()
        if "зовут" in normalized or "кто ты" in normalized or "ты кто" in normalized or "как обращ" in normalized:
            draft = (
                "Я AI-консультант Vesta Trading. Помогаю подобрать товары из фида: "
                "трубы, насосы, котлы, краны, канализацию и радиаторную арматуру. "
                "Напишите, что нужно подобрать — уточню параметры и пришлю карточки."
            )
            return self._polish(
                "ResponseComposerAgent.small_talk_identity",
                message,
                draft,
                "Ответь на вопрос о личности бота, сохрани перечисление категорий и приглашение написать запрос.",
            )
        if "что ты умеешь" in normalized or "что умеешь" in normalized or "помоги" in normalized or "у меня вопрос" in normalized:
            draft = (
                "Я помогу подобрать товар по запросу, уточню цену, наличие и характеристики "
                "и дам прямую ссылку на карточку. Категории: трубы, насосы, котлы, краны, "
                "канализация и радиаторная арматура. Опишите задачу своими словами."
            )
            return self._polish(
                "ResponseComposerAgent.small_talk_capability",
                message,
                draft,
                "Коротко объясни возможности консультанта интернет-магазина, перечисли категории.",
            )
        if "спасибо" in normalized or "благодарю" in normalized:
            draft = "Пожалуйста! Если нужно, могу показать аналоги, варианты подешевле или передать вопрос менеджеру."
            return self._polish(
                "ResponseComposerAgent.small_talk_thanks",
                message,
                draft,
                "Коротко и дружелюбно ответь на благодарность.",
            )
        if "как дела" in normalized or "как ты" == normalized or normalized.startswith("как ты "):
            draft = "Дела хорошо, спасибо. Готов помочь с подбором товаров Vesta Trading — что нужно?"
            return self._polish(
                "ResponseComposerAgent.small_talk_howareyou",
                message,
                draft,
                "Кратко ответь на вопрос о делах и предложи помощь с подбором.",
            )
        if "красив" in normalized or "молодец" in normalized or "умничк" in normalized or "хорош" in normalized and len(normalized) < 25:
            draft = "Спасибо! Готов помочь с подбором — что нужно по ассортименту?"
            return self._polish(
                "ResponseComposerAgent.small_talk_compliment",
                message,
                draft,
                "Скромно поблагодари за комплимент и предложи помощь.",
            )
        if "пока" == normalized or "до свидан" in normalized or "до встреч" in normalized:
            draft = "До свидания! Возвращайтесь, если понадобится подбор по ассортименту Vesta Trading."
            return self._polish(
                "ResponseComposerAgent.small_talk_bye",
                message,
                draft,
                "Вежливо попрощайся.",
            )
        if any(greet in normalized for greet in ["здравств", "добрый день", "добрый вечер", "доброе утро"]):
            draft = (
                "Здравствуйте! Я AI-консультант Vesta Trading. "
                "Опишите, что нужно подобрать — трубы, насосы, котлы, краны, "
                "канализацию или радиаторную арматуру, и я уточню параметры."
            )
            return self._polish(
                "ResponseComposerAgent.small_talk_greeting",
                message,
                draft,
                "Поздоровайся и предложи помощь, перечисли категории.",
            )
        if "привет" in normalized:
            draft = (
                "Привет! Я AI-консультант Vesta Trading. Опишите, что нужно подобрать — "
                "трубы, насосы, котлы, краны, канализацию или радиаторную арматуру."
            )
            return self._polish(
                "ResponseComposerAgent.small_talk_greeting",
                message,
                draft,
                "Поздоровайся и предложи помощь, перечисли категории.",
            )
        draft = (
            "Я на связи. Если нужно подобрать товар Vesta Trading — "
            "трубы, насосы, котлы, краны, канализацию или радиаторную арматуру — "
            "просто опишите задачу своими словами."
        )
        return self._polish(
            "ResponseComposerAgent.small_talk",
            message,
            draft,
            "Доброжелательно ответь и предложи помощь с подбором, без навязчивого «дела хорошо».",
        )

    def compose_confirm_last(self, cards: list[ProductCard]) -> str:
        if not cards:
            draft = "Не вижу последнего показанного товара. Уточните артикул или что нужно подобрать."
            return self._polish(
                "ResponseComposerAgent.confirm",
                "",
                draft,
                "Уточни, что нужно подобрать, потому что предыдущей карточки нет.",
            )
        card = cards[0]
        draft = (
            f"Да, это {card.sku} — {card.name}. Цена: {card.price:g} {card.currency}. "
            f"Ссылка: {card.url}"
        )
        return self._polish(
            "ResponseComposerAgent.confirm",
            card.sku,
            draft,
            "Подтверди, что речь о том же товаре, сохрани SKU, цену и ссылку из черновика.",
        )

    def compose_pending_repeat(self, pending_question: str) -> str:
        draft = (
            f"На связи. Чтобы продолжить, нужна одна деталь: {pending_question}"
        )
        return self._polish(
            "ResponseComposerAgent.pending_repeat",
            pending_question,
            draft,
            "Коротко напомни о ранее заданном вопросе и не запускай поиск без ответа.",
        )

    def compose_unknown(self) -> str:
        draft = (
            "Я консультант по товарам Vesta Trading. Могу помочь с трубами, насосами, "
            "котлами, кранами, канализацией и радиаторной арматурой. Напишите, что нужно подобрать."
        )
        return self._polish(
            "ResponseComposerAgent.unknown",
            "",
            draft,
            "Пользователь написал нетоварный или неясный запрос. Не запускай подбор без товарного намерения.",
        )

    def compose_out_of_scope(self) -> str:
        draft = (
            "Я не отвлекаюсь на нетоварные темы. Помогу подобрать товары Vesta Trading: "
            "трубы, насосы, котлы, краны, канализацию или радиаторную арматуру."
        )
        return self._polish(
            "ResponseComposerAgent.out_of_scope",
            "",
            draft,
            "Вежливо откажись от нетоварной темы и верни разговор к подбору товаров.",
        )

    def compose_no_cheaper(self, cards: list[ProductCard]) -> str:
        if cards:
            skus = ", ".join(card.sku for card in cards[:3])
            draft = (
                "Более дешёвых подходящих вариантов в данных фида не вижу. "
                f"Последний подходящий вариант: {skus}. Могу показать аналоги или передать вопрос менеджеру."
            )
        else:
            draft = (
                "Более дешёвых подходящих вариантов в данных фида не вижу. "
                "Могу показать аналоги или передать вопрос менеджеру."
            )
        return self._polish(
            "ResponseComposerAgent.no_cheaper",
            "",
            draft,
            "Честно сообщи, что более дешёвого подходящего товара нет. Не называй тот же товар более дешёвым.",
        )

    def compose_no_match(self, query: SearchQuery) -> str:
        slots = query.slots
        if query.category == "valves" and slots.get("diameter_mm"):
            draft = (
                f"Не вижу точного совпадения по крану с диаметром {slots['diameter_mm']} мм в данных фида. "
                "Уточните размер в дюймах — 1/2, 3/4 или 1 — либо передам вопрос менеджеру."
            )
        elif query.category == "sewer":
            details = []
            if slots.get("sewer_scope"):
                details.append(str(slots["sewer_scope"]))
            if slots.get("element_type"):
                details.append(str(slots["element_type"]))
            if slots.get("diameter_mm"):
                details.append(f"{slots['diameter_mm']} мм")
            if slots.get("length_mm"):
                details.append(f"длина {slots['length_mm']} мм")
            requested = ", ".join(details) or query.original_text
            draft = (
                f"Не вижу точного совпадения в фиде: {requested}. "
                "Не буду подбирать другую длину или наружную канализацию вместо нужной. "
                "Можно уточнить параметры или передать вопрос менеджеру."
            )
        else:
            draft = "Не нашёл подходящие товары в данных фида. Могу уточнить параметры или передать вопрос менеджеру."
        return self._polish(
            "ResponseComposerAgent.no_match",
            query.original_text,
            draft,
            "Честно сообщи, что точного совпадения нет. Не предлагай неподходящие товары.",
        )

    def compose_alternative_note(self, query: SearchQuery) -> str:
        requested = self._requested_summary(query)
        if requested:
            return (
                f"Точного совпадения в фиде не вижу: {requested}. "
                "Показываю ближайшие альтернативы из фида — проверьте отличия в характеристиках."
            )
        return (
            "Точного совпадения в фиде не вижу. Показываю ближайшие альтернативы из фида — "
            "проверьте отличия в характеристиках."
        )

    def compose_old_pump_note(self, query: SearchQuery) -> str | None:
        slots = query.slots
        if query.category != "pumps" or not slots.get("old_model"):
            return None

        details = []
        if slots.get("connection_size"):
            details.append(f"присоединение {slots['connection_size']}")
        if slots.get("head_m"):
            details.append(f"напор {slots['head_m']:g} м")
        if slots.get("mounting_length_mm"):
            details.append(f"монтажная длина {slots['mounting_length_mm']} мм")
        recognized = ", ".join(details)
        if recognized:
            return (
                f"По модели старого насоса {slots['old_model']} распознал ориентиры: {recognized}. "
                "Показываю варианты из фида; совместимость и монтажную длину лучше сверить по карточке."
            )
        return (
            f"Использую модель старого насоса {slots['old_model']} как ориентир. "
            "Показываю варианты из фида; совместимость лучше сверить по карточке."
        )

    def compose_clarification(
        self,
        question: str,
        small_talk: bool = False,
        user_message: str | None = None,
    ) -> str:
        prefix = ""
        if small_talk:
            normalized = (user_message or "").lower().replace("ё", "е")
            if "как дела" in normalized:
                prefix = "Дела хорошо, спасибо. "
            elif "здравств" in normalized or "добрый" in normalized:
                prefix = "Здравствуйте. "
            elif "привет" in normalized:
                prefix = "Привет. "
        draft = f"{prefix}{question}"
        return self._polish(
            "ResponseComposerAgent.clarification",
            user_message or question,
            draft,
            "Сформулируй максимум один короткий уточняющий вопрос. Не добавляй новых параметров.",
        )

    def compose_products(
        self,
        cards: list[ProductCard],
        query: SearchQuery,
        note: str | None = None,
    ) -> str:
        if not cards:
            draft = "Не нашёл подходящие товары в данных фида. Могу передать вопрос менеджеру."
            return self._polish(
                "ResponseComposerAgent.no_products",
                query.original_text,
                draft,
                "Сообщи, что подходящих товаров нет в фиде, без выдуманных альтернатив.",
            )

        lines: list[str] = []
        if note:
            lines.append(note)
        elif query.category == "boilers" and query.slots.get("area_m2"):
            area = query.slots["area_m2"]
            lines.append(
                f"Ориентир по мощности для {area:g} м² приблизительный, поэтому показываю варианты без инженерного расчёта."
            )
        else:
            lines.append("Нашёл подходящие варианты:")

        for index, card in enumerate(cards, start=1):
            lines.append(f"{index}. {card.name}")
            lines.append(f"   Артикул: {card.sku}")
            if card.brand:
                lines.append(f"   Бренд: {card.brand}")
            lines.append(f"   Цена: {card.price:g} {card.currency}")
            stock = card.stock_status
            if card.stock_qty is not None:
                stock = f"{stock}, {card.stock_qty} шт."
            lines.append(f"   Наличие: {stock}")
            if card.characteristics:
                attrs = "; ".join(
                    f"{key}: {value}" for key, value in list(card.characteristics.items())[:3]
                )
                lines.append(f"   Характеристики: {attrs}")
            lines.append(f"   Ссылка: {card.url}")

        lines.append(self._next_action(query, len(cards)))
        draft = "\n".join(lines)
        return self._polish(
            "ResponseComposerAgent.products",
            query.original_text,
            draft,
            (
                "Сделай ответ чуть более человеческим, но строго сохрани все SKU, цены, "
                "наличие и URL из черновика. Не добавляй товары, характеристики, остатки или ссылки."
            ),
        )

    def compose_link_answer(
        self,
        cards: list[ProductCard],
        selected_index: int | None = None,
    ) -> str:
        if not cards:
            draft = "Не вижу последнего показанного товара. Напишите артикул или что нужно подобрать."
            return self._polish(
                "ResponseComposerAgent.link",
                "",
                draft,
                "Коротко попроси уточнить товар, потому что предыдущей карточки нет.",
            )
        if selected_index is not None and 0 <= selected_index < len(cards):
            card = cards[selected_index]
            draft = f"Ссылка на товар {card.sku}: {card.url}"
            return self._polish(
                "ResponseComposerAgent.link",
                card.sku,
                draft,
                "Дай только прямую ссылку на уже показанный товар. Не добавляй новые ссылки.",
            )
        if len(cards) == 1:
            card = cards[0]
            draft = f"Ссылка на товар {card.sku}: {card.url}"
            return self._polish(
                "ResponseComposerAgent.link",
                card.sku,
                draft,
                "Дай только прямую ссылку на уже показанный товар. Не добавляй новые ссылки.",
            )
        lines = ["Вот ссылки на показанные товары:"]
        for index, card in enumerate(cards[:3], start=1):
            lines.append(f"{index}. {card.sku}: {card.url}")
        draft = "\n".join(lines)
        return self._polish(
            "ResponseComposerAgent.link",
            ", ".join(card.sku for card in cards[:3]),
            draft,
            "Перечисли только реальные ссылки на уже показанные товары без новых.",
        )

    def compose_complectation_confirmed(self, card: ProductCard, requested_parts: list[str]) -> str:
        parts = ", ".join(requested_parts)
        draft = (
            f"По данным фида для {card.sku} вижу подтверждение: {parts}. "
            f"Карточка товара: {card.url}"
        )
        return self._polish(
            "ResponseComposerAgent.complectation",
            parts,
            draft,
            "Ответь только по подтверждённой комплектации из фида. Не добавляй непроверенные узлы.",
        )

    def _next_action(self, query: SearchQuery, cards_count: int) -> str:
        if query.cheap:
            return "Следующее действие: Показать аналоги."
        if cards_count > 1:
            return "Следующее действие: Сравнить."
        return "Следующее действие: Показать аналоги."

    def _requested_summary(self, query: SearchQuery) -> str:
        slots = query.slots
        details: list[str] = []
        if slots.get("sewer_scope"):
            details.append(str(slots["sewer_scope"]))
        if slots.get("element_type"):
            details.append(str(slots["element_type"]))
        if slots.get("diameter_mm"):
            details.append(f"{slots['diameter_mm']} мм")
        if slots.get("length_mm"):
            details.append(f"длина {slots['length_mm']} мм")
        if slots.get("pump_type"):
            details.append(str(slots["pump_type"]))
        if slots.get("old_model"):
            details.append(f"старая модель {slots['old_model']}")
        if slots.get("connection_size"):
            details.append(f"присоединение {slots['connection_size']}")
        if slots.get("head_m"):
            details.append(f"напор {slots['head_m']:g} м")
        if slots.get("mounting_length_mm"):
            details.append(f"монтажная длина {slots['mounting_length_mm']} мм")
        if slots.get("boiler_type"):
            details.append(str(slots["boiler_type"]))
        if slots.get("area_m2"):
            details.append(f"{slots['area_m2']:g} м²")
        return ", ".join(details)

    def _polish(self, agent: str, user_message: str, draft: str, instruction: str) -> str:
        self.last_llm_requested = True
        self.last_draft = draft
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты AI-консультант интернет-магазина Vesta Trading. "
                    "Твоя задача — улучшить формулировку готового безопасного ответа. "
                    "Запрещено добавлять новые факты, товары, цены, остатки, характеристики, URL, "
                    "инженерные расчёты или обещания. Если в черновике есть карточки, сохрани все "
                    "цифры, SKU и ссылки без изменений. Отвечай кратко на русском."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Инструкция: {instruction}\n"
                    f"Сообщение пользователя: {user_message}\n"
                    f"Безопасный черновик ответа:\n{draft}\n\n"
                    "Верни только финальный текст ответа."
                ),
            },
        ]
        result = self.llm_client.complete(
            agent=agent,
            messages=messages,
            temperature=0.2,
            max_tokens=700,
        )
        self.last_llm_used = self.last_llm_used or result.llm_used
        if result.fallback_reason:
            self.last_llm_fallback_reason = result.fallback_reason
        if result.llm_used and result.content:
            return result.content.strip()
        return draft
