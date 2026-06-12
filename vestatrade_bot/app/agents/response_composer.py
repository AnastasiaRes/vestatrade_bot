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
        self._history: list[dict[str, str]] = []

    def reset_usage(self) -> None:
        self.last_llm_used = False
        self.last_llm_requested = False
        self.last_llm_fallback_reason = None
        self.last_draft = None

    def set_history(self, history: list[dict[str, str]] | None) -> None:
        self._history = list(history or [])

    def _history_messages(self, limit: int = 8, max_chars: int = 600) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for entry in self._history[-limit:]:
            role = entry.get("role")
            content = (entry.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content:
                continue
            if len(content) > max_chars:
                content = content[:max_chars] + "…"
            messages.append({"role": role, "content": content})
        return messages

    def _history_text(self, limit: int = 6, max_chars: int = 300) -> str:
        lines: list[str] = []
        for entry in self._history[-limit:]:
            content = (entry.get("content") or "").strip()
            if not content:
                continue
            if len(content) > max_chars:
                content = content[:max_chars] + "…"
            speaker = "Клиент" if entry.get("role") == "user" else "Бот"
            lines.append(f"{speaker}: {content}")
        return "\n".join(lines)

    def compose_small_talk(self, message: str) -> str:
        return self._llm_smart_reply(
            agent="ResponseComposerAgent.small_talk",
            user_message=message,
            fallback_draft=self._small_talk_fallback(message),
        )

    def _llm_smart_reply(
        self,
        agent: str,
        user_message: str,
        fallback_draft: str,
    ) -> str:
        """Compose a small-talk / non-product reply via LLM with safe fallback.

        The LLM is allowed to acknowledge the user's message naturally, but is
        instructed to always steer the conversation back to product selection
        within Vesta Trading and to never invent products, prices or claims.
        """
        self.last_llm_requested = True
        self.last_draft = fallback_draft
        system = (
            "Ты — AI-консультант интернет-магазина Vesta Trading. "
            "Магазин продаёт инженерную сантехнику. Категории: трубы, насосы, "
            "котлы, краны, канализация, радиаторная арматура. "
            "Сейчас пользователь написал нетоварное сообщение (приветствие, "
            "small talk, эмоция, нестандартный вопрос или жалоба). Твои правила:\n"
            "1. Ответь живо и кратко (1–3 предложения), признай содержание сообщения "
            "пользователя — не игнорируй его и не отвечай шаблоном.\n"
            "1а. Тебе передана история диалога — обязательно учитывай её: если "
            "пользователь ссылается на сказанное ранее, что-то переспрашивает или "
            "продолжает мысль, отвечай в контексте, а не как на первое сообщение.\n"
            "2. Если пользователь спрашивает что-то вне ассортимента (погода, "
            "философия, личное), вежливо обозначь, что это вне твоей компетенции.\n"
            "3. В конце мягко верни разговор к подбору товаров: перечисли 2–4 "
            "категории и предложи описать задачу.\n"
            "4. ЗАПРЕЩЕНО: выдумывать товары, цены, наличие, характеристики, "
            "акции, скидки, ссылки, обещания доставки, факты о компании. Не "
            "ставь диагнозы и не давай инженерных расчётов.\n"
            "5. Отвечай по-русски, без markdown-разметки и без эмодзи."
        )
        messages = [
            {"role": "system", "content": system},
            *self._history_messages(),
            {"role": "user", "content": user_message or "(пустое сообщение)"},
        ]
        result = self.llm_client.complete(
            agent=agent,
            messages=messages,
            temperature=0.5,
            max_tokens=220,
        )
        self.last_llm_used = self.last_llm_used or result.llm_used
        if result.fallback_reason:
            self.last_llm_fallback_reason = result.fallback_reason
        if result.llm_used and result.content and result.content.strip():
            return result.content.strip()
        return fallback_draft

    def _small_talk_fallback(self, message: str) -> str:
        """Deterministic fallback for small talk when LLM is unavailable."""
        normalized = (message or "").lower().replace("ё", "е").strip()
        if "зовут" in normalized or "кто ты" in normalized or "ты кто" in normalized or "как обращ" in normalized:
            return (
                "Я AI-консультант Vesta Trading. Помогаю подобрать товары из фида: "
                "трубы, насосы, котлы, краны, канализацию и радиаторную арматуру. "
                "Напишите, что нужно подобрать — уточню параметры и пришлю карточки."
            )
        if "что ты умеешь" in normalized or "что умеешь" in normalized or "помоги" in normalized or "у меня вопрос" in normalized:
            return (
                "Я помогу подобрать товар по запросу, уточню цену, наличие и характеристики "
                "и дам прямую ссылку на карточку. Категории: трубы, насосы, котлы, краны, "
                "канализация и радиаторная арматура. Опишите задачу своими словами."
            )
        if "спасибо" in normalized or "благодарю" in normalized:
            return "Пожалуйста! Если нужно, могу показать аналоги, варианты подешевле или передать вопрос менеджеру."
        if "как дела" in normalized or "как ты" == normalized or normalized.startswith("как ты "):
            return "Дела хорошо, спасибо. Готов помочь с подбором товаров Vesta Trading — что нужно?"
        if "пока" == normalized or "до свидан" in normalized or "до встреч" in normalized:
            return "До свидания! Возвращайтесь, если понадобится подбор по ассортименту Vesta Trading."
        if any(greet in normalized for greet in ["здравств", "добрый день", "добрый вечер", "доброе утро"]):
            return (
                "Здравствуйте! Я AI-консультант Vesta Trading. "
                "Опишите, что нужно подобрать — трубы, насосы, котлы, краны, "
                "канализацию или радиаторную арматуру, и я уточню параметры."
            )
        if "привет" in normalized:
            return (
                "Привет! Я AI-консультант Vesta Trading. Опишите, что нужно подобрать — "
                "трубы, насосы, котлы, краны, канализацию или радиаторную арматуру."
            )
        return (
            "Я на связи. Если нужно подобрать товар Vesta Trading — "
            "трубы, насосы, котлы, краны, канализацию или радиаторную арматуру — "
            "просто опишите задачу своими словами."
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

    def compose_unknown(self, user_message: str = "") -> str:
        fallback = (
            "Я консультант по товарам Vesta Trading. Могу помочь с трубами, насосами, "
            "котлами, кранами, канализацией и радиаторной арматурой. Напишите, что нужно подобрать."
        )
        return self._llm_smart_reply(
            agent="ResponseComposerAgent.unknown",
            user_message=user_message,
            fallback_draft=fallback,
        )

    def compose_out_of_scope(self, user_message: str = "") -> str:
        fallback = (
            "Это вне моей компетенции — я по ассортименту Vesta Trading. "
            "Помогу подобрать трубы, насосы, котлы, краны, канализацию или радиаторную арматуру."
        )
        return self._llm_smart_reply(
            agent="ResponseComposerAgent.out_of_scope",
            user_message=user_message,
            fallback_draft=fallback,
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

    def compose_choose_one(
        self,
        card: ProductCard,
        query: SearchQuery | None = None,
        alternative: ProductCard | None = None,
    ) -> str:
        reasons = []
        if card.stock_status:
            reasons.append(f"наличие: {card.stock_status}")
        if card.price is not None:
            reasons.append(f"цена {card.price:g} {card.currency}")
        if query and query.slots:
            slot_reasons = self._requested_summary(query)
            if slot_reasons:
                reasons.append(f"совпадает с параметрами: {slot_reasons}")
        reason_text = "; ".join(reasons) or "он лучше всего совпадает с текущим подбором"
        if alternative:
            alt_line = (
                f"Альтернатива: {alternative.sku} — {alternative.name}, "
                f"{alternative.price:g} {alternative.currency}."
            )
        else:
            alt_line = "Альтернатива: могу показать вариант дешевле или с запасом по характеристикам."
        draft = "\n".join(
            [
                f"Рекомендую: {card.sku} — {card.name}. Цена {card.price:g} {card.currency}, "
                f"наличие: {card.stock_status}.",
                f"Почему: {reason_text}.",
                f"Когда не подойдёт: {self._choose_one_caveat(query)}",
                alt_line,
                f"Ссылка: {card.url}",
            ]
        )
        return self._polish(
            "ResponseComposerAgent.choose_one",
            card.sku,
            draft,
            (
                "Сохрани структуру: Рекомендую / Почему / Когда не подойдёт / Альтернатива. "
                "Сохрани SKU, цены, наличие и ссылку из черновика без изменений."
            ),
        )

    def _choose_one_caveat(self, query: SearchQuery | None) -> str:
        category = query.category if query else "other"
        if category == "boilers":
            return (
                "если площадь заметно больше или нужна горячая вода (двухконтурная схема) — "
                "лучше взять модель мощнее, уточните детали."
            )
        if category == "pumps":
            return (
                "если монтажная длина, присоединение или напор вашей системы отличаются — "
                "сверьте характеристики в карточке."
            )
        if category in {"pipes", "sewer"}:
            return "если нужен другой диаметр или назначение — уточните, подберу заново."
        return "если параметры вашей задачи отличаются от указанных — сверьте характеристики в карточке."

    def compose_comparison(self, cards: list[ProductCard]) -> str:
        cards = cards[:3]
        seen_keys: list[str] = []
        for card in cards:
            for key in card.characteristics:
                if key not in seen_keys:
                    seen_keys.append(key)
        diff_keys = [
            key
            for key in seen_keys
            if len({card.characteristics.get(key) for card in cards}) > 1
        ]
        lines = ["Сравниваю показанные варианты по данным фида:"]
        for card in cards:
            parts = [f"цена {card.price:g} {card.currency}", f"наличие: {card.stock_status}"]
            for key in diff_keys[:2]:
                value = card.characteristics.get(key)
                if value:
                    parts.append(f"{key}: {value}")
            lines.append(f"- {card.sku} — {card.name}: {'; '.join(parts)}")
        if diff_keys:
            key = diff_keys[0]
            values = " против ".join(
                str(card.characteristics.get(key, "не указано")) for card in cards
            )
            lines.append(f"Главное отличие — {key}: {values}.")
        else:
            values = " против ".join(f"{card.price:g} {card.currency}" for card in cards)
            lines.append(f"Главное отличие — цена: {values}.")
        lines.append("Если опишете вашу систему, порекомендую один вариант.")
        draft = "\n".join(lines)
        return self._polish(
            "ResponseComposerAgent.comparison",
            ", ".join(card.sku for card in cards),
            draft,
            (
                "Сравни товары только по фактам из черновика. Сохрани все SKU, цены и "
                "значения характеристик без изменений, ничего не добавляй."
            ),
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

    def compose_term_explanation(self, term: str, explanation: str) -> str:
        draft = f"{term}: {explanation}"
        return self._polish(
            "ResponseComposerAgent.term",
            term,
            draft,
            "Объясни термин простыми словами, коротко, без новых товарных фактов.",
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
        context_block = self._history_text()
        context_part = f"Недавний диалог:\n{context_block}\n" if context_block else ""
        messages = [
            {
                "role": "system",
                "content": (
                    "Ты AI-консультант интернет-магазина Vesta Trading. "
                    "Твоя задача — улучшить формулировку готового безопасного ответа. "
                    "Учитывай недавний диалог, чтобы ответ звучал связно и не повторял "
                    "уже сказанное как будто впервые. "
                    "Запрещено добавлять новые факты, товары, цены, остатки, характеристики, URL, "
                    "инженерные расчёты или обещания. Если в черновике есть карточки, сохрани все "
                    "цифры, SKU и ссылки без изменений. Отвечай кратко на русском."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Инструкция: {instruction}\n"
                    f"{context_part}"
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
