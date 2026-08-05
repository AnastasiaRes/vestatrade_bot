from __future__ import annotations

import re
from typing import Any

from app.models import ProductCard, SearchQuery
from app.openrouter_client import OpenRouterClient

from .utils import normalize_text


GREETING_REPLY = (
    "Добрый день. Веста Трейдинг, консультант на связи. "
    "Подскажите, что подбираем: котельную, отопление, водоснабжение или канализацию?"
)

PURE_GREETINGS = {
    "привет",
    "приветик",
    "приветствую",
    "здравствуйте",
    "здравствуй",
    "добрый день",
    "добрый вечер",
    "доброе утро",
    "доброго дня",
    "хай",
    "ку",
    "здаров",
    "здарова",
    "здорово",
}


MANAGER_PERSONA = (
    "Ты — AI-консультант интернет-магазина инженерной сантехники Vesta Trading и ведёшь "
    "диалог как опытный менеджер. Никогда не выдавай себя за человека: если клиент спрашивает, "
    "человек ты или бот, прямо ответь, что ты AI-консультант. Ассортимент: трубы PPR "
    "(включая армированные), насосы (циркуляционные, "
    "повысительные, дренажные, скважинные), котлы (газовые и электрические), краны и вентили, "
    "канализация (внутренняя и наружная), радиаторная арматура.\n"
    "Ориентиры, которыми можно делиться как общими правилами (это не инженерный расчёт): "
    "мощность котла — примерно 1 кВт на 10 м² плюс запас на утепление и горячую воду; "
    "двухконтурный котёл даёт отопление и горячую воду, одноконтурный — только отопление; "
    "монтажную длину циркуляционного насоса, присоединение, расчётные расход и напор "
    "нужно уточнять до рекомендации; нельзя предлагать «типовой» 25/6 только по площади "
    "или слову «отопление»; применимость PPR для горячей воды и отопления проверяют по паспорту, "
    "а жёсткая PPR не является трубой петли тёплого пола; "
    "закрытая камера сгорания берёт воздух с улицы через коаксиальный дымоход; "
    "у электрического котла нет камеры сгорания, поэтому никогда не приписывай ему открытую "
    "или закрытую камеру; "
    "американка — разъёмное соединение, с ним узел снимается без разборки трубы.\n"
    "Vesta Trading — наш магазин, а не магазин клиента: никогда не говори «ваш/вашего "
    "интернет-магазин» применительно к Vesta Trading. Манера: уважительная, вежливая, "
    "дружелюбная и профессиональная — представитель серьёзной компании. Обращайся к "
    "клиенту на «вы». Даже если клиент пишет резко, "
    "раздражённо или неформально, отвечай спокойно и с уважением: без ответной грубости, "
    "осуждения, фамильярности, сленга, шуточек и панибратства. Короткие, ясные фразы, "
    "без канцелярита, без markdown и без эмодзи. Если вопрос вне твоей области — мягко "
    "и культурно поясни это и вежливо верни разговор к подбору оборудования. Учитывай "
    "историю и структурированный контекст диалога и не переспрашивай то, что клиент уже "
    "сказал. Перед рекомендацией зафиксируй назначение узла и задай до трёх коротких "
    "вопросов о недостающих обязательных параметрах; если вопрос общий — сначала ответь "
    "по сути, потом предложи подбор.\n"
    "ЖЁСТКИЕ ПРАВИЛА: никогда не выдумывай товары, цены, наличие, артикулы, ссылки, акции и "
    "сроки доставки — такие факты берутся только из переданных тебе данных фида. Не делай "
    "инженерных расчётов и схем. Строго соблюдай ограничения клиента: тип котла, "
    "контурность, назначение трубы, рабочие температуру/давление, расчётные расход/напор "
    "насоса, диаметр, материал, бюджет и наличие. Если показываешь альтернативу, "
    "ясно назови, какое ограничение она не закрывает. Не повторяй дословно свой предыдущий ответ."
)


class ResponseComposerAgent:
    def __init__(self, llm_client: OpenRouterClient | None = None) -> None:
        self.llm_client = llm_client or OpenRouterClient()
        self.last_llm_used = False
        self.last_llm_requested = False
        self.last_llm_output_accepted = False
        self.last_llm_rejection_reason: str | None = None
        self.last_llm_fallback_reason: str | None = None
        self.last_draft: str | None = None
        self._history: list[dict[str, str]] = []
        self._state_summary: str = ""

    def reset_usage(self) -> None:
        self.last_llm_used = False
        self.last_llm_requested = False
        self.last_llm_output_accepted = False
        self.last_llm_rejection_reason = None
        self.last_llm_fallback_reason = None
        self.last_draft = None

    def set_history(self, history: list[dict[str, str]] | None) -> None:
        self._history = list(history or [])

    def set_state(
        self,
        category: str | None,
        slots: dict[str, Any] | None,
        last_product_summary: str | None = None,
        docs_excerpt: str | None = None,
    ) -> None:
        parts: list[str] = []
        if category:
            parts.append(f"категория: {category}")
        informative = {
            key: value for key, value in (slots or {}).items() if not isinstance(value, bool)
        }
        if informative:
            parts.append(
                "известные параметры: "
                + ", ".join(f"{key}={value}" for key, value in informative.items())
            )
        if last_product_summary:
            parts.append(f"последний показанный товар: {last_product_summary}")
        if docs_excerpt:
            parts.append(
                "выдержка из официальной документации этого товара (можно опираться на эти "
                f"факты): {docs_excerpt}"
            )
        self._state_summary = "; ".join(parts)

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
        if self._is_pure_greeting(message):
            # Для чистого приветствия вариативность не нужна: слабая модель иногда
            # превращала одну строку в длинную рекламную речь или технический опрос.
            self.last_draft = GREETING_REPLY
            return GREETING_REPLY
        # Идентичность и физические действия нельзя оставлять на усмотрение модели:
        # в живом QA она уклонялась от ответа «бот или человек» и создавала впечатление,
        # что может приехать на монтаж.
        boundary_reply = self.compose_identity_or_service(message)
        if boundary_reply:
            return boundary_reply
        fallback = self._small_talk_fallback(message)
        return self._llm_smart_reply(
            agent="ResponseComposerAgent.small_talk",
            user_message=message,
            fallback_draft=fallback,
            situation=(
                "Клиент написал нетоварное сообщение: приветствие, small talk, эмоция или "
                "вопрос о тебе. Ответь вежливо, уважительно и приветливо, кратко (1–2 "
                "предложения), на «вы». Признай содержание сообщения и мягко предложи помощь "
                "с подбором, упомянув 2–3 категории ассортимента: котлы, насосы, трубы, "
                "краны, канализация, радиаторная арматура. Без сленга, шуток, фамильярности "
                "и технической воды. ВАЖНО: не начинай подбор и не задавай технических "
                "вопросов, пока клиент сам не назвал задачу. На «как дела?» ответь спокойно "
                "от лица консультанта и сразу верни к помощи с подбором. Не спрашивай клиента "
                "в ответ «как у вас дела?» и не говори, что не можешь обсуждать личные или "
                "персональные вопросы."
            ),
        )

    def compose_identity_or_service(self, message: str) -> str | None:
        """Return a deterministic capability/identity answer when applicable."""
        if not (
            self._is_identity_question(message)
            or self._is_field_service_question(message)
        ):
            return None
        fallback = self._small_talk_fallback(message)
        self.last_draft = fallback
        return fallback

    def compose_term_consult(self, user_message: str) -> str:
        fallback = (
            "Точное значение этого термина не подскажу без проверки — не хочу вводить в "
            "заблуждение. Могу объяснить базовые понятия: монтажная длина, напор, контуры "
            "котла, типы труб и кранов. Или опишите задачу — подберу товар из ассортимента: "
            "трубы, насосы, котлы, краны, канализация, радиаторная арматура."
        )
        return self._llm_smart_reply(
            agent="ResponseComposerAgent.term_consult",
            user_message=user_message,
            fallback_draft=fallback,
            situation=(
                "Клиент спрашивает значение термина или просит что-то объяснить. Объясни "
                "простыми словами в 2–4 предложениях, без выдумок: если термин из твоей "
                "области — объясни по памятке и общим знаниям сантехники; если не уверен — "
                "честно скажи и предложи уточнить у менеджера. ЗАПРЕЩЕНО утверждать, что "
                "какой-то товар есть или отсутствует в ассортименте или наличии — это "
                "проверяется только подбором по каталогу. В конце одним предложением "
                "предложи помощь с подбором. Не задавай технических вопросов для подбора."
            ),
        )

    def _llm_smart_reply(
        self,
        agent: str,
        user_message: str,
        fallback_draft: str,
        situation: str,
    ) -> str:
        """Compose a free-form reply via LLM with manager persona and safe fallback.

        The LLM may answer general questions and give rule-of-thumb advice from the
        persona cheat-sheet, but must never invent products, prices or stock —
        those facts come only from the feed-driven flows.
        """
        self.last_llm_requested = True
        self.last_draft = fallback_draft
        system = MANAGER_PERSONA + "\n\nСитуация: " + situation
        if self._state_summary:
            system += f"\nТекущий контекст подбора клиента: {self._state_summary}."
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
            reply = result.content.strip()
            if self._repeats_last_assistant(reply):
                self.last_llm_rejection_reason = "repeated_previous_answer"
                return fallback_draft
            if self._is_degenerate(reply):
                self.last_llm_rejection_reason = "degenerate_output"
                return fallback_draft
            if self._contains_assortment_claims(reply):
                self.last_llm_rejection_reason = "ungrounded_assortment_claim"
                return fallback_draft
            self.last_llm_output_accepted = True
            return reply
        return fallback_draft

    def _contains_assortment_claims(self, reply: str) -> bool:
        """Free-form replies must not assert what the shop stocks — only feed flows may."""
        normalized = " ".join(reply.lower().replace("ё", "е").split())
        markers = [
            "в ассортименте есть",
            "у нас в ассортименте",
            "у нас есть",
            "у нас представлен",
            "есть в наличии",
            "в наличии есть",
            "в продаже есть",
            "имеется в продаже",
        ]
        return any(marker in normalized for marker in markers)

    def _is_pure_greeting(self, message: str) -> bool:
        text = normalize_text(message).strip(" .,!?-")
        return text in PURE_GREETINGS

    def _is_identity_question(self, message: str) -> bool:
        text = normalize_text(message)
        return any(
            marker in text
            for marker in [
                "ты бот",
                "вы бот",
                "бот или",
                "человек или бот",
                "живой человек",
                "ты человек",
                "вы человек",
                "кто ты",
                "кто вы",
                "искусственный интеллект",
                "ai консультант",
            ]
        )

    def _is_field_service_question(self, message: str) -> bool:
        text = normalize_text(message)
        if any(marker in text for marker in ["выех", "приех", "приед", "выезд"]):
            return True
        onsite = any(
            marker in text
            for marker in ["ко мне", "у меня", "на объект", "на дом", "по адресу"]
        )
        installation = any(marker in text for marker in ["монтаж", "смонтир", "установ", "подключ"])
        if onsite and installation:
            return True
        # Ask to perform the work, not merely to sell/select parts "для монтажа".
        return bool(
            re.search(
                r"\b(?:можешь|можете|вы)\s+(?:сами\s+)?"
                r"(?:смонтир\w*|установ\w*|подключ\w*|сдел\w*\s+монтаж|выполн\w*\s+монтаж)",
                text,
            )
            or re.search(r"\b(?:можешь|можете)\b.{0,30}\bмонтаж\w*\s+(?:сдел|выполн)", text)
        )

    def _is_degenerate(self, text: str) -> bool:
        """Detect a weak model looping (the same line/prefix over and over)."""
        from collections import Counter

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 4:
            return False
        normalized = [" ".join(line.lower().split()) for line in lines]
        if Counter(normalized).most_common(1)[0][1] >= 3:
            return True
        prefixes = [" ".join(line.split()[:3]).lower() for line in lines if len(line.split()) >= 3]
        if prefixes and Counter(prefixes).most_common(1)[0][1] >= max(4, int(len(prefixes) * 0.6)):
            return True
        return False

    def _repeats_last_assistant(self, reply: str) -> bool:
        """Weak models sometimes parrot their previous answer instead of replying anew."""
        last_assistant = next(
            (
                entry.get("content", "")
                for entry in reversed(self._history)
                if entry.get("role") == "assistant"
            ),
            "",
        )
        if not last_assistant:
            return False
        reply_norm = " ".join(reply.lower().split())
        last_norm = " ".join(last_assistant.lower().split())
        if not reply_norm or len(reply_norm) < 40:
            return False
        return reply_norm == last_norm or reply_norm in last_norm or last_norm in reply_norm

    def _small_talk_fallback(self, message: str) -> str:
        """Deterministic fallback for small talk when LLM is unavailable."""
        normalized = (message or "").lower().replace("ё", "е").strip()
        if any(
            marker in normalized
            for marker in [
                "зовут",
                "кто ты",
                "кто вы",
                "ты кто",
                "как обращ",
                "ты бот",
                "вы бот",
                "бот или",
                "живой человек",
                "ты человек",
                "вы человек",
                "искусственный интеллект",
            ]
        ):
            return (
                "Я AI-консультант Vesta Trading. Помогаю подобрать товары из ассортимента: "
                "трубы, насосы, котлы, краны, канализацию и радиаторную арматуру. "
                "Напишите, что нужно подобрать — уточню параметры и пришлю карточки."
            )
        if any(marker in normalized for marker in ["выех", "приех", "приед", "монтаж", "установ"]):
            return (
                "Я AI-консультант и не могу приехать или выполнить монтаж. Могу помочь "
                "подобрать оборудование по каталогу, а возможность выезда и установки "
                "уточнит менеджер."
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
        if any(word in normalized for word in ["классн", "красив", "умниц", "молодец", "хорош"]):
            return (
                "Спасибо, очень приятно. Помогу подобрать товары Vesta Trading по задаче: "
                "котёл, насос, трубы, краны, канализацию или радиаторную арматуру."
            )
        if "к делу" in normalized or "по делу" in normalized:
            return "Конечно. Опишите, что нужно подобрать — я уточню параметры и предложу подходящие товары из ассортимента."
        if "пока" == normalized or "до свидан" in normalized or "до встреч" in normalized:
            return "До свидания! Возвращайтесь, если понадобится подбор по ассортименту Vesta Trading."
        if any(
            greet in normalized
            for greet in ["здравств", "добрый день", "добрый вечер", "доброе утро", "привет"]
        ):
            return GREETING_REPLY
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
            situation=(
                "Клиент написал сообщение, которое не похоже на конкретный товарный запрос: "
                "общий вопрос, просьба о совете или неясная формулировка. Если это вопрос по "
                "твоей области — ответь по сути, используя ориентиры из памятки. Если просят "
                "совета по выбору — назови понятный критерий выбора и задай один уточняющий "
                "вопрос. В конце предложи подобрать конкретные варианты из каталога."
            ),
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
            situation=(
                "Клиент спрашивает что-то вне ассортимента магазина (погода, политика, "
                "личное и т.п.). Вежливо и доброжелательно, на «вы», поясните, что это вне "
                "вашей области, и культурно верните разговор к подбору, упомянув 2–3 категории. "
                "Без шуток и развязного тона — это серьёзная компания."
            ),
        )

    def compose_no_cheaper(self, cards: list[ProductCard]) -> str:
        if cards:
            skus = ", ".join(card.sku for card in cards[:3])
            draft = (
                "Более дешёвых подходящих вариантов в текущем ассортименте не вижу. "
                f"Последний подходящий вариант: {skus}. Могу показать аналоги или передать вопрос менеджеру."
            )
        else:
            draft = (
                "Более дешёвых подходящих вариантов в текущем ассортименте не вижу. "
                "Могу показать аналоги или передать вопрос менеджеру."
            )
        self.last_draft = draft
        return draft

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
        strict_single = bool(
            query
            and (
                query.slots.get("choose_one")
                or query.slots.get("result_limit") == 1
            )
        )
        if strict_single:
            alt_line = None
        elif alternative:
            alt_line = (
                f"Альтернатива: {alternative.sku} — {alternative.name}, "
                f"{alternative.price:g} {alternative.currency}."
            )
        else:
            alt_line = "Альтернатива: могу показать вариант дешевле или с запасом по характеристикам."
        sizing_warning = self._boiler_sizing_warning([card], query) if query else None
        first_line = (
            f"Рекомендую ближайшую к вашим параметрам модель: {card.sku} — {card.name}. "
            f"Цена {card.price:g} {card.currency}, наличие: {card.stock_status}."
            if sizing_warning
            else (
                f"Рекомендую: {card.sku} — {card.name}. Цена {card.price:g} {card.currency}, "
                f"наличие: {card.stock_status}."
            )
        )
        draft_lines = [first_line]
        if sizing_warning:
            draft_lines.append(sizing_warning)
        draft_lines.extend(
            [
                f"Почему: {reason_text}.",
                f"Когда не подойдёт: {self._choose_one_caveat(query, card)}",
            ]
        )
        if alt_line:
            draft_lines.append(alt_line)
        draft_lines.append(f"Ссылка: {card.url}")
        draft = "\n".join(draft_lines)
        self.last_draft = draft
        return draft

    def _choose_one_caveat(
        self,
        query: SearchQuery | None,
        card: ProductCard | None = None,
    ) -> str:
        category = query.category if query else "other"
        if category == "boilers":
            if query and card and self._boiler_sizing_warning([card], query):
                return (
                    "мощность заметно выше ориентировочного диапазона для указанной площади — "
                    "до покупки нужен расчёт теплопотерь и проверка минимальной мощности/модуляции."
                )
            return (
                "если площадь заметно больше или нужна горячая вода (двухконтурная схема) — "
                "лучше взять модель мощнее, уточните детали."
            )
        if category == "pumps":
            return (
                "если монтажная длина, присоединение или напор вашей системы отличаются — "
                "сверьте характеристики в карточке."
            )
        if category == "water_heaters":
            return (
                "если отличаются объём, накопительный/проточный тип, источник нагрева "
                "или вариант монтажа — этот товар не является совместимой заменой."
            )
        if category == "hydraulic_accumulators":
            return (
                "если это бак другого назначения (отопление вместо водоснабжения), "
                "другого расчётного объёма или присоединения — он не является заменой."
            )
        if category in {"pipes", "sewer"}:
            return "если нужен другой диаметр или назначение — уточните, подберу заново."
        return "если параметры вашей задачи отличаются от указанных — сверьте характеристики в карточке."

    def _boiler_sizing_warning(
        self,
        cards: list[ProductCard],
        query: SearchQuery,
    ) -> str | None:
        if query.category != "boilers" or not query.slots.get("area_m2") or not cards:
            return None
        area = float(query.slots["area_m2"])
        base_kw = area / 10.0
        upper_kw = base_kw * 1.3
        powers = [power for card in cards if (power := self._card_power_kw(card)) is not None]
        if powers and min(powers) < base_kw:
            return (
                f"Для {area:g} м² предварительный ориентир — не меньше примерно "
                f"{base_kw:g} кВт до поправок на теплопотери и ГВС. Позиции ниже этого "
                "ориентира показываю только как пограничные: не считаю их достаточными "
                "или имеющими запас без теплотехнического расчёта."
            )
        if not powers or min(powers) <= upper_kw * 1.25:
            return None
        closest_card = min(
            (card for card in cards if self._card_power_kw(card) is not None),
            key=lambda card: self._card_power_kw(card) or float("inf"),
        )
        passport_range = self._card_passport_power_range(closest_card)
        if passport_range:
            minimum, maximum = passport_range
            min_text = f"{minimum:g}".replace(".", ",")
            max_text = f"{maximum:g}".replace(".", ",")
            return (
                f"Для {area:g} м² предварительный ориентир — примерно "
                f"{base_kw:g}–{upper_kw:g} кВт. Самая маломощная найденная модель рассчитана "
                f"на {min(powers):g} кВт, но по техническому паспорту может снижать "
                f"теплопроизводительность примерно до {min_text} кВт "
                f"(диапазон {min_text}–{max_text} кВт). Поэтому она не работает постоянно "
                f"на максимуме, однако при теплопотреблении ниже {min_text} кВт возможны "
                "более частые включения. Окончательный вывод зависит от теплопотерь здания."
            )
        return (
            f"Для {area:g} м² предварительный ориентир — около {base_kw:g}–{upper_kw:g} кВт. "
            f"Минимальная найденная модель имеет {min(powers):g} кВт, то есть заметно больше; "
            "показываю её только как ближайший вариант, а не как автоматически оптимальный подбор."
        )

    def _card_passport_power_range(self, card: ProductCard) -> tuple[float, float] | None:
        for key, value in card.characteristics.items():
            if "диапазон мощности отопления по паспорту" not in normalize_text(key):
                continue
            numbers = [
                float(raw.replace(",", "."))
                for raw in re.findall(r"\d+(?:[,.]\d+)?", str(value))
            ]
            if len(numbers) >= 2 and 0 < numbers[0] <= numbers[1]:
                return numbers[0], numbers[1]
        return None

    def _card_power_kw(self, card: ProductCard) -> float | None:
        for value in [card.name, *card.characteristics.values()]:
            match = re.search(r"(\d+(?:[,.]\d+)?)\s*квт", normalize_text(str(value)))
            if match:
                return float(match.group(1).replace(",", "."))
        for key, value in card.characteristics.items():
            key_text = normalize_text(str(key))
            if "мощ" not in key_text or "квт" not in key_text:
                continue
            number = re.search(r"\d+(?:[,.]\d+)?", str(value))
            if number:
                return float(number.group(0).replace(",", "."))
        return None

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
        lines = ["Сравниваю показанные варианты по карточкам товаров:"]
        for card in cards:
            stock = card.stock_status
            if card.stock_qty is not None:
                stock = f"{stock}, {card.stock_qty} шт."
            parts = [f"цена {card.price:g} {card.currency}", f"наличие: {stock}"]
            for key in seen_keys[:4]:
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
        self.last_draft = draft
        return draft

    def compose_no_match(self, query: SearchQuery) -> str:
        slots = query.slots
        if query.category == "valves" and slots.get("diameter_mm"):
            draft = (
                f"Не вижу точного совпадения по крану с диаметром {slots['diameter_mm']} мм в текущем ассортименте. "
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
                f"Не вижу точного совпадения в ассортименте: {requested}. "
                "Не буду подбирать другую длину или наружную канализацию вместо нужной. "
                "Можно уточнить параметры или передать вопрос менеджеру."
            )
        elif query.category == "boilers":
            requested = self._requested_summary(query) or query.original_text
            hot_water_note = (
                " Для электрического отопления с ГВС может понадобиться отдельный "
                "бойлер, но его нужно подбирать как отдельный узел."
                if slots.get("boiler_type") == "электрический"
                and slots.get("contours") == "двухконтурный"
                else ""
            )
            draft = (
                f"Не вижу точного совпадения в ассортименте: {requested}. "
                "Не буду показывать котёл, нарушающий бюджет или обязательные характеристики. "
                "Если захотите, можно отдельно разрешить ослабить одно из условий."
                f"{hot_water_note}"
            )
        elif (
            query.category == "pumps"
            and slots.get("flow_unit_status") == "estimated_standard_hose"
        ):
            estimate = self.compose_pump_estimate_note(query)
            draft = (
                f"{estimate}\n\n"
                "По этим параметрам подходящей позиции в текущем ассортименте не нашёл. "
                "Можно уточнить фактический расход или передать подбор менеджеру."
            )
        elif query.category == "water_heaters":
            requested = self._requested_summary(query) or query.original_text
            if slots.get("allow_alternatives") is True:
                draft = (
                    f"Не вижу совпадения в ассортименте: {requested}. Даже среди аналогов "
                    "с теми же обязательными параметрами подходящего товара нет. Уточните, "
                    "какое одно условие можно изменить: объём, тип водонагревателя, источник "
                    "нагрева, монтаж, бюджет или требование наличия."
                )
            else:
                draft = (
                    f"Не вижу точного совпадения в ассортименте: {requested}. "
                    "Не буду подменять накопительный водонагреватель проточным, электрический "
                    "косвенным или менять требуемый объём без вашего согласия. Можно отдельно "
                    "разрешить изменить одно из условий."
                )
        elif query.category == "hydraulic_accumulators":
            requested = self._requested_summary(query) or query.original_text
            draft = (
                f"Не вижу точного совпадения в ассортименте: {requested}. "
                "Не буду подменять гидроаккумулятор для водоснабжения расширительным "
                "баком отопления или менять расчётный объём без вашего согласия."
            )
        elif query.category == "filters":
            requested = self._requested_summary(query) or query.original_text
            draft = (
                f"Не вижу точного совпадения в ассортименте: {requested}. "
                "Не буду подменять типоразмер или назначение картриджа; проверьте "
                "формат, технологию очистки и тонкость фильтрации."
            )
        elif query.category == "controls":
            requested = self._requested_summary(query) or query.original_text
            draft = (
                f"Не вижу точного совпадения в ассортименте: {requested}. "
                "Проверьте тип автоматики, питание, нормальное состояние и сигнал управления."
            )
        else:
            draft = "Не нашёл подходящие товары в текущем ассортименте. Могу уточнить параметры или передать вопрос менеджеру."
        self.last_draft = draft
        return draft

    @staticmethod
    def compose_pump_estimate_note(query: SearchQuery) -> str:
        slots = query.slots
        if (
            query.category != "pumps"
            or slots.get("flow_unit_status") != "estimated_standard_hose"
        ):
            return ""

        flow_l_min = float(slots.get("required_flow_l_min") or 20.0)
        flow_m3_h = float(slots.get("required_flow_m3_h") or flow_l_min * 0.06)
        pressure_bar = float(slots.get("required_pressure_bar") or 2.0)
        lift_height_m = float(slots.get("lift_height_m") or 0.0)
        details = [
            f"один стандартный садовый шланг — {flow_l_min:g} л/мин "
            f"({flow_m3_h:g} м³/ч)",
            f"давление у шланга — {pressure_bar:g} бар",
            f"дополнительный перепад участка — {lift_height_m:g} м",
        ]
        note = (
            "Предварительно считаю по допущениям: "
            + "; ".join(details)
            + ". 30 минут — это продолжительность полива, а не расход: "
            "если известен фактический объём воды или шлангов несколько, расчёт нужно уточнить."
        )
        if slots.get("required_head_m") is not None:
            note += (
                " С учётом сохранённых глубины и горизонтальной трассы "
                f"расчётный ориентир по напору — {float(slots['required_head_m']):g} м."
            )
        if normalize_text(str(slots.get("pump_type") or "")) == "колодезный":
            note += (
                " При глубине до воды больше 8 м нужен погружной колодезный насос; "
                "поверхностный насос здесь не подходит."
            )
        return note

    def compose_alternative_note(self, query: SearchQuery) -> str:
        # Электрических двухконтурных в фиде нет — это типовая ситуация, объясняем по-человечески.
        if (
            query.category == "boilers"
            and query.slots.get("boiler_type") == "электрический"
            and query.slots.get("contours") == "двухконтурный"
        ):
            return (
                "Электрического двухконтурного котла в наличии нет — у электрических обычно один "
                "контур. Показываю одноконтурный вариант: для горячей воды к нему ставят отдельный "
                "бойлер косвенного нагрева."
            )
        if query.category == "boilers" and query.slots.get("contours") == "двухконтурный":
            boiler_type = query.slots.get("boiler_type")
            type_text = f" типа «{boiler_type}»" if boiler_type else ""
            return (
                f"Точного двухконтурного котла{type_text} в текущем ассортименте не вижу. "
                "Ниже только ближайшие альтернативы: если в характеристиках указан один "
                "контур, горячую воду нужно решать отдельно через бойлер или другую схему."
            )
        requested = self._requested_summary(query)
        if requested:
            return (
                f"Точного совпадения в ассортименте не вижу: {requested}. "
                "Показываю ближайшие альтернативы — проверьте отличия в характеристиках."
            )
        return (
            "Точного совпадения в ассортименте не вижу. Показываю ближайшие альтернативы — "
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
                "Показываю варианты из ассортимента; совместимость и монтажную длину лучше сверить по карточке."
            )
        return (
            f"Использую модель старого насоса {slots['old_model']} как ориентир. "
            "Показываю варианты из ассортимента; совместимость лучше сверить по карточке."
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
                prefix = "Здравствуйте. "
        draft = f"{prefix}{question}"
        if "м²" in question and "примерно на" in question:
            # The acknowledgement is part of conversational memory. A stylistic
            # rewrite must not turn "котёл на 100" back into a context-free question.
            self.last_draft = draft
            return draft
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
            draft = "Не нашёл подходящие товары в текущем ассортименте. Могу передать вопрос менеджеру."
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
            sizing_warning = self._boiler_sizing_warning(cards, query)
            if sizing_warning:
                lines.append(sizing_warning)
            else:
                lines.append(
                    f"Ориентир по мощности для {area:g} м² предварительный; точный подбор "
                    "зависит от теплопотерь здания."
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
                characteristic_limit = (
                    5
                    if query.category == "water_heaters"
                    else 4 if query.category == "boilers" else 3
                )
                attrs = "; ".join(
                    f"{key}: {value}"
                    for key, value in list(card.characteristics.items())[:characteristic_limit]
                )
                lines.append(f"   Характеристики: {attrs}")
            lines.append(f"   Ссылка: {card.url}")

        if query.category == "boilers" and query.slots.get("needs_chimney"):
            chimney_type = query.slots.get("chimney_type") or "требуемый тип"
            chimney_size = query.slots.get("chimney_size")
            size_text = f" {chimney_size}" if chimney_size else ""
            lines.append(
                "Дымоход зафиксировал как второй обязательный компонент: "
                f"{chimney_type}{size_text}. Его нельзя выбирать универсально — сначала "
                "нужно выбрать точную модель котла и сверить по её паспорту диаметр, "
                "состав комплекта и допустимую длину."
            )

        lines.append(self._next_action(query, len(cards)))
        draft = "\n".join(lines)
        self.last_draft = draft
        return draft

    def compose_term_explanation(self, term: str, explanation: str) -> str:
        draft = f"{term}: {explanation}"
        return self._polish(
            "ResponseComposerAgent.term",
            term,
            draft,
            "Объясни термин простыми словами, коротко, без новых товарных фактов.",
        )

    def answer_in_context(self, user_message: str, context_block: str, fallback: str) -> str:
        """Grounded conversational answer about products already shown to the customer.

        The model gets the full card facts (price, stock, specs, passport, built-in
        parts) of the shown products and the dialogue, and answers the free-form
        follow-up using ONLY those facts. The orchestrator guards the result against
        invented prices/SKUs/stock before sending it.
        """
        self.last_llm_requested = True
        self.last_draft = fallback
        system = (
            MANAGER_PERSONA
            + "\n\nСитуация: клиент задаёт вопрос про товары, которые ты уже показал в этом "
            "диалоге. Ниже приведены точные данные их карточек (цена, наличие, остаток, "
            "характеристики, паспорт, встроенные узлы). Ответь на вопрос, опираясь СТРОГО на "
            "эти данные и историю диалога:\n"
            "- спрашивают про наличие/цену/характеристику конкретной позиции — назови её из данных;\n"
            "- просят посоветовать/«что лучше» — порекомендуй одну позицию и кратко объясни почему "
            "(по мощности, цене, наличию), при равенстве — самую дешёвую или с большим остатком;\n"
            "- спрашивают «под какой котёл/систему подходит», про совместимость — отвечай по типу и "
            "характеристикам товара и истории диалога;\n"
            "- просят данные из паспорта — используй приведённый текст паспорта;\n"
            "- если для ответа реально не хватает данных в карточках — честно скажи об этом и "
            "предложи уточнить или передать менеджеру.\n"
            "СТРОГО ЗАПРЕЩЕНО придумывать цены, артикулы, остатки, характеристики и ссылки, которых "
            "нет в данных ниже. Не повторяй весь список карточек — отвечай по существу вопроса.\n\n"
            "ДАННЫЕ ПОКАЗАННЫХ ТОВАРОВ:\n" + context_block
        )
        messages = [
            {"role": "system", "content": system},
            *self._history_messages(),
            {"role": "user", "content": user_message or "(пустое сообщение)"},
        ]
        result = self.llm_client.complete(
            agent="ResponseComposerAgent.context",
            messages=messages,
            temperature=0.3,
            max_tokens=280,
        )
        self.last_llm_used = self.last_llm_used or result.llm_used
        if result.fallback_reason:
            self.last_llm_fallback_reason = result.fallback_reason
        if result.llm_used and result.content and result.content.strip():
            reply = result.content.strip()
            if self._repeats_last_assistant(reply):
                self.last_llm_rejection_reason = "repeated_previous_answer"
                return fallback
            if self._is_degenerate(reply):
                self.last_llm_rejection_reason = "degenerate_output"
                return fallback
            self.last_llm_output_accepted = True
            return reply
        return fallback

    def compose_builtin_components(
        self,
        card: ProductCard,
        components: list[str],
    ) -> str:
        if components:
            parts = ", ".join(components)
            draft = (
                f"По описанию карточки {card.sku} ({card.name}) в котёл встроены: {parts}. "
                f"Полную комплектацию поставки лучше сверить в паспорте или у менеджера. "
                f"Карточка: {card.url}"
            )
        else:
            draft = (
                f"В данных карточки {card.sku} состав комплекта поставки не детализирован. "
                f"По характеристикам это {card.name}. Точную комплектацию подскажет менеджер "
                f"или паспорт изделия. Карточка: {card.url}"
            )
        self.last_draft = draft
        return draft

    def compose_link_answer(
        self,
        cards: list[ProductCard],
        selected_index: int | None = None,
        *,
        include_name: bool = False,
    ) -> str:
        if not cards:
            draft = "Не вижу последнего показанного товара. Напишите артикул или что нужно подобрать."
            self.last_draft = draft
            return draft
        if selected_index is not None and 0 <= selected_index < len(cards):
            card = cards[selected_index]
            draft = (
                f"{card.name}. Артикул: {card.sku}. Ссылка: {card.url}"
                if include_name
                else f"Ссылка на товар {card.sku}: {card.url}"
            )
            self.last_draft = draft
            return draft
        if len(cards) == 1:
            card = cards[0]
            draft = (
                f"{card.name}. Артикул: {card.sku}. Ссылка: {card.url}"
                if include_name
                else f"Ссылка на товар {card.sku}: {card.url}"
            )
            self.last_draft = draft
            return draft
        lines = ["Вот ссылки на показанные товары:"]
        for index, card in enumerate(cards[:3], start=1):
            label = f"{card.name}. Артикул: {card.sku}" if include_name else card.sku
            lines.append(f"{index}. {label}: {card.url}")
        draft = "\n".join(lines)
        self.last_draft = draft
        return draft

    def compose_complectation_confirmed(self, card: ProductCard, requested_parts: list[str]) -> str:
        parts = ", ".join(requested_parts)
        draft = (
            f"Да, в карточке {card.sku} вижу подтверждение: {parts}. "
            "Это подтверждает только указанный элемент товара или комплекта; необходимость "
            "дополнительных узлов зависит от конкретной системы. "
            f"Карточка товара: {card.url}"
        )
        self.last_draft = draft
        return draft

    def _next_action(self, query: SearchQuery, cards_count: int) -> str:
        if query.cheap:
            return "Могу показать сопоставимые аналоги."
        if query.category == "boilers" and cards_count == 1:
            return (
                "Чтобы проверить применимость точнее, уточните регион, утепление "
                "и высоту потолков."
            )
        if query.category == "water_heaters" and cards_count == 1:
            return (
                "Перед покупкой сверьте способ монтажа, подвод воды, электропитание "
                "или источник нагрева с паспортом этой модели."
            )
        if cards_count > 1:
            return "Могу сравнить эти варианты по главным отличиям для вашей задачи."
        return "Могу показать сопоставимые аналоги."

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
        if slots.get("energy_source"):
            details.append(str(slots["energy_source"]))
        if slots.get("heater_type"):
            details.append(str(slots["heater_type"]))
        if slots.get("volume_l") is not None:
            details.append(f"{float(slots['volume_l']):g} л")
        if slots.get("mounting"):
            details.append(f"монтаж: {slots['mounting']}")
        if slots.get("orientation"):
            details.append(f"ориентация: {slots['orientation']}")
        if slots.get("contours"):
            details.append(str(slots["contours"]))
        if slots.get("area_m2"):
            details.append(f"{slots['area_m2']:g} м²")
        if slots.get("power_kw") is not None:
            details.append(f"{float(slots['power_kw']):g} кВт")
        if slots.get("voltage_v"):
            details.append(f"{int(slots['voltage_v'])} В")
        if slots.get("max_price") is not None:
            details.append(f"до {float(slots['max_price']):g} RUB")
        if slots.get("min_price") is not None:
            details.append(f"от {float(slots['min_price']):g} RUB")
        if slots.get("required_features"):
            details.append(
                "обязательно: " + ", ".join(str(item) for item in slots["required_features"])
            )
        if slots.get("excluded_features"):
            details.append(
                "без: " + ", ".join(str(item) for item in slots["excluded_features"])
            )
        if slots.get("required_builtin_parts"):
            details.append(
                "встроено: "
                + ", ".join(str(item) for item in slots["required_builtin_parts"])
            )
        if slots.get("excluded_builtin_parts"):
            details.append(
                "без встроенных компонентов: "
                + ", ".join(str(item) for item in slots["excluded_builtin_parts"])
            )
        if query.in_stock_only or slots.get("in_stock"):
            details.append("только в наличии")
        if slots.get("result_limit") == 1:
            details.append("1 вариант")
        return ", ".join(details)

    def _polish(self, agent: str, user_message: str, draft: str, instruction: str) -> str:
        self.last_llm_requested = True
        self.last_draft = draft
        context_block = self._history_text()
        context_part = f"Недавний диалог:\n{context_block}\n" if context_block else ""
        if self._state_summary:
            context_part += f"Текущий контекст подбора: {self._state_summary}.\n"
        messages = [
            {
                "role": "system",
                "content": (
                    MANAGER_PERSONA
                    + "\n\nСитуация: тебе дан готовый безопасный черновик ответа. Твоя задача — "
                    "только улучшить его формулировку, чтобы он звучал как ответ опытного "
                    "AI-консультанта: связно с диалогом, без повторения уже сказанного как будто "
                    "впервые. Запрещено добавлять новые факты, товары, цены, остатки, "
                    "характеристики, URL, расчёты или обещания. Если в черновике есть карточки, "
                    "сохрани все цифры, SKU и ссылки без изменений. Отвечай кратко."
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
            self.last_llm_output_accepted = True
            return result.content.strip()
        return draft
