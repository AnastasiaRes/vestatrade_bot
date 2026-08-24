from __future__ import annotations

import re
from typing import Any

from app.models import ProductCard, SearchQuery
from app.openrouter_client import OpenRouterClient

from .product_card import constrained_characteristic_keys
from .product_constraints import normalize_thread_pair
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

    # Проверенные определения. Живой прогон показал, что модель уверенно
    # выдумывает нишевые сокращения: на «что значит ВР/ВР?» она ответила
    # «врезное/врезное… крепится фитингами, а не резьбой» — то есть прямо
    # противоположное. Промпт «если не уверен — скажи» не помог, потому что
    # модель не была не уверена. Поэтому известный термин отвечается
    # детерминированно, ровно как цена берётся из фида, а не из памяти модели.
    MOUNTING_LENGTH_DEFINITION = (
        "Монтажная длина — размер face-to-face: расстояние по оси изделия между "
        "плоскостями подключений, то есть двумя присоединительными плоскостями. "
        "Для циркуляционного насоса её измеряют "
        "от одной присоединительной плоскости корпуса до другой, без учёта накидных "
        "гаек, переходников и длины труб. Типовые значения — 130 или 180 мм; точный "
        "размер нужно сверять по паспорту изделия."
    )

    CLOSED_CIRCULATION_HEAD_DEFINITION = (
        "Напор циркуляционного насоса в закрытой системе выбирают по суммарным "
        "гидравлическим потерям расчётного циркуляционного кольца при требуемом расходе: "
        "в трубах, арматуре, теплообменниках и других элементах. Геометрическую высоту "
        "здания к напору не прибавляют: в замкнутом контуре статические столбы "
        "теплоносителя взаимно уравновешиваются."
    )

    GENERAL_PUMP_HEAD_DEFINITION = (
        "Напор — удельная энергия, которую насос передаёт жидкости; обычно её выражают "
        "в метрах водяного столба. Для подъёма воды или водоснабжения учитывают "
        "геометрический подъём, требуемое давление в точке разбора и гидравлические "
        "потери. В закрытой циркуляционной системе геометрическую высоту не прибавляют: "
        "напор насоса определяют по гидравлическим потерям расчётного кольца."
    )

    BOILER_CONTOUR_DEFINITION = (
        "Контур котла — функциональный тракт нагрева внутри котла. Одноконтурный котёл "
        "обслуживает отопление, а двухконтурный дополнительно готовит горячую воду для "
        "ГВС во втором тракте. Это не то же самое, что отдельная трубная петля тёплого пола."
    )

    WARM_FLOOR_CONTOUR_DEFINITION = (
        "Контур тёплого пола — отдельная петля трубы: она выходит из подающего коллектора, "
        "проходит по своей зоне пола и возвращается в обратный коллектор. Длину и число "
        "таких петель определяют расчётом по площади, шагу укладки и допустимым "
        "гидравлическим потерям. Это не «контурность» котла."
    )

    AMBIGUOUS_CONTOUR_DEFINITION = (
        "Слово «контур» используют в двух разных смыслах. У котла это функциональный "
        "тракт отопления или ГВС; у тёплого пола — отдельная трубная петля от подающего "
        "коллектора к обратному. Уточните, речь о контуре котла или о петле тёплого пола."
    )

    TERM_GLOSSARY: tuple[tuple[tuple[str, ...], str], ...] = (
        (
            ("вр/вр", "вр-вр", "вр вр", "ff", "вн.-вн", "вн-вн"),
            "ВР/ВР — внутренняя резьба с обеих сторон (обозначают ещё «вн.-вн.» или ff). "
            "То есть в оба конца детали вкручивается наружная резьба ответной трубы или фитинга.",
        ),
        (
            ("вр/нр", "вр-нр", "вн/нр", "вн.-нар", "fm", "мама-папа", "мама папа"),
            "ВР/НР — с одной стороны внутренняя резьба, с другой наружная "
            "(обозначают «вн.-нар.» или fm; на монтажном сленге «мама-папа»).",
        ),
        (
            ("нр/нр", "нр-нр", "нар.-нар", "mm"),
            "НР/НР — наружная резьба с обеих сторон (обозначают «нар.-нар.» или mm).",
        ),
        (
            ("монтажная длина", "монтажную длину", "монтажной длины"),
            MOUNTING_LENGTH_DEFINITION,
        ),
        (
            ("полнопроходн", "полный проход"),
            "Полнопроходной — проход внутри детали равен внутреннему диаметру трубы, "
            "поток не сужается. Противоположность — редуцированный (неполнопроходной), "
            "у него проход меньше и сопротивление выше.",
        ),
        (
            ("pex-a или pe-rt", "pe-rt или pex-a", "пекс-а или пе-рт", "pex или pe-rt"),
            "PEX-a и PE-RT для тёплого пола оба подходят: рабочая температура "
            "контура низкая (обычно 35–45 °C). Разница в другом. PEX-a — сшитый "
            "полиэтилен с самой высокой степенью сшивки и эффектом памяти формы: "
            "залом можно отогреть строительным феном, труба держит форму, "
            "монтируется аксиальными фитингами с надвижной гильзой. PE-RT — "
            "термостойкий полиэтилен без сшивки: заметно гибче, дешевле, проще в "
            "укладке с малым шагом, но памяти формы у него нет и запас по "
            "температуре меньше. Для длинных контуров и повышенных температур "
            "берут PEX-a, для обычного пола в жилой комнате достаточно PE-RT. "
            "В обоих случаях труба обязана иметь кислородный барьер EVOH, иначе "
            "металлические узлы системы будут корродировать.",
        ),
        (
            ("pex-a", "пекс-а", "pe-x a"),
            "PEX-a — сшитый полиэтилен пероксидным способом, самая высокая степень "
            "сшивки среди PEX. Обладает памятью формы: залом отогревают феном, и "
            "труба восстанавливается. Держит повышенные температуры и давление, "
            "монтируется аксиальными фитингами с надвижной гильзой. Для тёплого "
            "пола нужен вариант с кислородным барьером EVOH.",
        ),
        (
            ("pe-rt", "пе-рт", "перт"),
            "PE-RT — термостойкий полиэтилен без сшивки. Гибче и дешевле PEX, "
            "удобен для укладки тёплого пола с малым шагом, соединяется как "
            "сваркой, так и обжимными фитингами. Памяти формы нет, запас по "
            "температуре меньше, чем у PEX-a. Для тёплого пола нужен вариант с "
            "кислородным барьером EVOH.",
        ),
        (
            ("американк", "полусгон"),
            "Американка — разъёмное резьбовое соединение с накидной гайкой. Позволяет снять "
            "прибор или насос, не разбирая трубопровод.",
        ),
        (
            ("гребенк", "коллектор"),
            "Гребёнка (коллектор) — узел, который распределяет теплоноситель по нескольким "
            "контурам и позволяет регулировать каждый отдельно. Используется в тёплых полах "
            "и лучевой разводке.",
        ),
        (
            ("дюймовк",),
            "Дюймовка — труба или резьба размером 1 дюйм. Полдюйма — 1/2, три четверти — 3/4.",
        ),
        (
            ("группа безопасност",),
            "Группа безопасности — блок из предохранительного клапана, автоматического "
            "воздухоотводчика и манометра. Защищает закрытую систему от превышения давления "
            "и убирает воздух.",
        ),
        (
            ("гидроаккумулятор", "гидробак"),
            "Гидроаккумулятор — мембранный бак системы водоснабжения: он создаёт "
            "небольшой запас воды, сглаживает перепады давления и уменьшает число "
            "пусков насоса. Его не делят на «паровой и водяной» и не подменяют "
            "расширительным баком отопления; объём выбирают по насосу, расходу и "
            "настройкам давления.",
        ),
        (
            ("котел", "котёл"),
            "Котёл — теплогенератор системы отопления: он нагревает теплоноситель, "
            "а двухконтурная модель дополнительно готовит горячую воду. Тип и мощность "
            "выбирают по расчётным теплопотерям, доступному энергоносителю, задаче ГВС, "
            "дымоудалению и допустимой схеме подключения, а не только по площади.",
        ),
        (
            ("бкн",),
            "БКН — бойлер косвенного нагрева: накопительный бак, который обычно "
            "нагревается теплоносителем от котла через теплообменник. Его объём и "
            "мощность змеевика выбирают по расходу горячей воды и возможностям котла.",
        ),
        (
            ("эвн",),
            "ЭВН — электрический водонагреватель. Сокращение не говорит, накопительный "
            "он или проточный, поэтому для подбора ещё нужны объём либо расход, мощность "
            "и способ установки.",
        ),
        (
            ("расширительный бак", "расширительного бака"),
            "Расширительный бак компенсирует тепловое расширение теплоносителя: при нагреве "
            "объём воды растёт, и излишек уходит в бак, а не поднимает давление в системе.",
        ),
        (
            ("хвс",),
            "ХВС — холодное водоснабжение. ГВС — горячее водоснабжение.",
        ),
        (
            ("гвс",),
            "ГВС — горячее водоснабжение. ХВС, соответственно, холодное.",
        ),
        (
            ("закрытая камера", "открытая камера", "камера сгорания"),
            "Камера сгорания: открытая берёт воздух из помещения и требует дымохода с "
            "естественной тягой; закрытая забирает воздух с улицы через коаксиальную трубу "
            "и работает с принудительным отводом.",
        ),
        (
            ("межосевое",),
            "Межосевое расстояние — расстояние между центрами верхнего и нижнего "
            "подключений радиатора, обычно 350 или 500 мм. По нему подбирают замену.",
        ),
        (
            (" pn", "pn20", "pn25", "pn10"),
            "PN — номинальный класс давления при заданных стандартом условиях. "
            "Он не гарантирует ту же допустимую нагрузку при высокой температуре: "
            "для ГВС и отопления нужно проверять температурно-ресурсную диаграмму и "
            "паспорт конкретной трубы, а не выбирать только по большему числу PN.",
        ),
        (
            ("sdr",),
            "SDR — отношение наружного диаметра трубы к толщине стенки. Чем меньше SDR, тем "
            "толще стенка и выше допустимое давление.",
        ),
        (
            ("обратный клапан",),
            "Обратный клапан пропускает воду только в одну сторону и не даёт ей идти назад — "
            "например, из системы обратно в водопровод.",
        ),
    )

    def glossary_definitions(self, message: str, limit: int = 4) -> list[str]:
        """Все определения из справочника, встреченные в тексте.

        ``_glossary_definition`` возвращает одно — самое длинное совпадение.
        Этого хватает для «что такое X», но не для «чем они отличаются?» по
        вопросу вида «ВР-ВР, ВР-НР или НР-НР»: там нужно объяснить все
        предложенные варианты, а не первый из них.
        """

        text = normalize_text(message)
        found: list[tuple[int, str]] = []
        seen: set[str] = set()
        for spellings, definition in self.TERM_GLOSSARY:
            if definition in seen:
                continue
            for spelling in spellings:
                needle = normalize_text(spelling)
                if needle and needle in text:
                    found.append((text.index(needle), definition))
                    seen.add(definition)
                    break
        found.sort(key=lambda item: item[0])
        return [definition for _, definition in found[:limit]]

    def has_glossary_definition(self, message: str) -> bool:
        """Whether this turn names a term the verified glossary can define.

        A confirmed table hit is stronger evidence about what the customer wants
        than a probabilistic turn classifier, so callers may use it to answer a
        question directly instead of returning the parameter funnel.
        """

        return self._glossary_definition(message) is not None

    def _glossary_definition(self, message: str) -> str | None:
        text = normalize_text(message)
        pump_marking = self._pump_marking_definition(text)
        if pump_marking:
            return pump_marking
        best: tuple[int, str] | None = None
        for spellings, definition in self.TERM_GLOSSARY:
            for spelling in spellings:
                needle = normalize_text(spelling)
                if needle and needle in text:
                    # Более длинное совпадение точнее: «вр/вр» важнее, чем «вр/нр»,
                    # а «монтажная длина» важнее, чем «напор».
                    if best is None or len(needle) > best[0]:
                        best = (len(needle), definition)
        typed_definition = self._typed_engineering_definition(text)
        if typed_definition and (best is None or typed_definition[0] > best[0]):
            best = typed_definition
        return best[1] if best else None

    @staticmethod
    def _pump_marking_definition(text: str) -> str | None:
        """Explain common circulation-pump notation without delegating numbers to an LLM."""
        match = re.search(
            r"(?<!\d)(?P<connection>15|20|25|32|40|50)\s*[/\-]\s*"
            r"(?P<head>4|5|6|7|8|10|12)(?:0)?\s*[-/ ]\s*"
            r"(?P<length>130|180)(?!\d)",
            text,
        )
        if not match:
            return None
        # The three-part shape itself is specific enough to a common wet-rotor
        # circulation-pump size.  Customers often give only a brand and model
        # (``Wilo Star-RS 25/6-180``) and never repeat the word ``pump``.  Requiring
        # that noun sent the critical numbers to a free-form LLM, which can swap
        # connection, head and mounting length.
        connection = int(match.group("connection"))
        head = int(match.group("head"))
        length = int(match.group("length"))
        return (
            f"В распространённой маркировке циркуляционного насоса "
            f"{connection}/{head}-{length}: {connection} — номинальный размер "
            f"присоединения (обычно DN {connection}, точную резьбу проверяют отдельно); "
            f"{head} — класс максимального напора около {head} м, а не расход; "
            f"{length} — монтажная длина {length} мм между присоединительными "
            "плоскостями корпуса. Это расшифровка типоразмера, а не достаточный расчёт "
            "подбора: рабочую точку проверяют по требуемым расходу и напору на Q–H-кривой. "
            "У конкретной серии обозначение нужно сверить по карточке и паспорту производителя."
        )

    def _typed_engineering_definition(self, text: str) -> tuple[int, str] | None:
        """Resolve domain terms whose meaning depends on the named system.

        A flat substring glossary cannot safely explain these terms: ``head``
        is calculated differently for an open lift and a closed circulation
        loop, while ``circuit`` means different physical objects for a boiler
        and for underfloor heating.  Resolve that type before falling back to
        the ordinary one-definition glossary.
        """

        wants_instant_hot_water = bool(
            any(marker in text for marker in ["горяч", "гвс"])
            and any(marker in text for marker in ["дальн", "последн", "удален", "удалён"])
            and any(
                marker in text
                for marker in [
                    "сразу",
                    "без ожид",
                    "не ждать",
                    "не сливать",
                    "долго ждать",
                ]
            )
        )
        if "рециркуляц" in text or wants_instant_hot_water:
            return (
                len("рециркуляция горячей воды"),
                "Это называется рециркуляцией ГВС: горячая вода движется по подающей "
                "и обратной линии, поэтому у удалённой точки её не приходится долго "
                "сливать. Для такой схемы проверяют наличие обратной линии, источник "
                "нагрева, длину трассы, теплоизоляцию, допустимость рециркуляции для "
                "оборудования и рассчитывают небольшой циркуляционный насос; одной "
                "покупкой насоса отсутствие обратной линии не исправить.",
            )

        if "напор" in text:
            closed_circulation = any(
                marker in text
                for marker in [
                    "циркуляц",
                    "замкнут",
                    "закрыт",
                    "кольц",
                    "систем отоплен",
                    "контур отоплен",
                ]
            )
            return (
                len("напор"),
                (
                    self.CLOSED_CIRCULATION_HEAD_DEFINITION
                    if closed_circulation
                    else self.GENERAL_PUMP_HEAD_DEFINITION
                ),
            )

        if "контур" not in text:
            return None
        warm_floor = bool(
            ("тепл" in text and "пол" in text)
            or "петл" in text
            or ("коллектор" in text and "кот" not in text)
        )
        if warm_floor:
            return len("контур теплого пола"), self.WARM_FLOOR_CONTOUR_DEFINITION
        boiler = any(
            marker in text
            for marker in [
                "котел",
                "котл",
                "одноконтур",
                "двухконтур",
                "гвс",
            ]
        )
        if boiler:
            specificity = max(
                [
                    len(marker)
                    for marker in [
                        "контур котла",
                        "одноконтур",
                        "двухконтур",
                        "контур",
                    ]
                    if marker in text
                ],
                default=len("контур"),
            )
            return specificity, self.BOILER_CONTOUR_DEFINITION
        return len("контур"), self.AMBIGUOUS_CONTOUR_DEFINITION

    def compose_term_consult(self, user_message: str) -> str:
        definition = self._glossary_definition(user_message)
        if definition:
            draft = (
                definition
                + " Если нужно, подберу подходящие позиции из ассортимента — опишите задачу."
            )
            self.last_draft = draft
            return draft
        fallback = (
            "Точное значение этого термина не подскажу без проверки — не хочу вводить в "
            "заблуждение. Могу объяснить базовые понятия: монтажная длина, напор, контуры "
            "котла, типы труб и кранов. Или опишите задачу — подберу товар из ассортимента: "
            "трубы, насосы, котлы, краны, канализация, радиаторная арматура."
        )
        # An unknown engineering word is not a safe place for model improvisation.
        # The LLM still interprets intent and polishes low-risk dialogue, while
        # definitions and numeric notation come only from the verified glossary.
        self.last_draft = fallback
        return fallback

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
        candidate_count: int | None = None,
    ) -> str:
        unverified_pump_duty = bool(
            query
            and query.category == "pumps"
            and query.slots.get("required_flow_m3_h") is not None
            and query.slots.get("required_head_m") is not None
        )
        if unverified_pump_duty:
            draft = (
                "Не могу корректно рекомендовать один насос только по его максимальной "
                "подаче и максимальному напору: эти значения не достигаются одновременно. "
                f"Ближайший кандидат для проверки по Q–H-кривой: {card.sku} — {card.name}, "
                f"{card.price:g} {card.currency}, наличие: {card.stock_status}. "
                "Подтвердить модель можно лишь если её кривая проходит через требуемую "
                f"рабочую точку. Карточка: {card.url}"
            )
            self.last_draft = draft
            return draft

        reasons: list[str] = []
        if query and query.slots:
            slot_reasons = self._requested_summary(query)
            if slot_reasons:
                reasons.append(f"учитывает заданные параметры: {slot_reasons}")
        verified = self._verified_choice_characteristics(card, query)
        if verified:
            reasons.append("по карточке: " + "; ".join(verified))
        if query and (
            query.cheap
            or query.slots.get("max_price") is not None
            or query.slots.get("min_price") is not None
        ):
            reasons.append(f"цена {card.price:g} {card.currency} соответствует ценовому условию")
        if query and query.slots.get("in_stock") and card.stock_status:
            reasons.append(f"наличие: {card.stock_status}")
        reason_text = "; ".join(reasons) or "это ближайшее подтверждённое совпадение в текущей подборке"
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
        if candidate_count == 1:
            choice_context = (
                "Из найденной подборки сейчас остался один ближайший вариант"
                if sizing_warning
                else "Из найденной подборки сейчас один вариант, прошедший заданные фильтры"
            )
        elif candidate_count and candidate_count > 1:
            choice_context = f"Сравнил {candidate_count} найденных варианта"
        else:
            choice_context = (
                "Это ближайший к вашим параметрам вариант"
                if sizing_warning
                else "Выбираю по подтверждённым параметрам карточки"
            )
        first_line = (
            f"{choice_context}. Рекомендую: {card.sku} — {card.name}. "
            f"Цена {card.price:g} {card.currency}, наличие: {card.stock_status}."
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

    @staticmethod
    def _verified_choice_characteristics(
        card: ProductCard,
        query: SearchQuery | None,
    ) -> list[str]:
        """Return category-relevant facts already grounded in the product card.

        A sales recommendation must explain the engineering fit, not merely say
        that the row is cheap or in stock.  This helper never invents a reason:
        it can only render fields emitted by ``ProductCardAgent``.
        """

        category = query.category if query else "other"
        priorities: dict[str, tuple[str, ...]] = {
            "boilers": (
                "тип котла",
                "количество контуров",
                "мощност",
                "диапазон мощности",
                "камера",
            ),
            "pumps": (
                "тип насоса",
                "напор",
                "производительност",
                "расход",
                "монтажная длина",
                "присоедин",
            ),
            "pipes": (
                "назначение",
                "материал",
                "армир",
                "диаметр",
                "температур",
                "давлен",
            ),
            "sewer": ("тип товара", "назначение", "диаметр", "длина"),
            "valves": (
                "назначение",
                "диаметр",
                "размер",
                "тип резьбы",
                "тип присоединения",
                "давлен",
                "температур",
            ),
            "radiator_fittings": (
                "назначение",
                "диаметр",
                "резьб",
                "тип присоединения",
            ),
            "water_heaters": (
                "тип водонагревателя",
                "объем",
                "мощност",
                "способ нагрева",
                "монтаж",
            ),
            "radiators": ("тип радиатора", "межосев", "секц", "мощност"),
        }
        markers = priorities.get(category, ())
        if not markers:
            return []
        items = list(card.characteristics.items())
        chosen: list[str] = []
        used_keys: set[str] = set()
        for marker in markers:
            for key, value in items:
                normalized_key = normalize_text(str(key))
                if normalized_key in used_keys or marker not in normalized_key:
                    continue
                chosen.append(f"{key}: {value}")
                used_keys.add(normalized_key)
                break
            if len(chosen) >= 4:
                break
        return chosen

    def _choose_one_caveat(
        self,
        query: SearchQuery | None,
        card: ProductCard | None = None,
    ) -> str:
        category = query.category if query else "other"
        if category == "boilers":
            if query and card and self._boiler_sizing_warning([card], query):
                caveat = (
                    "мощность заметно выше ориентировочного диапазона для указанной площади — "
                    "до покупки нужен расчёт теплопотерь и проверка минимальной мощности/модуляции."
                )
                if query.slots.get("contours") == "одноконтурный":
                    caveat += (
                        " Если нужна горячая вода от котла, потребуется отдельный бойлер "
                        "либо двухконтурная модель."
                    )
                return caveat
            if query and query.slots.get("contours") == "одноконтурный":
                return (
                    "если нужна горячая вода от котла, потребуется отдельный бойлер либо "
                    "двухконтурная модель; мощность всё равно подтверждают расчётом теплопотерь."
                )
            return (
                "если расчётные теплопотери, требование по ГВС или доступное подключение "
                "отличаются — тип и мощность нужно пересчитать до покупки."
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
            return (
                "если отличаются участок системы, материал, диаметр, рабочие температура/давление "
                "или способ прокладки — нужна другая позиция."
            )
        if category in {"valves", "radiator_fittings"}:
            return (
                "если не совпадают среда, размер, тип резьбы или допустимые температура/давление — "
                "соединение нельзя считать совместимым."
            )
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
            if len(
                {
                    self._canonical_comparison_value(card, key)
                    for card in cards
                }
            )
            > 1
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

    @staticmethod
    def _canonical_comparison_value(card: ProductCard, key: str) -> str:
        """Compare semantics, not feed spelling variants."""

        value = card.characteristics.get(key)
        key_text = normalize_text(str(key))
        if "резьб" in key_text:
            pair = normalize_thread_pair(f"{card.name} {value or ''}")
            if pair is not None:
                return pair
        return normalize_text(str(value or ""))

    def compose_no_match(self, query: SearchQuery) -> str:
        slots = query.slots
        industrial_valve = bool(
            query.category == "valves"
            and (
                slots.get("nominal_diameter_dn") is not None
                or slots.get("operating_temperature_c") is not None
                or slots.get("operating_pressure_bar") is not None
                or "пар" in normalize_text(str(slots.get("application") or ""))
            )
        )
        if industrial_valve:
            details: list[str] = []
            dn = slots.get("nominal_diameter_dn") or slots.get("diameter_mm")
            if dn is not None:
                details.append(f"DN {float(dn):g}")
            if slots.get("application"):
                details.append(f"среда: {slots['application']}")
            if slots.get("operating_temperature_c") is not None:
                details.append(
                    f"температура до {float(slots['operating_temperature_c']):g} °C"
                )
            if slots.get("operating_pressure_bar") is not None:
                details.append(
                    f"давление {float(slots['operating_pressure_bar']):g} бар"
                )
            requested = ", ".join(details) or "заданные промышленные параметры"
            draft = (
                "В текущем каталоге не вижу промышленного вентиля или другой "
                f"запорной арматуры, подтверждённой под условия: {requested}. "
                "Трубу или бытовой водяной кран вместо неё не предлагаю. Можно "
                "подготовить эти параметры для проверки менеджером."
            )
        elif query.category == "valves" and slots.get("diameter_mm"):
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
        if any(
            marker in question
            for marker in [
                "Для дренажного насоса уточните:",
                "КНС/санитарный насос нельзя выбирать",
                "Чтобы я рассчитал расчётный напор",
            ]
        ):
            # These questions encode the minimum hydraulic safety gate.  A style
            # rewrite must not replace particle size/lift/route with the motor
            # power, nor imply that a pump is selected before its Q-H check.
            self.last_draft = draft
            return draft
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
        elif query.category == "pumps" and (
            query.slots.get("required_flow_m3_h") is not None
            or query.slots.get("required_head_m") is not None
        ):
            lines.append(
                "Нашёл только предварительных кандидатов по предельным параметрам; "
                "это не подтверждённый подбор по рабочей точке Q–H:"
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
                # Параметры, которые покупатель задал сам, обрезать нельзя:
                # иначе подтвердить требование по карточке невозможно.
                # ProductCardAgent ставит их первыми, поэтому достаточно
                # расширить срез до их количества.
                # Запрошенные поля добавляются к обычным, а не вытесняют их:
                # иначе «тип ручки» исчезал из карточки ровно тогда, когда
                # покупатель уточнял резьбу и размер.
                characteristic_limit = min(
                    6,
                    max(
                        characteristic_limit,
                        len(
                            constrained_characteristic_keys(
                                card.characteristics, query.slots
                            )
                        )
                        + 1,
                    ),
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
