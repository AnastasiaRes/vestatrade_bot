"""Strict, side-effect-free understanding of one customer turn.

This module is intentionally independent from routing, dialogue state updates,
catalogue search and response generation.  During the shadow rollout its output
is recorded for evaluation only and can never alter the customer-facing path.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from copy import deepcopy
from difflib import SequenceMatcher
from enum import Enum
from time import monotonic
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.catalog_v2.normalization import parse_pump_designation
from app.catalog_v2.registry import DEFAULT_CONTRACTS
from app.models import SessionState
from app.openrouter_client import OpenRouterClient
from app.pii import redact_pii_for_model

from .domain_ontology import (
    RANGE_CAPABLE_CONSTRAINT_FACTS,
    semantic_ontology_payload,
)


SEMANTIC_PROMPT_VERSION = "turn-understanding-v1.18"
SEMANTIC_INTERPRETER_PROMPT = """
Ты — семантический интерпретатор одного нового сообщения покупателя магазина
инженерной сантехники. Верни только JSON по переданной схеме.

Твоя задача — описать смысл НОВОЙ реплики, а не отвечать покупателю:
- выдели все действия покупателя, даже если их несколько;
- отличай явно запрошенный товар (target) от товара/системы, которые лишь
  задают контекст (context), уже установлены (existing) или нужны как аксессуар;
- явно названную старую модель/тип, для которой просят замену или аналог,
  кодируй как alternative, а не только context/existing: её класс задаёт класс
  искомой замены;
- различай новую цель, продолжение, уточнение, исправление, смену цели и возврат;
- сохраняй отрицания, предпочтения, неизвестные и отложенные параметры;
- бытовые названия можно канонизировать, но исходный фрагмент всегда сохрани
  в evidence;
- технические модификаторы внутри названия товара (тип, исполнение, размер,
  мощность, длина, присоединение и другие явно названные характеристики)
  также вынеси в отдельные constraints; не оставляй их только внутри product;
- каждый прямой вопрос отрази подходящим act, даже когда в той же реплике есть
  подбор или уточнение товара;
- явные информационные запросы дополнительно сохрани в information_requests:
  что именно требуется узнать, зачем это нужно покупателю и нужен ли ему текст,
  инструкция либо проверенная ссылка. Не создавай information_request, если
  предмет или требуемый результат неоднозначны;
- вопрос о характеристике текущего или уже показанного товара — explain и
  продолжение текущей цели; это не новый подбор и не unknown-ограничение;
- явное разрешение ослабить ранее заданное условие сохрани как refine/correct
  и preferred-ограничение, а не как новое обязательное required-условие;
- короткий ответ можно связать с pending_question из контекста, однако evidence
  всё равно должен быть дословным фрагментом НОВОЙ реплики;
- authoritative_dialogue_state_v2 содержит подтверждённые типизированные цели,
  задачи и факты. Он сильнее прежнего текста бота, recent_dialogue и legacy
  pending_question. Явное исправление в current_message сильнее и этого state;
- last_committed_presentation содержит только карточки, действительно показанные
  покупателю в последнем подтверждённом V2-ответе. Используй этот типизированный
  контекст для ссылок на уже показанные модели; отсутствие этого поля не даёт
  права считать shadow-кандидатов показанными;
- опечатки, разговорная речь и транслитерация не меняют эти правила.

Строгие ограничения:
- не составляй ответ покупателю и не добавляй поле reply;
- не выбирай SKU, товары или аналоги;
- не вычисляй и не конвертируй значения;
- не копируй параметры из истории в constraints;
- не додумывай отсутствующие значения;
- каждый product, constraint, reference и ambiguity должен иметь непустой
  evidence, дословно встречающийся в current_message;
- если current_message написан транслитом, evidence тоже копируй транслитом
  из current_message: не переводи его обратно в кириллицу;
- value у constraint — только явно сказанное значение. Если покупатель говорит,
  что не знает параметр, status=unknown и value=null;
- явный числовой диапазон сохраняй одним constraint того же факта: не выбирай
  одну границу и не создавай два конфликтующих значения. Скопируй диапазон в
  value строкой из сообщения, а общую единицу сохрани в unit;
- единицу явно указанного количества сохраняй: метры товара имеют unit=m;
  значение не вычисляй и не конвертируй;
- единица числового constraint должна относиться к тому же физическому
  семейству, что и стабильное имя факта. Не смешивай напор/длину с давлением:
  bar, kPa и другие единицы давления не являются метрами напора. Не
  конвертируй и не переименовывай факт, чтобы скрыть несовместимость;
- applies_to_product — индекс элемента products или null.

Как кодировать information_requests:
- fact_name — стабильное имя явно запрошенной характеристики либо null, если
  вопрос относится ко всему товару или документу;
- purpose: value — запрос значения; meaning — смысла термина;
  decision_relevance — почему факт влияет на выбор; determination_method — как
  его определить; compatibility — проверка совместимости; provenance — запрос
  подтверждающего источника;
- requested_outputs содержит один или несколько результатов: explanation,
  instruction, verified_link. output_relation=all, если нужны все результаты,
  и any, если достаточно любого из них;
- source_kind заполняй для verified_link: catalog_product_page,
  manufacturer_documentation, technical_documentation,
  official_business_site либо any_verified. Без явного вида источника выбирай
  any_verified; не выдумывай URL;
- act допускает только explain, get_link или calculate и обязательно должен
  также присутствовать в acts;
- applies_to_product связывает запрос с явно упомянутым products; для разных
  товаров создавай отдельные information_requests;
- subject_scope=presented_candidates, когда покупатель спрашивает значение
  характеристики именно у уже показанных моделей/карточек или
  просит их проверить по карточкам; subject_scope=customer_goal, когда
  спрашивает значение параметра своей задачи/системы; не копируй
  значения из прежних карточек в constraints;
- purpose=provenance требует requested_outputs с verified_link; verified_link
  всегда требует source_kind;
- evidence — точный фрагмент только current_message.

Как кодировать действия (не схлопывай несколько действий в одно):
- find — показать/найти варианты без просьбы решить, какой подходит;
- select — подобрать или рекомендовать подходящий вариант по условиям;
- compare — сопоставить варианты;
- explain — объяснить свойство или правило;
- calculate — посчитать результат по исходным данным;
- check_price, check_stock и get_link — отдельные действия, если покупатель
  одновременно просит цену, наличие или ссылку;
- разовый вопрос «есть ли товар в наличии?» кодируй как check_stock без
  constraint: это запрос сведений, а не разрешение скрыть отсутствующий товар;
- устойчивое условие выбора «только/именно из наличия» кодируй одновременно
  как find/select, check_stock и constraint stock_availability=true,
  status=known, polarity=required, привязанный к целевому товару;
- явное снятие этого условия («наличие неважно») кодируй как operation=refine,
  constraint stock_availability=true, status=known, polarity=excluded; не
  добавляй check_stock, если отдельного вопроса о наличии нет;
- разрешение включить товары без подтверждённого наличия, включая товары,
  которые сейчас отсутствуют, также снимает прежний фильтр: кодируй его как
  operation=refine, constraint stock_availability=true, status=known,
  polarity=excluded и не добавляй check_stock без отдельного вопроса;
- check_delivery используй только при явном вопросе или утверждении о доставке,
  логистике, перевозке, отгрузке, складе/пункте выдачи либо о сроке, стоимости
  или адресе именно доставки. Само слово «проверь» без логистического предмета
  не означает доставку; проверка технической характеристики — explain;
- остальные действия выбирай строго по их именам в JSON-схеме;
- просьба сначала подобрать товар, а затем выполнить коммерческую операцию —
  это два самостоятельных acts. Не теряй коммерческое действие из-за того,
  что в той же реплике есть товар или подбор;
- request_quote — оценка стоимости или коммерческое предложение, когда
  покупатель не просит платёжный документ; request_invoice — именно счёт как
  отдельный платёжный/бухгалтерский документ, не request_quote;
  reserve_product — любая просьба временно удержать или отложить товар за
  покупателем; place_order — оформление
  заказа; modify_order — изменение существующего заказа; cancel_order — его
  отмена; order_status — проверка состояния заказа; check_delivery — условия
  или стоимость доставки; return_product — возврат; warranty — гарантийное
  обращение; complaint — претензия; handoff — явная просьба передать обращение
  сотруднику;
- contact_store — вопрос, нужно ли, можно ли или как связаться, позвонить либо
  написать в магазин, офис, филиал, менеджеру или другому сотруднику для
  проверки/консультации. Кодируй contact_store и тогда, когда это сформулировано
  как вопрос («мне позвонить в офис?»), а не как приказ. Не заменяй его explain;
- handoff отличается от contact_store: handoff означает просьбу именно этому
  ассистенту передать текущий чат/обращение сотруднику, а contact_store — запрос
  проверяемого человеческого канала или совет обратиться туда самостоятельно.

Как кодировать управление commerce workflow:
- workflow_controls содержит только явно сказанное confirm, decline,
  withdraw_consent, opt_out или resume_after_opt_out;
- короткое «да» является confirm только когда оно отвечает на pending-вопрос о
  согласии; не выбирай workflow и не утверждай, что операция выполнена;
- явное подтверждение, отклонение или отзыв ранее подготовленной операции — это
  workflow_control, а не новая просьба выполнить ту же предметную операцию.
  Не повторяй соответствующий commerce act только из-за упоминания его объекта
  внутри подтверждения; новый act добавляй лишь для отдельной новой просьбы;
- каждый control сохраняет дословное evidence из current_message.

Взаимоисключающие инварианты перед возвратом JSON:
- прямой запрос выставить/подготовить счёт или invoice как документ всегда
  требует act=request_invoice. Не заменяй его request_quote; оба act допустимы
  только если покупатель отдельно просит и счёт, и коммерческое предложение;
- confirm, decline, withdraw_consent, opt_out и resume_after_opt_out никогда не
  допустимы внутри acts — только внутри workflow_controls;
- handoff, request_invoice и остальные предметные действия никогда не
  допустимы внутри workflow_controls. Явная новая просьба передать обращение
  сотруднику — act=handoff;
- operation описывает только связь новой реплики с задачей: new, continue,
  refine, correct, switch, return, cancel или unknown. Предметные действия
  вроде modify_order, cancel_order и return_product никогда не допустимы в
  operation — они находятся в acts;
- если новая реплика только подтверждает или отклоняет ожидающую операцию,
  acts может быть пустым. До ещё не данного согласия отрицательный ответ —
  decline; withdraw_consent относится к отзыву уже ранее данного согласия.

Как кодировать ограничения:
- name — стабильное имя характеристики, а не вся бытовая фраза;
- известное число/строка/булево значение: status=known и value содержит его;
- требование отсутствия функции: polarity=excluded, status=known, value=true;
- качественный признак («настенный», «для горячей воды») — это известное
  строковое или булево value, а не null;
- value=null допустимо только при unknown/refused/deferred;
- unknown, refused и deferred допустимы только когда покупатель явно сказал,
  что не знает параметр, отказался его сообщать или отложил уточнение. Молчание
  и отсутствие параметра в реплике не создают такой constraint;
- preferred означает пожелание, excluded — запрет, required — обязательное
  условие. Polarity не заменяет value.

Допустимые категории:
pumps, pipes, boilers, water_heaters, hydraulic_accumulators, filters, controls,
valves, sewer, radiator_fittings, radiators, fittings, meters, sanitary_ware,
installation_systems, other.

Верни объект с schema_version="1.2", language, operation, acts, products,
constraints, references, ambiguities, workflow_controls, information_requests,
answers_pending_question и confidence.
Не добавляй никаких других полей.
""".strip()

SEMANTIC_PROMPT_HASH = hashlib.sha256(
    SEMANTIC_INTERPRETER_PROMPT.encode("utf-8")
).hexdigest()

SEMANTIC_AUDIT_PROMPT = """
Ты — второй, независимый проход контроля семантического разбора сообщения
покупателя. Получишь current_message, контекст до хода, ontology, JSON-схему и
candidate от первого прохода. Верни исправленный полный TurnUnderstanding по
той же JSON-схеме, без пояснений и дополнительных полей.

Проверь смысл, а не отдельные ключевые слова:
1. Каждая самостоятельная просьба отражена отдельным acts: подбор подходящего
   товара — select, простой поиск/показ — find, цена/наличие/ссылка — отдельные
   check_price/check_stock/get_link. Отдельно проверь все commerce-просьбы:
   request_quote, request_invoice, reserve_product, place_order, modify_order,
   cancel_order, order_status, check_delivery, return_product, warranty,
   complaint и handoff. Товарный подбор и commerce-просьбу в одной реплике не
   схлопывай. Не смешивай request_invoice с request_quote: платёжный или
   бухгалтерский счёт — request_invoice, оценка/предложение без счёта —
   request_quote. Просьба временно удержать товар — reserve_product, даже если
   покупатель использует разговорный глагол.
   Разовый вопрос о наличии — check_stock без constraint. Условие выбрать
   только доступный сейчас товар требует check_stock рядом с find/select и
   product-scoped constraint stock_availability=true, known, required. Явное
   «наличие неважно» снимает фильтр через stock_availability=true, known,
   excluded и не создаёт check_stock без отдельного вопроса. Разрешение
   показать товары без подтверждённого наличия или даже отсутствующие означает
   ту же typed-релаксацию stock_availability=true, known, excluded.
2. Главный запрошенный товар имеет role=target. Уже установленный — existing;
   объект системы, который только задаёт условия, — context. Контекст не может
   заменить цель. Старую модель/тип в явной просьбе подобрать замену или аналог
   сохрани как alternative, чтобы не потерять класс искомого товара.
3. Все явные ограничения, предпочтения, запреты, неизвестные или отложенные
   параметры отражены в constraints с правильными status, polarity и value.
   Технические модификаторы, входящие прямо в название товара, не теряй: они
   тоже становятся отдельными constraints. Явное разрешение ослабить прежнее
   требование кодируй как preferred и refine/correct, не как новое required.
4. Исправление, смена и возврат отражены в operation; короткий ответ правильно
   связан с pending_question, если он есть. Подтверждённый
   authoritative_dialogue_state_v2 сильнее старого текста бота и legacy
   pending_question; явное текущее исправление покупателя сильнее state.
5. Никаких вычислений, ответов, SKU и фактов из истории. Evidence каждого
   элемента — непустая дословная часть current_message. Для транслита копируй
   точный латинский фрагмент, не создавай кириллический перевод evidence.
6. Каждый прямой вопрос отражён подходящим act и не потерян из-за товарного
   подбора или уточнения параметров. Проверка, подтверждение или объяснение
   доставки — это check_delivery, не explain/other, но только при явном
   логистическом предмете. Общее «проверь» о техническом параметре — explain.
   Вопрос о характеристике текущего/показанного товара продолжает его typed
   goal/task и не создаёт unknown из самого факта вопроса.
7. Явное согласие, отказ, отзыв согласия, opt-out или возобновление ранее
   подготовленного workflow отражено в workflow_controls. Само упоминание
   предмета подтверждаемой операции не создаёт повторный commerce act. Новый
   act нужен только для самостоятельной новой просьбы.
8. Выполни финальную взаимоисключающую проверку: запрос счёта как документа —
   request_invoice, не request_quote; control-kind никогда не находится в
   acts; предметный act, включая handoff, никогда не находится в
   workflow_controls; предметный act никогда не находится в operation; чистый
   ответ на consent может иметь acts=[]. До выдачи согласия «нет» означает
   decline, а не withdraw_consent.
9. Не создавай unknown/refused/deferred из простого отсутствия параметра. Эти
   статусы допустимы только при явном незнании, отказе или откладывании в
   current_message.
10. Для каждого числового constraint проверь физическое семейство единицы:
    длина/напор, давление, расход, мощность, температура и другие семейства не
    взаимозаменяемы. В частности, bar/kPa не являются метрами напора. Не
    конвертируй значение и не переименовывай факт ради совпадения.
11. Каждый явный запрос значения, объяснения, важности параметра, способа его
    определения, совместимости или подтверждающего источника отрази в
    information_requests. Не смешивай запросы по разным products. Проверь, что
    act запроса есть в acts, evidence дословно взят из current_message,
    verified_link имеет source_kind, а provenance требует verified_link.
    Если спрашивают факт именно у уже показанных моделей/карточек,
    ставь subject_scope=presented_candidates; запрос факта о системе или задаче
    покупателя остаётся customer_goal. Факты карточек не копируй в constraints.

Если candidate уже точен и полон, верни его без смысловых изменений.
""".strip()
SEMANTIC_AUDIT_PROMPT_HASH = hashlib.sha256(
    SEMANTIC_AUDIT_PROMPT.encode("utf-8")
).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GoalOperation(str, Enum):
    NEW = "new"
    CONTINUE = "continue"
    REFINE = "refine"
    CORRECT = "correct"
    SWITCH = "switch"
    RETURN = "return"
    CANCEL = "cancel"
    UNKNOWN = "unknown"


class CustomerAct(str, Enum):
    FIND = "find"
    SELECT = "select"
    COMPARE = "compare"
    EXPLAIN = "explain"
    CALCULATE = "calculate"
    CHECK_PRICE = "check_price"
    CHECK_STOCK = "check_stock"
    GET_LINK = "get_link"
    REQUEST_QUOTE = "request_quote"
    REQUEST_INVOICE = "request_invoice"
    RESERVE_PRODUCT = "reserve_product"
    PLACE_ORDER = "place_order"
    MODIFY_ORDER = "modify_order"
    CANCEL_ORDER = "cancel_order"
    ORDER_STATUS = "order_status"
    CHECK_DELIVERY = "check_delivery"
    RETURN_PRODUCT = "return_product"
    WARRANTY = "warranty"
    COMPLAINT = "complaint"
    CONTACT_STORE = "contact_store"
    HANDOFF = "handoff"
    GREETING = "greeting"
    GRATITUDE = "gratitude"
    OTHER = "other"


class ProductRole(str, Enum):
    TARGET = "target"
    CONTEXT = "context"
    EXISTING = "existing"
    ACCESSORY = "accessory"
    ALTERNATIVE = "alternative"
    UNKNOWN = "unknown"


class ProductCategory(str, Enum):
    PUMPS = "pumps"
    PIPES = "pipes"
    BOILERS = "boilers"
    WATER_HEATERS = "water_heaters"
    HYDRAULIC_ACCUMULATORS = "hydraulic_accumulators"
    FILTERS = "filters"
    CONTROLS = "controls"
    VALVES = "valves"
    SEWER = "sewer"
    RADIATOR_FITTINGS = "radiator_fittings"
    RADIATORS = "radiators"
    FITTINGS = "fittings"
    METERS = "meters"
    SANITARY_WARE = "sanitary_ware"
    INSTALLATION_SYSTEMS = "installation_systems"
    OTHER = "other"


class ConstraintStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    REFUSED = "refused"
    DEFERRED = "deferred"


class ConstraintPolarity(str, Enum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    EXCLUDED = "excluded"


class ReferenceKind(str, Enum):
    PREVIOUS_PRODUCT = "previous_product"
    PREVIOUS_CATEGORY = "previous_category"
    ORDINAL = "ordinal"
    DEICTIC = "deictic"
    PENDING_QUESTION = "pending_question"
    OTHER = "other"


class WorkflowControlKind(str, Enum):
    CONFIRM = "confirm"
    DECLINE = "decline"
    WITHDRAW_CONSENT = "withdraw_consent"
    OPT_OUT = "opt_out"
    RESUME_AFTER_OPT_OUT = "resume_after_opt_out"


class InformationPurpose(str, Enum):
    VALUE = "value"
    MEANING = "meaning"
    DECISION_RELEVANCE = "decision_relevance"
    DETERMINATION_METHOD = "determination_method"
    COMPATIBILITY = "compatibility"
    PROVENANCE = "provenance"


class RequestedInformationOutput(str, Enum):
    EXPLANATION = "explanation"
    INSTRUCTION = "instruction"
    VERIFIED_LINK = "verified_link"


class InformationOutputRelation(str, Enum):
    ALL = "all"
    ANY = "any"


class InformationSourceKind(str, Enum):
    CATALOG_PRODUCT_PAGE = "catalog_product_page"
    MANUFACTURER_DOCUMENTATION = "manufacturer_documentation"
    TECHNICAL_DOCUMENTATION = "technical_documentation"
    OFFICIAL_BUSINESS_SITE = "official_business_site"
    ANY_VERIFIED = "any_verified"


class InformationRequestAct(str, Enum):
    EXPLAIN = "explain"
    GET_LINK = "get_link"
    CALCULATE = "calculate"


class InformationSubjectScope(str, Enum):
    CUSTOMER_GOAL = "customer_goal"
    PRESENTED_CANDIDATES = "presented_candidates"


class ProductMention(StrictModel):
    text: str = Field(min_length=1, max_length=240)
    canonical_type: str | None = Field(default=None, max_length=120)
    category: ProductCategory = ProductCategory.OTHER
    role: ProductRole = Field(
        description=(
            "target for the primary requested product; existing only when it is "
            "already installed/owned; context when it merely constrains a target."
        )
    )
    evidence: str = Field(min_length=1, max_length=240)


class ConstraintFact(StrictModel):
    name: str = Field(
        min_length=1,
        max_length=120,
        description="Stable attribute name, not the complete source phrase.",
    )
    value: str | int | float | bool | None = Field(
        default=None,
        description=(
            "Explicit numeric, text or boolean value; null only when status is "
            "unknown, refused or deferred."
        ),
    )
    unit: str | None = Field(default=None, max_length=40)
    status: ConstraintStatus = ConstraintStatus.KNOWN
    polarity: ConstraintPolarity = ConstraintPolarity.REQUIRED
    applies_to_product: int | None = Field(default=None, ge=0)
    evidence: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def unknown_values_are_empty(self) -> "ConstraintFact":
        if self.status in {
            ConstraintStatus.UNKNOWN,
            ConstraintStatus.REFUSED,
            ConstraintStatus.DEFERRED,
        } and self.value is not None:
            raise ValueError("unknown/refused/deferred constraint must have null value")
        if self.status == ConstraintStatus.KNOWN and self.value is None:
            raise ValueError("known constraint must have a value")
        return self


class TurnReference(StrictModel):
    kind: ReferenceKind
    text: str = Field(min_length=1, max_length=240)
    target_hint: str | None = Field(default=None, max_length=160)
    evidence: str = Field(min_length=1, max_length=240)


class TurnAmbiguity(StrictModel):
    kind: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=400)
    evidence: str = Field(min_length=1, max_length=240)


class WorkflowControl(StrictModel):
    kind: WorkflowControlKind
    evidence: str = Field(min_length=1, max_length=240)


class InformationRequest(StrictModel):
    """Explicit information the customer asks the assistant to provide."""

    fact_name: str | None = Field(default=None, min_length=1, max_length=120)
    purpose: InformationPurpose
    requested_outputs: list[RequestedInformationOutput] = Field(
        min_length=1,
        max_length=3,
    )
    output_relation: InformationOutputRelation = InformationOutputRelation.ALL
    source_kind: InformationSourceKind | None = None
    act: InformationRequestAct
    subject_scope: InformationSubjectScope = InformationSubjectScope.CUSTOMER_GOAL
    applies_to_product: int | None = Field(default=None, ge=0)
    evidence: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def verified_sources_are_explicit(self) -> "InformationRequest":
        outputs = set(self.requested_outputs)
        if len(outputs) != len(self.requested_outputs):
            raise ValueError("requested_outputs must not contain duplicates")
        if (
            RequestedInformationOutput.VERIFIED_LINK in outputs
            and self.source_kind is None
        ):
            raise ValueError("verified_link requires source_kind")
        if (
            self.purpose == InformationPurpose.PROVENANCE
            and RequestedInformationOutput.VERIFIED_LINK not in outputs
        ):
            raise ValueError("provenance requires verified_link")
        return self


class TurnUnderstanding(StrictModel):
    """Grounded semantics of the current message; never an execution plan."""

    schema_version: Literal["1.0", "1.1", "1.2"] = "1.2"
    language: str = Field(default="ru", min_length=2, max_length=16)
    operation: GoalOperation = Field(
        default=GoalOperation.UNKNOWN,
        description=(
            "Dialogue relation only (new/continue/refine/correct/switch/return/"
            "cancel/unknown), never a domain action such as modify_order."
        ),
    )
    acts: list[CustomerAct] = Field(
        default_factory=list,
        max_length=12,
        description=(
            "Every independent customer action; do not collapse selection, "
            "price, stock, link or comparison requests into one action. "
            "request_invoice is a requested invoice/payment document and is "
            "not interchangeable with request_quote. Workflow control kinds "
            "never belong in acts. check_delivery requires explicit logistics "
            "or delivery scope; checking a technical characteristic is explain."
        ),
    )
    products: list[ProductMention] = Field(default_factory=list, max_length=12)
    constraints: list[ConstraintFact] = Field(default_factory=list, max_length=40)
    references: list[TurnReference] = Field(default_factory=list, max_length=12)
    ambiguities: list[TurnAmbiguity] = Field(default_factory=list, max_length=12)
    workflow_controls: list[WorkflowControl] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Explicit control of an already prepared workflow. A control-only "
            "turn may have no acts; decline precedes consent, while "
            "withdraw_consent revokes consent already granted."
        ),
    )
    information_requests: list[InformationRequest] = Field(
        default_factory=list,
        max_length=12,
        description=(
            "Explicit requests for a fact value, explanation, determination "
            "method, compatibility check or verified provenance."
        ),
    )
    answers_pending_question: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def product_indexes_exist(self) -> "TurnUnderstanding":
        for constraint in self.constraints:
            if (
                constraint.applies_to_product is not None
                and constraint.applies_to_product >= len(self.products)
            ):
                raise ValueError("constraint points to a missing product mention")
        for request in self.information_requests:
            if (
                request.applies_to_product is not None
                and request.applies_to_product >= len(self.products)
            ):
                raise ValueError(
                    "information request points to a missing product mention"
                )
            if CustomerAct(request.act.value) not in self.acts:
                raise ValueError(
                    "information request act is absent from turn acts"
                )
        return self


class SemanticInterpretationResult(StrictModel):
    status: Literal["accepted", "rejected", "skipped"]
    requested: bool = False
    transport_succeeded: bool = False
    output_accepted: bool = False
    model: str | None = None
    latency_ms: int = Field(default=0, ge=0)
    prompt_version: str = SEMANTIC_PROMPT_VERSION
    prompt_hash: str = SEMANTIC_PROMPT_HASH
    audit_prompt_hash: str = SEMANTIC_AUDIT_PROMPT_HASH
    audit_requested: bool = False
    audit_output_accepted: bool = False
    audit_rejection_reason: str | None = None
    structural_repairs: tuple[str, ...] = ()
    understanding: TurnUnderstanding | None = None
    rejection_reason: str | None = None
    fallback_reason: str | None = None


def _normalize_evidence(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


_SOURCE_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_NUMERIC_LITERAL_RE = re.compile(r"(?<![\w])\d+(?:[.,]\d+)?(?![\w])")
_NUMERIC_STRING_SCALAR_RE = re.compile(
    r"^\s*(?P<value>[-+]?\d+(?:[.,]\d+)?)\s*(?P<unit>.*?)\s*$",
    flags=re.IGNORECASE,
)
_NUMERIC_STRING_RANGE_RE = re.compile(
    r"^\s*(?P<minimum>[-+]?\d+(?:[.,]\d+)?)\s*"
    r"(?:\.\.|[-\u2013\u2014]|до|to)\s*"
    r"(?P<maximum>[-+]?\d+(?:[.,]\d+)?)\s*(?P<unit>.*?)\s*$",
    flags=re.IGNORECASE,
)
_EVIDENCE_NUMERIC_RANGE_RE = re.compile(
    r"(?<![\w])(?P<minimum>[-+]?\d+(?:[.,]\d+)?)\s*"
    r"(?:\.\.|[-\u2013\u2014]|до|to)\s*"
    r"(?P<maximum>[-+]?\d+(?:[.,]\d+)?)(?![\w])",
    flags=re.IGNORECASE,
)

# Stable numeric fact names encode their physical dimension.  This table is
# intentionally about dimensions and canonical naming conventions, not about
# catalogue products or customer wording.  More-specific suffixes precede the
# generic length suffixes.
_NUMERIC_FACT_UNIT_FAMILIES: tuple[tuple[str, str], ...] = (
    ("_pressure_bar", "pressure"),
    ("pressure_bar", "pressure"),
    ("_flow_m3_h", "flow"),
    ("_flow_l_min", "flow"),
    ("_flow_l_h", "flow"),
    ("_temperature_c", "temperature"),
    ("_power_kw", "power"),
    ("_power_w", "power"),
    ("_area_m2", "area"),
    ("_volume_l", "volume"),
    ("_concentration_percent", "ratio"),
    ("_percent", "ratio"),
    ("_angle_deg", "angle"),
    ("_voltage_v", "voltage"),
    ("_count", "count"),
    ("_rating_um", "length"),
    ("_um", "length"),
    ("_mm", "length"),
    ("_cm", "length"),
    ("_m", "length"),
    ("_rub", "money"),
)

# General unit syntax used only to identify a physical family.  No conversion
# factor lives here: the semantic layer keeps the exact value and unit stated
# by the customer, or drops an incompatible model proposal.
_EXPLICIT_UNIT_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "flow",
        re.compile(
            r"^\s*(?:(?:л|l)\s*/\s*(?:мин(?:ут\w*)?|min(?:ute)?s?|ч|h|hours?)|"
            r"(?:м|m)\s*[³3]\s*/\s*(?:ч|h|hours?)|"
            r"литр\w*\s+(?:в|/)?\s*минут\w*)(?![\w])",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "pressure",
        re.compile(
            r"^\s*(?:bar|bars?|бар(?:а|ов)?|kpa|кпа|mpa|мпа|pa|па|"
            r"atm|атм(?:осфер\w*)?)(?![\w])",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "area",
        re.compile(
            r"^\s*(?:(?:м|m)\s*[²2]|кв\.?\s*м|square\s+met(?:er|re)s?)"
            r"(?![\w])",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "temperature",
        re.compile(
            r"^\s*(?:°\s*[cс]|℃|celsius|цельси\w*)(?![\w])",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "power",
        re.compile(
            r"^\s*(?:kw|квт|киловатт\w*|w|вт|ватт\w*)(?![\w])",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "volume",
        re.compile(
            r"^\s*(?:(?:л|l)(?!\s*/)|литр\w*|(?:м|m)\s*[³3])"
            r"(?![\w])",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "length",
        re.compile(
            r"^\s*(?:mm|мм|миллиметр\w*|cm|см|сантиметр\w*|"
            r"um|мкм|микрометр\w*|m|м|met(?:er|re)s?|метр(?:а|ов)?)"
            r"(?![\w])",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "ratio",
        re.compile(
            r"^\s*(?:%|percent|процент\w*)(?![\w])",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "angle",
        re.compile(
            r"^\s*(?:°|deg(?:ree)?s?|градус\w*)(?![\w])",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "voltage",
        re.compile(
            r"^\s*(?:v|вольт\w*)(?![\w])",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "count",
        re.compile(
            r"^\s*(?:шт\.?|pcs?|pieces?|секц\w*|контур\w*)(?![\w])",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "money",
        re.compile(
            r"^\s*(?:rub|руб\w*|₽)(?![\w])",
            flags=re.IGNORECASE,
        ),
    ),
)
_LATIN_ALPHANUMERIC_IDENTIFIER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)"
    r"[A-Za-z0-9]+(?![A-Za-z0-9])"
)
_MIXED_IDENTIFIER_TOKEN_RE = re.compile(
    r"(?<![\w./-])"
    r"(?=[A-Za-zА-Яа-яЁё0-9._/-]*[A-Za-zА-Яа-яЁё])"
    r"(?=[A-Za-zА-Яа-яЁё0-9._/-]*\d)"
    r"[A-Za-zА-Яа-яЁё0-9](?:[A-Za-zА-Яа-яЁё0-9._/-]*"
    r"[A-Za-zА-Яа-яЁё0-9])?"
    r"(?![\w./-])"
)
_STRUCTURED_MODEL_NUMBER_RE = re.compile(
    r"(?<![\w])\d+(?:(?:\s*[-/]\s*|\s+)\d+){2,}"
    r"(?:\s*\(\s*\d+\s*\))?(?![\w])"
)
_LATIN_WORD_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]{3,}(?![A-Za-z])")

# Non-known facts are assertions about the customer's epistemic state.  A
# grounded product word is not evidence that the customer does not know a
# field, refused it, or postponed it.  These language-level markers are kept
# deliberately independent of product/SKU vocabulary.
_EXPLICIT_NON_KNOWN_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "unknown": (
        re.compile(
            r"\bне\s+(?:зна\w*|помн\w*|уверен\w*)\b|"
            r"\bнеизвест\w*\b|\bнет\s+(?:данн\w*|информац\w*)\b|"
            r"\bбез\s+понятия\b|"
            r"\bне\s+(?:мог|мож)\w*\s+"
            r"(?:определ\w*|уточн\w*|измер\w*|сказ\w*)\b",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:do\s+not|don't)\s+know\b|\bunknown\b|"
            r"\bnot\s+sure\b|\bno\s+(?:data|information)\b",
            flags=re.IGNORECASE,
        ),
    ),
    "refused": (
        re.compile(
            r"\bотказыва\w*\b|\bне\s+(?:хоч\w*|буд\w*|стан\w*)\s+"
            r"(?:сообщ\w*|говор\w*|уточн\w*|предостав\w*)\b|"
            r"\bне\s+(?:скаж\w*|сообщ\w*|предостав\w*)\b",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:refuse|decline)\b|\b(?:will\s+not|won't|do\s+not|don't)\s+"
            r"(?:tell|share|provide)\b",
            flags=re.IGNORECASE,
        ),
    ),
    "deferred": (
        re.compile(
            r"\b(?:позже|потом)\s+"
            r"(?:уточн\w*|скаж\w*|сообщ\w*|измер\w*|провер\w*)\b|"
            r"\b(?:уточн\w*|скаж\w*|сообщ\w*|измер\w*|провер\w*)\s+"
            r"(?:позже|потом)\b|\b(?:отлож\w*|остав\w*)\s+"
            r"(?:это\s+)?(?:на\s+потом|пока)\b|"
            r"\bверн\w*\s+к\s+(?:этому|параметр\w*)\s+(?:позже|потом)\b",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:tell|check|measure|clarify)\s+(?:it\s+)?later\b|"
            r"\b(?:postpone|defer)\b",
            flags=re.IGNORECASE,
        ),
    ),
}

_EXPLICIT_SOFTENING_RE = re.compile(
    r"\b(?:можно|допустим\w*|разреш\w*)\s+(?:взять\s+)?(?:ближайш\w*|"
    r"похож\w*|примерн\w*)\b|\bне\s+(?:обязательн\w*|строг\w*)\b|"
    r"\b(?:желательн\w*|предпочтительн\w*|примерн\w*|ориентировочн\w*)\b|"
    r"\bв\s+районе\b|"
    r"\b(?:approximately|roughly|preferably|nearest\s+is\s+acceptable)\b",
    flags=re.IGNORECASE,
)

# Availability has two opposite durable meanings that both mention an absent
# item: rejecting an unavailable candidate keeps the in-stock requirement,
# while permitting unavailable/unverified candidates removes it.  These
# compositional language markers are product- and dialogue-independent; an
# availability coordinate from the declarative ontology is still required.
_AVAILABILITY_RELAXATION_RE = re.compile(
    r"\b(?:наличи\w*)\s+(?:не\s+важн\w*|неважн\w*|необязательн\w*)\b|"
    r"\b(?:можно|разреш\w*|допустим\w*)\b[^.!?]{0,80}"
    r"\b(?:показ\w*|включ\w*|рассмотр\w*|предлож\w*)\b|"
    r"\b(?:without|regardless\s+of)\s+(?:confirmed\s+)?stock\b|"
    r"\b(?:may|can)\s+(?:also\s+)?(?:show|include|consider)\b",
    flags=re.IGNORECASE,
)
_UNAVAILABLE_CANDIDATE_REJECTION_RE = re.compile(
    r"\bне\s+(?:подойд\w*|устраива\w*|нуж\w*)\b|"
    r"\b(?:не\s+рассматрива\w*|не\s+показыва\w*|исключ\w*|откаж\w*)\b|"
    r"\b(?:not\s+suitable|does(?:n't|\s+not)\s+work|exclude|reject|"
    r"do(?:n't|\s+not)\s+(?:show|consider))\b",
    flags=re.IGNORECASE,
)

_EXPLICIT_REPLACEMENT_RE = re.compile(
    r"\b(?:замен\w*|аналог\w*|вместо|альтернатив\w*)\b|"
    r"\b(?:replace\w*|replacement|instead\s+of|alternative)\b",
    flags=re.IGNORECASE,
)
_NEGATED_REPLACEMENT_RE = re.compile(
    r"\bне\s+(?:нуж\w*|треб\w*)\s+(?:замен\w*|аналог\w*)\b|"
    r"\b(?:замен\w*|аналог\w*)\s+не\s+(?:нуж\w*|треб\w*)\b|"
    r"\bне\s+(?:ищ\w*|подбира\w*|показыва\w*)\s+"
    r"(?:замен\w*|аналог\w*)\b",
    flags=re.IGNORECASE,
)

# ``check_delivery`` is a domain act, not a synonym for the generic imperative
# "check".  These are logistics-domain stems rather than test utterances.
_DELIVERY_SCOPE_RE = re.compile(
    r"\b(?:достав\w*|логист\w*|перевоз\w*|транспортир\w*|отгруз\w*|"
    r"самовывоз\w*|курьер\w*|склад\w*|пункт\w*\s+(?:выдач\w*|отгруз\w*)|"
    r"shipping|delivery|logistics?|freight|dispatch|warehouse|pickup|courier)\b",
    flags=re.IGNORECASE,
)
_DIRECT_QUESTION_RE = re.compile(
    r"\?|^\s*(?:ка(?:кой|кая|кие|ково|кую)|сколько|что|где|когда|почему|"
    r"зачем|есть\s+ли|можно\s+ли|подходит\s+ли|what|which|how|where|when|why)\b",
    flags=re.IGNORECASE,
)

_POWER_ANCHOR_RE = re.compile(
    r"(?<![\w])(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>к\s*вт|kw)(?![\w])",
    flags=re.IGNORECASE,
)
_POWER_RANGE_ANCHOR_RE = re.compile(
    r"(?<![\w])(?:от\s+|from\s+)?"
    r"(?P<minimum>\d+(?:[.,]\d+)?)\s*"
    r"(?:(?:к\s*вт|kw)\s*)?"
    r"(?:[-\u2013\u2014]|до|to)\s*"
    r"(?P<maximum>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>к\s*вт|kw)(?![\w])",
    flags=re.IGNORECASE,
)
_FLOW_ANCHOR_RE = re.compile(
    r"(?<![\w])(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>л\s*/\s*мин(?:ут\w*)?|л\s*/\s*ч|м\s*[³3]\s*/\s*ч|"
    r"l\s*/\s*min(?:ute)?|l\s*/\s*h|m\s*[³3]\s*/\s*h|"
    r"литр\w*\s+в\s+минут\w*)\b",
    flags=re.IGNORECASE,
)
_PIPE_QUANTITY_ANCHOR_RE = re.compile(
    r"(?<![\w])(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>метр(?:а|ов)?|met(?:er|re)s?|м|m)(?![\w])",
    flags=re.IGNORECASE,
)
_PIPE_QUANTITY_CONTEXT_RE = re.compile(
    r"\b(?:количеств\w*|нуж\w*|треб\w*|заказ\w*|взять|купить|"
    r"quantity|need|order|buy)\b",
    flags=re.IGNORECASE,
)
_HEAD_ANCHOR_RES = (
    re.compile(
        r"(?<![\w])(?P<value>\d+(?:[.,]\d+)?)\s*"
        r"(?P<unit>м|m)\s+(?:напор\w*|head)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:напор\w*|head)\s*(?:в|около|примерно|of|at)?\s*"
        r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>м|m)(?![\w])",
        flags=re.IGNORECASE,
    ),
)
_PIPE_DIAMETER_ANCHOR_RES = (
    re.compile(
        r"\b(?:наружн\w*\s+)?(?:диаметр\w*|diameter)\s*[:=]?\s*"
        r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>мм|mm)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"(?:[Ø⌀]|\bdn\s*)(?P<value>\d+(?:[.,]\d+)?)"
        r"(?:\s*(?P<unit>мм|mm))?\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:pe\s*[- ]?x|pex|труб\w*|pipe)\s*[-:/]?\s*"
        r"(?P<value>\d{1,3})(?:\s*(?P<unit>мм|mm))?\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"(?<![\w])(?P<value>\d{1,3})\s*[xх×]\s*\d+(?:[.,]\d+)?"
        r"(?:\s*(?P<unit>мм|mm))?\b",
        flags=re.IGNORECASE,
    ),
)
_CYRILLIC_TRANSLITERATION = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}
_EVIDENCE_STOP_WORDS = frozenset(
    {
        # Function words do not provide enough provenance by themselves.  The
        # set is intentionally language-level, never product- or test-specific.
        "а",
        "без",
        "бы",
        "в",
        "во",
        "вот",
        "все",
        "где",
        "да",
        "для",
        "до",
        "если",
        "есть",
        "же",
        "за",
        "и",
        "из",
        "или",
        "к",
        "как",
        "мне",
        "на",
        "над",
        "не",
        "нет",
        "но",
        "нужен",
        "нужна",
        "нужно",
        "о",
        "об",
        "от",
        "по",
        "под",
        "про",
        "с",
        "со",
        "так",
        "то",
        "у",
        "уже",
        "что",
        "чтобы",
        "это",
        "я",
        "a",
        "an",
        "and",
        "are",
        "can",
        "do",
        "for",
        "from",
        "in",
        "is",
        "need",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "you",
    }
)


def _source_tokens(value: str) -> list[tuple[str, int, int]]:
    """Return normalized word tokens and their exact spans in *value*."""

    result: list[tuple[str, int, int]] = []
    for match in _SOURCE_TOKEN_RE.finditer(str(value or "")):
        normalized = unicodedata.normalize("NFKC", match.group(0)).casefold()
        normalized = normalized.replace("ё", "е")
        if normalized:
            result.append((normalized, match.start(), match.end()))
    return result


def _explicit_numeric_values(value: str) -> tuple[float, ...]:
    return tuple(
        float(match.group(0).replace(",", "."))
        for match in _NUMERIC_LITERAL_RE.finditer(str(value or ""))
    )


def _parsed_numeric_string(
    value: object,
) -> tuple[str, tuple[float, ...], str | None] | None:
    """Parse a scalar or interval only for grounding comparisons.

    The original constraint value is never replaced or converted.  Parsed
    floats are ephemeral equality coordinates used to prove that the exact
    numeric proposal is present in the current-turn evidence.
    """

    if not isinstance(value, str):
        return None
    range_match = _NUMERIC_STRING_RANGE_RE.fullmatch(value)
    if range_match is not None:
        minimum = float(range_match.group("minimum").replace(",", "."))
        maximum = float(range_match.group("maximum").replace(",", "."))
        inline_unit = range_match.group("unit").strip() or None
        return "range", (minimum, maximum), inline_unit
    scalar_match = _NUMERIC_STRING_SCALAR_RE.fullmatch(value)
    if scalar_match is None:
        return None
    scalar = float(scalar_match.group("value").replace(",", "."))
    inline_unit = scalar_match.group("unit").strip() or None
    return "scalar", (scalar,), inline_unit


def _evidence_numeric_ranges(value: str) -> tuple[tuple[float, float], ...]:
    return tuple(
        (
            float(match.group("minimum").replace(",", ".")),
            float(match.group("maximum").replace(",", ".")),
        )
        for match in _EVIDENCE_NUMERIC_RANGE_RE.finditer(str(value or ""))
    )


def _numeric_coordinates(constraint: dict[str, Any]) -> tuple[float, ...]:
    value = constraint.get("value")
    if isinstance(value, bool):
        return ()
    if isinstance(value, (int, float)):
        return (float(value),)
    parsed = _parsed_numeric_string(value)
    return parsed[1] if parsed is not None else ()


def _numeric_fact_unit_family(name: str) -> str | None:
    """Return the physical unit family encoded by a canonical fact name."""

    normalized = _normalize_evidence(name).replace("-", "_").replace(" ", "_")
    for suffix, family in _NUMERIC_FACT_UNIT_FAMILIES:
        if normalized == suffix.removeprefix("_") or normalized.endswith(suffix):
            return family
    return None


def _explicit_unit_family(value: str, *, require_full: bool) -> str | None:
    """Classify an explicit unit token without converting its numeric value."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = normalized.replace("ё", "е")
    for family, pattern in _EXPLICIT_UNIT_FAMILY_PATTERNS:
        match = pattern.match(normalized)
        if match is None:
            continue
        if require_full and normalized[match.end() :].strip(" .,:;()[]{}"):
            continue
        return family
    return None


def _evidence_unit_families_for_value(
    evidence: str,
    value: int | float,
) -> frozenset[str]:
    """Return units attached to exact occurrences of *value* in evidence."""

    families: set[str] = set()
    for match in _NUMERIC_LITERAL_RE.finditer(evidence):
        evidence_value = float(match.group(0).replace(",", "."))
        if not math.isclose(
            float(value),
            evidence_value,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            continue
        family = _explicit_unit_family(
            evidence[match.end() :],
            require_full=False,
        )
        if family is not None:
            families.add(family)
    return frozenset(families)


def _numeric_constraint_unit_incompatibility(
    constraint: dict[str, Any],
) -> tuple[str, tuple[str, ...]] | None:
    """Describe a cross-dimension numeric proposal, if one is present.

    Missing units remain admissible because short pending-question answers can
    be intentionally unitless.  An explicit but unknown unit fails closed: it
    cannot be proven compatible with the canonical fact family.
    """

    values = _numeric_coordinates(constraint)
    if not values:
        return None
    schema = _numeric_constraint_schema(str(constraint.get("name") or ""))
    expected = (
        str(schema.get("unit_family") or "") or None
        if schema is not None
        else _numeric_fact_unit_family(str(constraint.get("name") or ""))
    )
    if expected is None:
        return None

    observed: set[str] = set()
    declared_units: list[str] = []
    raw_unit = constraint.get("unit")
    if isinstance(raw_unit, str) and raw_unit.strip():
        declared_units.append(raw_unit)
    parsed_string = _parsed_numeric_string(constraint.get("value"))
    if parsed_string is not None and parsed_string[2]:
        declared_units.append(parsed_string[2] or "")
    for declared_unit in declared_units:
        declared = _explicit_unit_family(declared_unit, require_full=True)
        if declared is None:
            return expected, ("unrecognized",)
        observed.add(declared)
    for value in values:
        observed.update(
            _evidence_unit_families_for_value(
                str(constraint.get("evidence") or ""),
                value,
            )
        )
    incompatible = tuple(sorted(family for family in observed if family != expected))
    if incompatible:
        return expected, incompatible
    return None


def _numeric_string_declared_unit_is_ungrounded(
    constraint: dict[str, Any],
) -> bool:
    """Reject a unit attached by the model but absent from turn evidence."""

    parsed = _parsed_numeric_string(constraint.get("value"))
    if parsed is None:
        return False
    declared_units = [
        unit
        for unit in (constraint.get("unit"), parsed[2])
        if isinstance(unit, str) and unit.strip()
    ]
    if not declared_units:
        return False
    declared_families = {
        family
        for unit in declared_units
        if (family := _explicit_unit_family(unit, require_full=True)) is not None
    }
    if not declared_families:
        # The incompatibility validator reports unknown declared units.
        return False
    evidence_families: set[str] = set()
    for value in parsed[1]:
        evidence_families.update(
            _evidence_unit_families_for_value(
                str(constraint.get("evidence") or ""),
                value,
            )
        )
    return not declared_families.issubset(evidence_families)


def _consonant_skeleton(value: str) -> str:
    return "".join(
        character
        for character in value
        if character in "бвгджзйклмнпрстфхцчшщ"
    )


def _is_cyrillic_word(value: str) -> bool:
    return bool(value) and all(
        character in _CYRILLIC_TRANSLITERATION for character in value
    )


def _is_latin_word(value: str) -> bool:
    return bool(value) and value.isascii() and value.isalpha()


def _mixed_cyrillic_transliteration(value: str) -> str | None:
    has_cyrillic = any(
        character in _CYRILLIC_TRANSLITERATION for character in value
    )
    has_latin = any(
        character.isascii() and character.isalpha() for character in value
    )
    if not has_cyrillic or not has_latin:
        return None
    if not all(
        character in _CYRILLIC_TRANSLITERATION
        or (character.isascii() and character.isalpha())
        for character in value
    ):
        return None
    return "".join(
        _CYRILLIC_TRANSLITERATION.get(character, character)
        for character in value
    )


def _transliteration_tokens_match(left: str, right: str) -> bool:
    mixed_left = _mixed_cyrillic_transliteration(left)
    mixed_right = _mixed_cyrillic_transliteration(right)
    if mixed_left is not None and _is_latin_word(right):
        return mixed_left == right
    if mixed_right is not None and _is_latin_word(left):
        return mixed_right == left
    if _is_cyrillic_word(left) and _is_latin_word(right):
        cyrillic, latin = left, right
    elif _is_cyrillic_word(right) and _is_latin_word(left):
        cyrillic, latin = right, left
    else:
        return False
    transliterated = "".join(
        _CYRILLIC_TRANSLITERATION[character] for character in cyrillic
    )
    if transliterated == latin:
        return True
    # Tolerate common reversible transliteration variants (for example h/kh
    # and final y/yi), but do not perform semantic fuzzy matching.
    return (
        min(len(transliterated), len(latin)) >= 4
        and SequenceMatcher(None, transliterated, latin).ratio() >= 0.84
    )


def _source_tokens_match(left: str, right: str) -> bool:
    """Conservative morphology-tolerant token comparison.

    Numbers and alphanumeric identifiers remain exact.  Natural-language words
    may differ only by a close inflectional form.  This is deliberately not a
    semantic synonym matcher: it cannot manufacture a source span that the
    customer did not write.
    """

    if left == right:
        return True
    if any(character.isdigit() for character in left + right):
        return False
    if _transliteration_tokens_match(left, right):
        return True
    if min(len(left), len(right)) < 4:
        return False
    if left.isascii() or right.isascii():
        return SequenceMatcher(None, left, right).ratio() >= 0.9
    left_skeleton = _consonant_skeleton(left)
    right_skeleton = _consonant_skeleton(right)
    if (
        len(left_skeleton) >= 3
        and left_skeleton == right_skeleton
        and SequenceMatcher(None, left, right).ratio() >= 0.78
    ):
        return True
    common_prefix = len(
        next(
            (
                left[:index]
                for index in range(min(len(left), len(right)), 0, -1)
                if left[:index] == right[:index]
            ),
            "",
        )
    )
    return (
        common_prefix >= 4
        and common_prefix / min(len(left), len(right)) >= 0.65
        and SequenceMatcher(None, left, right).ratio() >= 0.72
    )


def _bounded_source_fragment(
    source: str,
    start: int,
    end: int,
    *,
    max_length: int = 240,
) -> str:
    fragment = source[start:end].strip()
    if len(fragment) <= max_length:
        return fragment
    bounded = fragment[:max_length]
    last_space = bounded.rfind(" ")
    if last_space >= max_length // 2:
        bounded = bounded[:last_space]
    return bounded.rstrip()


def _grounded_evidence_fragment(evidence: str, current_message: str) -> str | None:
    """Map a close LLM quotation back to an exact, bounded source fragment.

    A repair is allowed only when the evidence has enough ordered lexical
    anchors in the current message.  Function words never count as anchors;
    every stated number/identifier must match exactly.  The returned value is
    always sliced from ``current_message`` and therefore remains auditable.
    """

    exact_evidence = evidence.strip()
    exact_start = current_message.find(exact_evidence)
    if exact_evidence and exact_start >= 0:
        return _bounded_source_fragment(
            current_message,
            exact_start,
            exact_start + len(exact_evidence),
        )

    evidence_tokens = _source_tokens(evidence)
    message_tokens = _source_tokens(current_message)
    if not evidence_tokens or not message_tokens:
        return None

    evidence_words = [item[0] for item in evidence_tokens]
    message_words = [item[0] for item in message_tokens]

    # First handle punctuation/spacing differences without any fuzzy matching.
    width = len(evidence_words)
    for start_index in range(len(message_words) - width + 1):
        if message_words[start_index : start_index + width] == evidence_words:
            return _bounded_source_fragment(
                current_message,
                message_tokens[start_index][1],
                message_tokens[start_index + width - 1][2],
            )

    content_evidence = [
        item
        for item in evidence_words
        if item not in _EVIDENCE_STOP_WORDS and (len(item) >= 3 or item.isdigit())
    ]
    content_message = [
        (word, start, end)
        for word, start, end in message_tokens
        if word not in _EVIDENCE_STOP_WORDS and (len(word) >= 3 or word.isdigit())
    ]
    if not content_evidence or not content_message:
        return None

    # Longest ordered match.  Source messages are bounded at the API boundary,
    # so this small dynamic program is deterministic and inexpensive.
    rows = len(content_evidence) + 1
    columns = len(content_message) + 1
    lengths = [[0] * columns for _ in range(rows)]
    for evidence_index in range(1, rows):
        for message_index in range(1, columns):
            if _source_tokens_match(
                content_evidence[evidence_index - 1],
                content_message[message_index - 1][0],
            ):
                lengths[evidence_index][message_index] = (
                    lengths[evidence_index - 1][message_index - 1] + 1
                )
            else:
                lengths[evidence_index][message_index] = max(
                    lengths[evidence_index - 1][message_index],
                    lengths[evidence_index][message_index - 1],
                )

    evidence_index = len(content_evidence)
    message_index = len(content_message)
    pairs: list[tuple[int, int]] = []
    while evidence_index and message_index:
        if _source_tokens_match(
            content_evidence[evidence_index - 1],
            content_message[message_index - 1][0],
        ) and lengths[evidence_index][message_index] == (
            lengths[evidence_index - 1][message_index - 1] + 1
        ):
            pairs.append((evidence_index - 1, message_index - 1))
            evidence_index -= 1
            message_index -= 1
        elif lengths[evidence_index - 1][message_index] >= lengths[evidence_index][
            message_index - 1
        ]:
            evidence_index -= 1
        else:
            message_index -= 1
    pairs.reverse()

    match_count = len(pairs)
    required_matches = (
        1
        if len(content_evidence) == 1
        else 2
        if len(content_evidence) == 2
        else max(2, math.ceil(len(content_evidence) * 0.67))
    )
    if match_count < required_matches:
        return None

    paired_evidence_indexes = {item[0] for item in pairs}
    for index, token in enumerate(content_evidence):
        if (
            any(character.isdigit() for character in token)
            and index not in paired_evidence_indexes
        ):
            return None

    paired_message_indexes = [item[1] for item in pairs]
    first_message_index = min(paired_message_indexes)
    last_message_index = max(paired_message_indexes)
    # Do not join weak anchors scattered across an unrelated long message.
    if last_message_index - first_message_index + 1 > match_count * 4 + 4:
        return None
    return _bounded_source_fragment(
        current_message,
        content_message[first_message_index][1],
        content_message[last_message_index][2],
    )


def _combined_grounded_evidence_fragment(
    first: str,
    second: str,
    current_message: str,
) -> str | None:
    """Return one exact bounded source span covering two grounded fragments."""

    first_start = current_message.find(first)
    second_start = current_message.find(second)
    if first_start < 0 or second_start < 0:
        return None
    start = min(first_start, second_start)
    end = max(first_start + len(first), second_start + len(second))
    if end - start > 240:
        return None
    return current_message[start:end].strip()


def _categorical_scalar_key(value: Any) -> tuple[str, str]:
    """Return a type-preserving key for declarative categorical values."""

    return type(value).__name__, json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _closed_value_groups_for_fact(
    fact_name: str,
) -> tuple[dict[str, Any], ...]:
    """Read one fact's closed value groups from the semantic ontology.

    A fact is validated only when its ontology explicitly declares
    ``closed_values``.  Definitions duplicated across compatible product kinds
    are merged by their typed canonical value, keeping this guard independent
    from products, SKUs and individual dialogue examples.
    """

    normalized_name = _normalize_evidence(fact_name)
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    vocabulary = semantic_ontology_payload().get("constraint_vocabulary") or {}
    for definitions in vocabulary.values():
        if not isinstance(definitions, list):
            continue
        for definition in definitions:
            if not isinstance(definition, dict) or _normalize_evidence(
                str(definition.get("name") or "")
            ) != normalized_name:
                continue
            groups = definition.get("closed_values")
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict) or "value" not in group:
                    continue
                canonical_value = group["value"]
                key = _categorical_scalar_key(canonical_value)
                target = merged.setdefault(
                    key,
                    {
                        "value": canonical_value,
                        "aliases": [],
                        "equivalent_values": [],
                    },
                )
                for field in ("aliases", "equivalent_values"):
                    items = group.get(field)
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if item not in target[field]:
                            target[field].append(item)
    return tuple(merged.values())


def _declared_alias_matches_text(alias: str, text: str) -> bool:
    """Match one approved alias as an ordered, contiguous source phrase.

    Token comparison retains the existing conservative inflection and
    transliteration support.  Unlike general evidence rebinding, categorical
    value support cannot bridge omitted words: the actual value alias must be
    present in the evidence fragment.
    """

    alias_tokens = [item[0] for item in _source_tokens(alias)]
    text_tokens = [item[0] for item in _source_tokens(text)]
    if not alias_tokens or len(alias_tokens) > len(text_tokens):
        return False
    width = len(alias_tokens)
    return any(
        all(
            _source_tokens_match(alias_token, text_token)
            for alias_token, text_token in zip(
                alias_tokens,
                text_tokens[start : start + width],
            )
        )
        for start in range(len(text_tokens) - width + 1)
    )


def _categorical_value_matches_group(value: Any, group: dict[str, Any]) -> bool:
    canonical = group.get("value")
    if _categorical_scalar_key(value) == _categorical_scalar_key(canonical):
        return True
    for equivalent in group.get("equivalent_values") or ():
        if _categorical_scalar_key(value) == _categorical_scalar_key(equivalent):
            return True
    if not isinstance(value, str):
        return False
    value_text = value.strip()
    if not value_text:
        return False
    candidates = [canonical, *(group.get("aliases") or ())]
    return any(
        isinstance(candidate, str)
        and _declared_alias_matches_text(candidate, value_text)
        for candidate in candidates
    )


def _categorical_value_directly_matches_group(
    value: Any,
    group: dict[str, Any],
) -> bool:
    """Prefer an exact canonical/equivalent scalar over substring aliases."""

    if _categorical_scalar_key(value) == _categorical_scalar_key(
        group.get("value")
    ):
        return True
    return any(
        _categorical_scalar_key(value) == _categorical_scalar_key(equivalent)
        for equivalent in (group.get("equivalent_values") or ())
    )


def _categorical_evidence_score(group: dict[str, Any], evidence: str) -> int:
    """Return the strongest approved alias actually present in evidence."""

    candidates = [group.get("value"), *(group.get("aliases") or ())]
    scores: list[int] = []
    for candidate in candidates:
        if not isinstance(candidate, str) or not _declared_alias_matches_text(
            candidate,
            evidence,
        ):
            continue
        normalized = _normalize_evidence(candidate)
        scores.append(len(_source_tokens(candidate)) * 1000 + len(normalized))
    return max(scores, default=0)


def _closed_categorical_constraint_issue(
    constraint: dict[str, Any],
) -> tuple[str, str] | None:
    """Validate a known closed categorical value against its exact evidence.

    The first tuple item is a typed ambiguity kind, and the second is a stable
    repair reason.  Open-ended strings and facts without a declarative closed
    value set intentionally remain untouched.
    """

    if constraint.get("status") != ConstraintStatus.KNOWN.value:
        return None
    groups = _closed_value_groups_for_fact(str(constraint.get("name") or ""))
    if not groups:
        return None
    value = constraint.get("value")
    directly_matching_groups = tuple(
        index
        for index, group in enumerate(groups)
        if _categorical_value_directly_matches_group(value, group)
    )
    matching_groups = directly_matching_groups or tuple(
        index
        for index, group in enumerate(groups)
        if _categorical_value_matches_group(value, group)
    )
    if len(matching_groups) != 1:
        return (
            "constraint_closed_value_not_allowed",
            "constraint_closed_value_not_allowed_dropped",
        )

    scores = tuple(
        _categorical_evidence_score(group, str(constraint.get("evidence") or ""))
        for group in groups
    )
    strongest = max(scores, default=0)
    value_group = matching_groups[0]
    strongest_groups = tuple(
        index for index, score in enumerate(scores) if score == strongest and score > 0
    )
    if strongest <= 0 or strongest_groups != (value_group,):
        return (
            "constraint_closed_value_not_grounded",
            "constraint_closed_value_not_grounded_dropped",
        )
    return None


def _normalize_schema_identifier(value: Any) -> str:
    return (
        _normalize_evidence(str(value or ""))
        .replace("-", "_")
        .replace(" ", "_")
    )


def _numeric_constraint_schema(fact_name: str) -> dict[str, Any] | None:
    """Resolve numeric fact metadata from contracts and semantic ontology.

    Contract aliases are accepted only as schema aliases.  Natural-language
    evidence never participates in this lookup, and the canonical public
    range allowlist remains the sole authority for interval support.
    """

    normalized_name = _normalize_schema_identifier(fact_name)
    exact_definitions = [
        definition
        for contract in DEFAULT_CONTRACTS
        for definition in contract.fact_definitions
        if _normalize_schema_identifier(definition.name) == normalized_name
    ]
    alias_definitions = [
        definition
        for contract in DEFAULT_CONTRACTS
        for definition in contract.fact_definitions
        if normalized_name
        in {
            _normalize_schema_identifier(alias)
            for alias in definition.aliases
        }
    ]
    definitions = exact_definitions or alias_definitions
    numeric_definitions = [
        definition
        for definition in definitions
        if str(getattr(definition.value_type, "value", definition.value_type))
        == "number"
    ]
    if numeric_definitions:
        canonical_names = {
            definition.name for definition in numeric_definitions
        }
        unit_families = {
            family
            for definition in numeric_definitions
            if (family := _numeric_fact_unit_family(definition.name)) is not None
        }
        canonical_name = (
            next(iter(canonical_names))
            if len(canonical_names) == 1
            else normalized_name
        )
        return {
            "canonical_name": canonical_name,
            "unit_family": (
                next(iter(unit_families)) if len(unit_families) == 1 else None
            ),
            "range_capable": bool(canonical_names)
            and canonical_names.issubset(RANGE_CAPABLE_CONSTRAINT_FACTS),
            "closed_categorical": bool(
                len(canonical_names) == 1
                and _closed_value_groups_for_fact(canonical_name)
            ),
        }

    vocabulary = semantic_ontology_payload().get("constraint_vocabulary") or {}
    for definitions in vocabulary.values():
        for definition in definitions or ():
            if not isinstance(definition, dict):
                continue
            canonical_name = str(definition.get("name") or "")
            if _normalize_schema_identifier(canonical_name) != normalized_name:
                continue
            unit_family = _numeric_fact_unit_family(canonical_name)
            groups = definition.get("closed_values") or ()
            closed_numeric = bool(groups) and all(
                isinstance(group, dict)
                and isinstance(group.get("value"), (int, float, bool))
                for group in groups
            )
            if (
                unit_family is None
                and not closed_numeric
                and canonical_name not in RANGE_CAPABLE_CONSTRAINT_FACTS
            ):
                return None
            return {
                "canonical_name": canonical_name,
                "unit_family": unit_family,
                "range_capable": (
                    canonical_name in RANGE_CAPABLE_CONSTRAINT_FACTS
                ),
                "closed_categorical": closed_numeric,
            }
    return None


def _numeric_string_grounding_issue(
    constraint: dict[str, Any],
) -> str | None:
    """Fail closed when a typed string number is not proven by this turn."""

    parsed = _parsed_numeric_string(constraint.get("value"))
    if parsed is None:
        return None
    schema = _numeric_constraint_schema(str(constraint.get("name") or ""))
    if schema is None:
        # Numeric-looking identifiers and other text facts remain text.
        return None
    kind, values, _inline_unit = parsed
    if kind == "range":
        if not schema["range_capable"]:
            return "constraint_numeric_range_not_allowed_dropped"
        if values[0] > values[1]:
            return "constraint_numeric_range_invalid_dropped"
        ranges = _evidence_numeric_ranges(str(constraint.get("evidence") or ""))
        if not any(
            math.isclose(values[0], minimum, rel_tol=1e-9, abs_tol=1e-9)
            and math.isclose(values[1], maximum, rel_tol=1e-9, abs_tol=1e-9)
            for minimum, maximum in ranges
        ):
            return "constraint_numeric_value_not_in_evidence_dropped"
        return None

    # Closed categorical values such as circuits=1 may be grounded by an
    # approved linguistic alias ("одноконтурный", "только отопление") rather
    # than by a digit.  Their existing closed-value validator remains the
    # authority; only intervals are forbidden above.
    if schema["closed_categorical"]:
        return None
    evidence_numbers = _explicit_numeric_values(
        str(constraint.get("evidence") or "")
    )
    if not any(
        math.isclose(values[0], evidence_number, rel_tol=1e-9, abs_tol=1e-9)
        for evidence_number in evidence_numbers
    ):
        return "constraint_numeric_value_not_in_evidence_dropped"
    return None


def _canonical_constraint_fact_name(fact_name: str) -> str:
    """Resolve one stable fact name from declarative schema aliases only."""

    capability_rule = _capability_constraint_rule(fact_name)
    if capability_rule is not None:
        return _normalize_schema_identifier(
            capability_rule.get("canonical_name")
        )

    normalized_name = _normalize_schema_identifier(fact_name)
    exact_names: set[str] = set()
    alias_names: set[str] = set()
    for contract in DEFAULT_CONTRACTS:
        for definition in contract.fact_definitions:
            canonical_name = _normalize_schema_identifier(definition.name)
            if canonical_name == normalized_name:
                exact_names.add(definition.name)
                continue
            if normalized_name in {
                _normalize_schema_identifier(alias)
                for alias in definition.aliases
            }:
                alias_names.add(definition.name)
    candidates = exact_names or alias_names
    if len(candidates) == 1:
        return next(iter(candidates))
    return normalized_name


def _constraint_numeric_scalar(
    constraint: dict[str, Any],
) -> float | None:
    """Return an ephemeral scalar coordinate without rewriting the fact."""

    if _numeric_constraint_schema(str(constraint.get("name") or "")) is None:
        return None
    value = constraint.get("value")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    parsed = _parsed_numeric_string(value)
    if parsed is None or parsed[0] != "scalar":
        return None
    return parsed[1][0]


def _constraint_contains_numeric_range(constraint: dict[str, Any]) -> bool:
    parsed = _parsed_numeric_string(constraint.get("value"))
    return bool(
        parsed is not None
        and parsed[0] == "range"
        and _numeric_constraint_schema(str(constraint.get("name") or ""))
        is not None
    )


def _constraint_closed_value_group(
    constraint: dict[str, Any],
) -> int | None:
    groups = _closed_value_groups_for_fact(
        _canonical_constraint_fact_name(str(constraint.get("name") or ""))
    )
    if not groups:
        return None
    matching_groups = tuple(
        index
        for index, group in enumerate(groups)
        if _categorical_value_matches_group(constraint.get("value"), group)
    )
    return matching_groups[0] if len(matching_groups) == 1 else None


def _constraint_values_equivalent(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    """Compare typed scalar coordinates without unit conversion."""

    if _canonical_constraint_fact_name(
        str(left.get("name") or "")
    ) != _canonical_constraint_fact_name(str(right.get("name") or "")):
        return False

    left_group = _constraint_closed_value_group(left)
    right_group = _constraint_closed_value_group(right)
    if left_group is not None or right_group is not None:
        return left_group is not None and left_group == right_group

    left_numeric = _constraint_numeric_scalar(left)
    right_numeric = _constraint_numeric_scalar(right)
    if left_numeric is not None or right_numeric is not None:
        return bool(
            left_numeric is not None
            and right_numeric is not None
            and math.isclose(
                left_numeric,
                right_numeric,
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
        )

    left_value = left.get("value")
    right_value = right.get("value")
    if isinstance(left_value, bool) or isinstance(right_value, bool):
        return type(left_value) is type(right_value) and left_value == right_value
    if isinstance(left_value, str) and isinstance(right_value, str):
        return _normalize_evidence(left_value) == _normalize_evidence(right_value)
    return _categorical_scalar_key(left_value) == _categorical_scalar_key(
        right_value
    )


def _constraint_declared_units(
    constraint: dict[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return recognized families and opaque labels for one fact's units."""

    raw_units: list[str] = []
    unit = constraint.get("unit")
    if isinstance(unit, str) and unit.strip():
        raw_units.append(unit)
    parsed = _parsed_numeric_string(constraint.get("value"))
    if parsed is not None and parsed[2]:
        raw_units.append(parsed[2] or "")

    families: set[str] = set()
    opaque: set[str] = set()
    for raw_unit in raw_units:
        family = _explicit_unit_family(raw_unit, require_full=True)
        if family is None:
            opaque.add(_normalize_evidence(raw_unit))
        else:
            families.add(family)
    return tuple(sorted(families)), tuple(sorted(opaque))


def _constraint_units_compatible(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    """Check physical-family compatibility without converting a value."""

    left_families, left_opaque = _constraint_declared_units(left)
    right_families, right_opaque = _constraint_declared_units(right)
    if left_opaque or right_opaque:
        return bool(
            left_opaque
            and right_opaque
            and left_opaque == right_opaque
            and not left_families
            and not right_families
        )
    if left_families and right_families:
        return left_families == right_families
    # A stable canonical numeric fact already supplies its physical family;
    # an omitted unit on a short follow-up therefore stays compatible.
    return True


def _constraint_value_is_grounded_scalar(
    constraint: dict[str, Any],
) -> bool:
    """Prove a current known scalar from its already rebound evidence."""

    if constraint.get("status") != ConstraintStatus.KNOWN.value:
        return False
    if _constraint_contains_numeric_range(constraint):
        return False

    canonical_name = _canonical_constraint_fact_name(
        str(constraint.get("name") or "")
    )
    groups = _closed_value_groups_for_fact(canonical_name)
    if groups:
        candidate = dict(constraint)
        candidate["name"] = canonical_name
        return _closed_categorical_constraint_issue(candidate) is None

    capability_rule = _capability_constraint_rule(canonical_name)
    if capability_rule is not None:
        return _capability_evidence_is_grounded(
            str(constraint.get("evidence") or ""),
            capability_rule,
            aliases_field="constraint_evidence_aliases",
        )

    numeric = _constraint_numeric_scalar(constraint)
    if numeric is not None:
        value = constraint.get("value")
        if isinstance(value, str):
            return (
                _numeric_string_grounding_issue(constraint) is None
                and _numeric_constraint_unit_incompatibility(constraint) is None
                and not _numeric_string_declared_unit_is_ungrounded(constraint)
            )
        return bool(
            any(
                math.isclose(
                    numeric,
                    evidence_number,
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                )
                for evidence_number in _explicit_numeric_values(
                    str(constraint.get("evidence") or "")
                )
            )
            and _numeric_constraint_unit_incompatibility(constraint) is None
        )

    value = constraint.get("value")
    return bool(
        isinstance(value, str)
        and value.strip()
        and _declared_alias_matches_text(
            value,
            str(constraint.get("evidence") or ""),
        )
    )


def _dedupe_equivalent_numeric_constraints(
    constraints: list[dict[str, Any]],
    changes: list[str],
) -> list[dict[str, Any]]:
    """Collapse duplicate scalar strings/numbers, preferring typed numbers."""

    deduplicated: list[dict[str, Any]] = []
    for source in constraints:
        candidate = dict(source)
        candidate_numeric = _constraint_numeric_scalar(candidate)
        duplicate_index: int | None = None
        if (
            candidate.get("status") == ConstraintStatus.KNOWN.value
            and candidate_numeric is not None
            and not _constraint_contains_numeric_range(candidate)
        ):
            canonical_name = _canonical_constraint_fact_name(
                str(candidate.get("name") or "")
            )
            for index, existing in enumerate(deduplicated):
                if existing.get("status") != ConstraintStatus.KNOWN.value:
                    continue
                if _canonical_constraint_fact_name(
                    str(existing.get("name") or "")
                ) != canonical_name:
                    continue
                if existing.get("polarity") != candidate.get("polarity"):
                    continue
                if existing.get("applies_to_product") != candidate.get(
                    "applies_to_product"
                ):
                    continue
                if not _constraint_units_compatible(existing, candidate):
                    continue
                if not _constraint_values_equivalent(existing, candidate):
                    continue
                duplicate_index = index
                break

        if duplicate_index is None:
            deduplicated.append(candidate)
            continue

        existing = deduplicated[duplicate_index]
        existing_is_typed = isinstance(existing.get("value"), (int, float)) and not isinstance(
            existing.get("value"), bool
        )
        candidate_is_typed = isinstance(candidate.get("value"), (int, float)) and not isinstance(
            candidate.get("value"), bool
        )
        if candidate_is_typed and not existing_is_typed:
            candidate["name"] = _canonical_constraint_fact_name(
                str(candidate.get("name") or "")
            )
            deduplicated[duplicate_index] = candidate
            changes.append("constraint_equivalent_numeric_duplicate_preferred_typed")
        elif existing.get("name") != _canonical_constraint_fact_name(
            str(existing.get("name") or "")
        ):
            existing["name"] = _canonical_constraint_fact_name(
                str(existing.get("name") or "")
            )
        changes.append("constraint_equivalent_numeric_duplicate_dropped")
    return deduplicated


def _current_constraint_targets_active_goal(
    constraint: dict[str, Any],
    products: list[dict[str, Any]],
    authoritative_state: dict[str, Any],
) -> bool:
    product_index = constraint.get("applies_to_product")
    if product_index is None:
        return True
    if (
        isinstance(product_index, bool)
        or not isinstance(product_index, int)
        or product_index < 0
        or product_index >= len(products)
    ):
        return False
    product = products[product_index]
    if str(product.get("role") or "") not in {
        ProductRole.TARGET.value,
        ProductRole.ALTERNATIVE.value,
    }:
        return False
    active_goal_id = authoritative_state.get("active_goal_id")
    active_goals = [
        goal
        for goal in (authoritative_state.get("goals") or ())
        if isinstance(goal, dict) and goal.get("goal_id") == active_goal_id
    ]
    if len(active_goals) != 1:
        return False
    active_type = _normalize_schema_identifier(
        active_goals[0].get("canonical_type")
    )
    current_type = _normalize_schema_identifier(product.get("canonical_type"))
    return bool(active_type and current_type and active_type == current_type)


def _active_known_facts(
    authoritative_state: dict[str, Any],
) -> list[dict[str, Any]]:
    active_goal_id = authoritative_state.get("active_goal_id")
    task_stack = authoritative_state.get("task_stack") or {}
    active_task_id = (
        task_stack.get("active_task_id")
        if isinstance(task_stack, dict)
        else None
    )
    result: list[dict[str, Any]] = []
    for source in authoritative_state.get("active_facts") or ():
        if not isinstance(source, dict):
            continue
        if source.get("status") != ConstraintStatus.KNOWN.value:
            continue
        goal_matches = bool(
            active_goal_id is not None and source.get("goal_id") == active_goal_id
        )
        task_matches = bool(
            active_task_id is not None and source.get("task_id") == active_task_id
        )
        unscoped = source.get("goal_id") is None and source.get("task_id") is None
        if (active_goal_id is not None or active_task_id is not None) and not (
            goal_matches or task_matches or unscoped
        ):
            continue
        result.append(dict(source))
    return result


def _promote_unambiguous_constraint_correction(
    repaired: dict[str, Any],
    products: list[dict[str, Any]],
    authoritative_state: dict[str, Any] | None,
    changes: list[str],
) -> None:
    """Promote one unambiguous active-fact change to ``correct``.

    The repair consumes typed facts only.  It never interprets raw customer
    wording, converts units, or chooses between multiple current values.
    """

    operation = str(getattr(repaired.get("operation"), "value", repaired.get("operation")))
    if operation not in {GoalOperation.CONTINUE.value, GoalOperation.REFINE.value}:
        return
    if not isinstance(authoritative_state, dict):
        return
    current_constraints = [
        item
        for item in (repaired.get("constraints") or ())
        if isinstance(item, dict)
        and item.get("status") == ConstraintStatus.KNOWN.value
        and _current_constraint_targets_active_goal(
            item,
            products,
            authoritative_state,
        )
    ]
    active_facts = _active_known_facts(authoritative_state)
    if not current_constraints or not active_facts:
        return

    current_by_name: dict[str, list[dict[str, Any]]] = {}
    active_by_name: dict[str, list[dict[str, Any]]] = {}
    for constraint in current_constraints:
        current_by_name.setdefault(
            _canonical_constraint_fact_name(str(constraint.get("name") or "")),
            [],
        ).append(constraint)
    for fact in active_facts:
        active_by_name.setdefault(
            _canonical_constraint_fact_name(str(fact.get("name") or "")),
            [],
        ).append(fact)

    changed_facts: list[str] = []
    ambiguous = False
    for canonical_name in sorted(set(current_by_name).intersection(active_by_name)):
        current_values = current_by_name[canonical_name]
        active_values = active_by_name[canonical_name]
        if any(_constraint_contains_numeric_range(item) for item in current_values):
            ambiguous = True
            continue
        if len(current_values) != 1 or len(active_values) != 1:
            ambiguous = True
            continue
        current = current_values[0]
        active = active_values[0]
        if not _constraint_value_is_grounded_scalar(current):
            ambiguous = True
            continue
        if not _constraint_units_compatible(current, active):
            ambiguous = True
            continue
        if _constraint_values_equivalent(current, active):
            continue
        changed_facts.append(canonical_name)

    if ambiguous or len(changed_facts) != 1:
        return
    repaired["operation"] = GoalOperation.CORRECT.value
    changes.append("operation_promoted_to_correct_from_active_fact_change")


def _capability_constraint_rule(fact_name: str) -> dict[str, Any] | None:
    """Resolve a capability fact through declarative schema-name aliases."""

    normalized_name = _normalize_schema_identifier(fact_name)
    rules = semantic_ontology_payload().get("capability_constraints") or ()
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        names = {_normalize_schema_identifier(rule.get("canonical_name"))}
        names.update(
            _normalize_schema_identifier(alias)
            for alias in (rule.get("name_aliases") or ())
        )
        if normalized_name in names:
            return rule
    return None


def _capability_positive_value_matches(value: Any, rule: dict[str, Any]) -> bool:
    """Match an explicit capability value without product/text inference."""

    positive_values = rule.get("positive_values") or ()
    value_key = _categorical_scalar_key(value)
    if any(
        value_key == _categorical_scalar_key(candidate)
        for candidate in positive_values
    ):
        return True
    if not isinstance(value, str):
        return False
    normalized_value = _normalize_schema_identifier(value)
    return any(
        isinstance(candidate, str)
        and normalized_value == _normalize_schema_identifier(candidate)
        for candidate in positive_values
    )


def _capability_evidence_is_grounded(
    evidence: str,
    rule: dict[str, Any],
    *,
    aliases_field: str,
) -> bool:
    """Require one declared capability coordinate in the exact evidence.

    This deliberately does not treat a generic existential (for example a
    bare "есть") as stock semantics.  Aliases live in the domain ontology and
    are matched as contiguous source tokens, so both actions and persistent
    facts remain auditable without dialogue-specific phrase checks.
    """

    aliases = rule.get(aliases_field) or ()
    return any(
        isinstance(alias, str)
        and alias.strip()
        and _declared_alias_matches_text(alias, evidence)
        for alias in aliases
    )


def _availability_requirement_polarity(
    evidence: str,
    current_message: str,
    rule: dict[str, Any],
) -> str | None:
    """Classify only an explicitly evidenced durable stock condition.

    A broad availability coordinate proves the subject of the fact, but not
    whether the customer is merely asking for its value.  Durable semantics
    therefore need either a declared in-stock-only coordinate, an explicit
    relaxation, or rejection of a currently unavailable candidate.
    """

    if not _capability_evidence_is_grounded(
        evidence,
        rule,
        aliases_field="constraint_evidence_aliases",
    ):
        return None
    if _AVAILABILITY_RELAXATION_RE.search(current_message):
        return ConstraintPolarity.EXCLUDED.value
    if _capability_evidence_is_grounded(
        current_message,
        rule,
        aliases_field="required_evidence_aliases",
    ):
        return ConstraintPolarity.REQUIRED.value
    if (
        _capability_evidence_is_grounded(
            current_message,
            rule,
            aliases_field="negative_state_evidence_aliases",
        )
        and _UNAVAILABLE_CANDIDATE_REJECTION_RE.search(current_message)
    ):
        return ConstraintPolarity.REQUIRED.value
    return None


def _capability_action_is_grounded(action: str, current_message: str) -> bool:
    """Require a capability-changing act to have declarative turn evidence.

    A generic technical request to "check" something is not evidence for a
    stock filter.  The vocabulary is data-driven in the ontology and does not
    depend on a dialogue, product or SKU.
    """

    rules = semantic_ontology_payload().get("capability_constraints") or ()
    matching_rules = [
        rule
        for rule in rules
        if isinstance(rule, dict) and str(rule.get("action") or "") == action
    ]
    if not matching_rules:
        return True
    return any(
        _capability_evidence_is_grounded(
            current_message,
            rule,
            aliases_field="action_evidence_aliases",
        )
        for rule in matching_rules
    )


def _has_explicit_non_known_status(status: str, evidence: str) -> bool:
    """Return whether *evidence* explicitly supports the proposed status."""

    return any(
        pattern.search(evidence)
        for pattern in _EXPLICIT_NON_KNOWN_PATTERNS.get(status, ())
    )


def _product_family(item: dict[str, Any]) -> str | None:
    canonical_type = _normalize_evidence(str(item.get("canonical_type") or ""))
    category = _normalize_evidence(str(item.get("category") or ""))
    if category == ProductCategory.PUMPS.value or canonical_type.endswith("pump"):
        return "pump"
    if "насос" in canonical_type:
        return "pump"
    if category == ProductCategory.BOILERS.value or canonical_type.endswith("boiler"):
        return "boiler"
    if "котел" in canonical_type or "котёл" in canonical_type:
        return "boiler"
    if category == ProductCategory.PIPES.value and (
        canonical_type in {"pipe", "pex_pipe", "труба"}
        or "pex" in canonical_type
        or "pe-x" in canonical_type
    ):
        return "pipe"
    return None


def _is_technical_characteristic_question(
    current_message: str,
    products: list[dict[str, Any]],
    authoritative_hints: tuple[dict[str, str], ...],
) -> bool:
    """Recognize a typed characteristic follow-up from declarative ontology."""

    if not _DIRECT_QUESTION_RE.search(current_message):
        return False
    canonical_types = {
        _normalize_evidence(str(item.get("canonical_type") or ""))
        for item in (*products, *authoritative_hints)
        if item.get("canonical_type")
    }
    if not canonical_types:
        return False
    vocabulary = semantic_ontology_payload().get("constraint_vocabulary") or {}
    aliases: list[str] = []
    for canonical_type in canonical_types:
        for definition in vocabulary.get(canonical_type, ()):  # type: ignore[arg-type]
            aliases.extend(str(item) for item in definition.get("aliases") or ())
    return any(
        _grounded_evidence_fragment(alias, current_message) is not None
        for alias in aliases
        if alias.strip()
    )


def _authoritative_product_hints(
    state: SessionState,
) -> tuple[dict[str, str], ...]:
    """Return the active typed goal only; legacy prose is never a type hint."""

    typed_state = state.live_dialogue_state_v2 or state.dialogue_state_v2
    if typed_state is None or typed_state.active_goal_id is None:
        return ()
    active_goal = next(
        (
            goal
            for goal in typed_state.product_goals
            if goal.goal_id == typed_state.active_goal_id
        ),
        None,
    )
    if active_goal is None or not active_goal.canonical_type:
        return ()
    return (
        {
            "canonical_type": active_goal.canonical_type,
            "category": str(_enum_value(active_goal.category) or ""),
        },
    )


def _shown_product_cards(state: SessionState) -> tuple[Any, ...]:
    """Return the bounded union of cards actually exposed by either path."""

    unique: dict[str, Any] = {}
    for card in (*state.v2_last_products, *state.last_products):
        normalized_sku = unicodedata.normalize("NFKC", str(card.sku)).casefold()
        if normalized_sku and normalized_sku not in unique:
            unique[normalized_sku] = card
    return tuple(unique.values())


def _identifier_tokens(value: str) -> list[tuple[str, str, int, int]]:
    return [
        (
            unicodedata.normalize("NFKC", match.group(0)).casefold(),
            match.group(0),
            match.start(),
            match.end(),
        )
        for match in _MIXED_IDENTIFIER_TOKEN_RE.finditer(value)
    ]


def _card_matches_identifier(card: Any, normalized_token: str) -> bool:
    normalized_sku = unicodedata.normalize("NFKC", str(card.sku)).casefold()
    if normalized_token == normalized_sku:
        return True
    return any(
        name_token == normalized_token
        for name_token, _raw, _start, _end in _identifier_tokens(str(card.name))
    )


def _exact_sku_mentioned(sku: str, evidence: str) -> bool:
    """Match a full SKU literally; unlike model resolution it may be numeric."""

    normalized_sku = unicodedata.normalize("NFKC", str(sku)).casefold()
    normalized_evidence = unicodedata.normalize("NFKC", evidence).casefold()
    if not normalized_sku:
        return False
    return bool(
        re.search(
            rf"(?<![\w]){re.escape(normalized_sku)}(?![\w])",
            normalized_evidence,
        )
    )


def _shown_identifier_binding(
    products: list[dict[str, Any]],
    authoritative_hints: tuple[dict[str, str], ...],
) -> tuple[bool, int | None]:
    typed_indexes = [
        index
        for index, product in enumerate(products)
        if _product_family(product) is not None
        and str(product.get("role") or "")
        in {
            ProductRole.TARGET.value,
            ProductRole.ALTERNATIVE.value,
            ProductRole.EXISTING.value,
        }
    ]
    if len(typed_indexes) == 1:
        return True, typed_indexes[0]
    if typed_indexes:
        return False, None
    if len(authoritative_hints) == 1 and _product_family(
        authoritative_hints[0]
    ) is not None:
        # An unscoped semantic fact is intentionally rebound only by the typed
        # active goal/task inside the reducer.  No product-name heuristic is
        # used here.
        return True, None
    return False, None


def _resolve_shown_card_identifier_constraints(
    constraints: list[dict[str, Any]],
    products: list[dict[str, Any]],
    current_message: str,
    authoritative_hints: tuple[dict[str, str], ...],
    shown_cards: tuple[Any, ...],
    changes: list[str],
) -> list[dict[str, Any]]:
    """Resolve a model code only against the exact, already-shown card set."""

    if not shown_cards:
        return constraints

    tokens = _identifier_tokens(current_message)
    token_matches: list[tuple[str, str, int, int, tuple[Any, ...]]] = []
    for normalized, raw, start, end in tokens:
        matching_cards = tuple(
            card
            for card in shown_cards
            if _card_matches_identifier(card, normalized)
        )
        token_matches.append((normalized, raw, start, end, matching_cards))

    normalized_constraints: list[dict[str, Any]] = []
    resolved_skus: set[str] = set()
    for constraint in constraints:
        if (
            _normalize_evidence(str(constraint.get("name") or "")) != "sku"
            or constraint.get("status") != ConstraintStatus.KNOWN.value
        ):
            normalized_constraints.append(constraint)
            continue

        evidence = str(constraint.get("evidence") or "")
        evidence_matches: dict[str, Any] = {}
        for _normalized, raw, _start, _end, matching_cards in token_matches:
            if raw not in evidence:
                continue
            for card in matching_cards:
                evidence_matches[str(card.sku)] = card
        for card in shown_cards:
            if _exact_sku_mentioned(str(card.sku), evidence):
                evidence_matches[str(card.sku)] = card

        if len(evidence_matches) != 1:
            changes.append("constraint_shown_card_sku_unverified_dropped")
            continue
        card = next(iter(evidence_matches.values()))
        constraint["value"] = str(card.sku)
        constraint["unit"] = None
        resolved_skus.add(str(card.sku))
        normalized_constraints.append(constraint)
        changes.append("constraint_shown_card_sku_verified")

    ambiguous = any(len(item[4]) > 1 for item in token_matches)
    uniquely_matched = [item for item in token_matches if len(item[4]) == 1]
    matched_by_sku = {
        str(item[4][0].sku): item[4][0] for item in uniquely_matched
    }
    if ambiguous or len(matched_by_sku) > 1:
        changes.append("shown_card_identifier_ambiguous")
        return normalized_constraints
    if not matched_by_sku:
        if tokens:
            changes.append("shown_card_identifier_unmatched")
        return normalized_constraints

    card = next(iter(matched_by_sku.values()))
    if str(card.sku) in resolved_skus:
        return normalized_constraints
    has_binding, product_index = _shown_identifier_binding(
        products,
        authoritative_hints,
    )
    if not has_binding:
        changes.append("shown_card_identifier_unbound")
        return normalized_constraints

    evidence_item = next(
        item for item in uniquely_matched if str(item[4][0].sku) == str(card.sku)
    )
    normalized_constraints.append(
        ConstraintFact(
            name="sku",
            value=str(card.sku),
            unit=None,
            status=ConstraintStatus.KNOWN,
            polarity=ConstraintPolarity.REQUIRED,
            applies_to_product=product_index,
            evidence=current_message[evidence_item[2] : evidence_item[3]],
        ).model_dump(mode="json")
    )
    changes.append("constraint_shown_card_identifier_resolved_to_sku")
    return normalized_constraints


def _number_from_anchor(match: re.Match[str]) -> int | float:
    return _number_from_literal(match.group("value"))


def _number_from_literal(literal: str) -> int | float:
    literal = literal.replace(",", ".")
    value = float(literal)
    return int(value) if value.is_integer() else value


def _numeric_range_value(minimum: int | float, maximum: int | float) -> str:
    return f"{minimum}\u2013{maximum}"


def _parsed_numeric_range_value(
    value: object,
) -> tuple[int | float, int | float] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(
        r"\s*(?P<minimum>[-+]?\d+(?:[.,]\d+)?)\s*"
        r"(?:\.\.|[-\u2013\u2014]|до|to)\s*"
        r"(?P<maximum>[-+]?\d+(?:[.,]\d+)?)\s*"
        r"(?:(?:к\s*вт|kw))?\s*",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    minimum = _number_from_literal(match.group("minimum"))
    maximum = _number_from_literal(match.group("maximum"))
    if float(minimum) > float(maximum):
        return None
    return minimum, maximum


def _anchor_is_maximum(current_message: str, start: int, end: int) -> bool:
    window = current_message[max(0, start - 36) : min(len(current_message), end + 18)]
    return bool(
        re.search(r"\b(?:макс\w*|максимальн\w*|maximum|max)\b", window, re.I)
    )


def _anchor_polarity(current_message: str, start: int, end: int) -> str:
    window = current_message[max(0, start - 64) : min(len(current_message), end + 64)]
    return (
        ConstraintPolarity.PREFERRED.value
        if _EXPLICIT_SOFTENING_RE.search(window)
        else ConstraintPolarity.REQUIRED.value
    )


def _typed_numeric_anchors(current_message: str) -> list[dict[str, Any]]:
    """Extract only unit/shape anchors with an unambiguous engineering type.

    This intentionally does not calculate or convert values.  Every evidence
    fragment is an exact slice of the current message.
    """

    anchors: list[dict[str, Any]] = []

    def add(
        match: re.Match[str],
        *,
        family: str,
        name: str,
        unit_override: str | None = None,
    ) -> None:
        evidence = current_message[match.start() : match.end()].strip()
        anchors.append(
            {
                "family": family,
                "name": name,
                "value": _number_from_anchor(match),
                "unit": unit_override or match.groupdict().get("unit"),
                "evidence": evidence,
                "start": match.start(),
                "end": match.end(),
                "polarity": _anchor_polarity(
                    current_message,
                    match.start(),
                    match.end(),
                ),
            }
        )

    power_range_spans: list[tuple[int, int]] = []
    for match in _POWER_RANGE_ANCHOR_RE.finditer(current_message):
        minimum = _number_from_literal(match.group("minimum"))
        maximum = _number_from_literal(match.group("maximum"))
        if float(minimum) > float(maximum):
            continue
        power_range_spans.append((match.start(), match.end()))
        anchors.append(
            {
                "family": "boiler",
                "name": "power_kw",
                "value": _numeric_range_value(minimum, maximum),
                "unit": match.group("unit"),
                "evidence": current_message[match.start() : match.end()].strip(),
                "start": match.start(),
                "end": match.end(),
                "polarity": _anchor_polarity(
                    current_message,
                    match.start(),
                    match.end(),
                ),
                "range_bounds": (minimum, maximum),
            }
        )
    for match in _POWER_ANCHOR_RE.finditer(current_message):
        if any(
            start <= match.start() and match.end() <= end
            for start, end in power_range_spans
        ):
            continue
        add(match, family="boiler", name="power_kw")
    for match in _FLOW_ANCHOR_RE.finditer(current_message):
        add(
            match,
            family="pump",
            name=(
                "max_flow_l_h"
                if _anchor_is_maximum(current_message, match.start(), match.end())
                else "duty_point_flow_l_h"
            ),
        )
    for match in _PIPE_QUANTITY_ANCHOR_RE.finditer(current_message):
        raw_unit = _normalize_evidence(match.group("unit"))
        window = current_message[
            max(0, match.start() - 40) : min(len(current_message), match.end() + 40)
        ]
        is_head_measurement = bool(
            re.search(r"\b(?:напор\w*|head)\b", window, flags=re.IGNORECASE)
        )
        has_quantity_scope = raw_unit not in {"м", "m"} or bool(
            _PIPE_QUANTITY_CONTEXT_RE.search(window)
        )
        if has_quantity_scope and not is_head_measurement:
            add(
                match,
                family="pipe",
                name="requested_quantity_m",
                unit_override="m",
            )
    for pattern in _HEAD_ANCHOR_RES:
        for match in pattern.finditer(current_message):
            add(
                match,
                family="pump",
                name=(
                    "max_head_m"
                    if _anchor_is_maximum(
                        current_message,
                        match.start(),
                        match.end(),
                    )
                    else "duty_point_head_m"
                ),
            )
    for pattern in _PIPE_DIAMETER_ANCHOR_RES:
        for match in pattern.finditer(current_message):
            add(match, family="pipe", name="diameter_mm")

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, str]] = set()
    for anchor in sorted(
        anchors,
        key=lambda item: (
            item["start"],
            0 if item.get("range_bounds") is not None else 1,
            item["end"],
        ),
    ):
        key = (
            anchor["family"],
            anchor["start"],
            anchor["end"],
            str(anchor["value"]),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(anchor)
    return unique


def _constraint_name_matches_anchor(name: str, anchor_name: str) -> bool:
    normalized = _normalize_evidence(name).replace(" ", "_")
    if anchor_name == "power_kw":
        return normalized in {"power", "power_kw", "boiler_power_kw"} or (
            "мощ" in normalized
        )
    if anchor_name in {"max_flow_l_h", "duty_point_flow_l_h"}:
        return "flow" in normalized or "расход" in normalized or "подач" in normalized
    if anchor_name in {"max_head_m", "duty_point_head_m"}:
        return "head" in normalized or "напор" in normalized
    if anchor_name == "diameter_mm":
        return "diameter" in normalized or "диаметр" in normalized
    if anchor_name == "requested_quantity_m":
        return normalized in {
            "requested_quantity_m",
            "quantity_m",
            "length_quantity_m",
        } or "количеств" in normalized
    return normalized == anchor_name


def _anchor_binding(
    anchor: dict[str, Any],
    products: list[dict[str, Any]],
    authoritative_hints: tuple[dict[str, str], ...],
) -> tuple[bool, int | None, bool]:
    """Return (is_applicable, product index, is_ambiguous)."""

    candidate_indexes = [
        index
        for index, product in enumerate(products)
        if _product_family(product) == anchor["family"]
    ]
    if len(candidate_indexes) == 1:
        return True, candidate_indexes[0], False
    if len(candidate_indexes) > 1:
        evidence_indexes = [
            index
            for index in candidate_indexes
            if _normalize_evidence(anchor["evidence"])
            in _normalize_evidence(str(products[index].get("evidence") or ""))
        ]
        if len(evidence_indexes) == 1:
            return True, evidence_indexes[0], False
        return True, None, True

    hint_families = [
        _product_family(hint)
        for hint in authoritative_hints
        if _product_family(hint) is not None
    ]
    if hint_families.count(anchor["family"]) == 1:
        return True, None, False
    if hint_families.count(anchor["family"]) > 1:
        return True, None, True
    return False, None, False


def _recover_typed_numeric_constraints(
    constraints: list[dict[str, Any]],
    products: list[dict[str, Any]],
    current_message: str,
    authoritative_hints: tuple[dict[str, str], ...],
    changes: list[str],
) -> list[dict[str, Any]]:
    """Recover exact typed anchors omitted or mistyped by the semantic model."""

    recovered = list(constraints)
    for anchor in _typed_numeric_anchors(current_message):
        applicable, product_index, ambiguous = _anchor_binding(
            anchor,
            products,
            authoritative_hints,
        )
        if not applicable:
            continue
        if ambiguous:
            changes.append("typed_numeric_anchor_ambiguous")
            continue

        range_bounds = anchor.get("range_bounds")
        if range_bounds is not None:
            matching_range_indexes: list[int] = []
            endpoint_indexes: list[int] = []
            for index, constraint in enumerate(recovered):
                if constraint.get("status") != ConstraintStatus.KNOWN.value:
                    continue
                if not _constraint_name_matches_anchor(
                    str(constraint.get("name") or ""),
                    anchor["name"],
                ):
                    continue
                binding = constraint.get("applies_to_product")
                if product_index is not None and binding not in {None, product_index}:
                    continue
                if product_index is None and binding is not None:
                    continue
                parsed_range = _parsed_numeric_range_value(
                    constraint.get("value")
                )
                if parsed_range is not None and all(
                    math.isclose(float(left), float(right), abs_tol=1e-9)
                    for left, right in zip(parsed_range, range_bounds)
                ):
                    matching_range_indexes.append(index)
                    continue
                value = constraint.get("value")
                if (
                    not isinstance(value, bool)
                    and isinstance(value, (int, float))
                    and any(
                        math.isclose(float(value), float(bound), abs_tol=1e-9)
                        for bound in range_bounds
                    )
                ):
                    endpoint_indexes.append(index)

            if len(matching_range_indexes) > 1:
                changes.append("typed_numeric_range_anchor_ambiguous")
                continue
            if matching_range_indexes:
                keep_index = matching_range_indexes[0]
                canonical = dict(recovered[keep_index])
                canonical.update(
                    {
                        "name": anchor["name"],
                        "value": anchor["value"],
                        "unit": anchor.get("unit"),
                        "polarity": anchor["polarity"],
                        "applies_to_product": product_index,
                        "evidence": anchor["evidence"],
                    }
                )
                drop = set(endpoint_indexes)
                recovered = [
                    canonical if index == keep_index else constraint
                    for index, constraint in enumerate(recovered)
                    if index not in drop
                ]
                changes.append("constraint_numeric_range_anchor_canonicalized")
                if endpoint_indexes:
                    changes.append("constraint_numeric_range_endpoints_collapsed")
                continue

            # A typed interval is one customer fact.  Endpoint proposals from
            # the model are an alternate serialization of that same fact, not
            # two independent values for the reducer to resolve arbitrarily.
            drop = set(endpoint_indexes)
            if drop:
                recovered = [
                    constraint
                    for index, constraint in enumerate(recovered)
                    if index not in drop
                ]
                changes.append("constraint_numeric_range_endpoints_collapsed")
            recovered.append(
                ConstraintFact(
                    name=anchor["name"],
                    value=anchor["value"],
                    unit=anchor.get("unit"),
                    status=ConstraintStatus.KNOWN,
                    polarity=anchor["polarity"],
                    applies_to_product=product_index,
                    evidence=anchor["evidence"],
                ).model_dump(mode="json")
            )
            changes.append("constraint_typed_numeric_range_recovered")
            continue

        matching_indexes: list[int] = []
        existing_typed_indexes: list[int] = []
        for index, constraint in enumerate(recovered):
            if constraint.get("status") != ConstraintStatus.KNOWN.value:
                continue
            if not _constraint_name_matches_anchor(
                str(constraint.get("name") or ""),
                anchor["name"],
            ):
                continue
            binding = constraint.get("applies_to_product")
            if product_index is not None and binding not in {None, product_index}:
                continue
            if product_index is None and binding is not None:
                continue
            existing_typed_indexes.append(index)
            numeric_value = _constraint_numeric_scalar(constraint)
            if numeric_value is None or not math.isclose(
                numeric_value,
                float(anchor["value"]),
                abs_tol=1e-9,
            ):
                continue
            if _normalize_evidence(anchor["evidence"]) not in _normalize_evidence(
                str(constraint.get("evidence") or "")
            ):
                continue
            matching_indexes.append(index)

        if len(matching_indexes) == 1:
            index = matching_indexes[0]
            if recovered[index].get("name") != anchor["name"]:
                recovered[index]["name"] = anchor["name"]
                changes.append("constraint_numeric_anchor_name_canonicalized")
            if recovered[index].get("value") != anchor["value"]:
                recovered[index]["value"] = anchor["value"]
                changes.append("constraint_numeric_anchor_value_canonicalized")
            exact_unit = anchor.get("unit")
            if exact_unit and recovered[index].get("unit") != exact_unit:
                recovered[index]["unit"] = exact_unit
                changes.append("constraint_numeric_anchor_unit_recovered")
            continue
        if matching_indexes:
            changes.append("typed_numeric_anchor_ambiguous")
            continue
        if existing_typed_indexes:
            # Anchors recover a fact omitted by the semantic model.  A second
            # number in the same sentence may be a delta, comparison or source
            # value and must not compete with an already grounded target fact.
            changes.append("typed_numeric_anchor_skipped_model_fact_present")
            continue
        recovered.append(
            ConstraintFact(
                name=anchor["name"],
                value=anchor["value"],
                unit=anchor.get("unit"),
                status=ConstraintStatus.KNOWN,
                polarity=anchor["polarity"],
                applies_to_product=product_index,
                evidence=anchor["evidence"],
            ).model_dump(mode="json")
        )
        changes.append("constraint_typed_numeric_anchor_recovered")
    return recovered


_CIRCULATION_PUMP_CANONICAL_TYPES = frozenset(
    {"circulation pump", "циркуляционный насос"}
)
_PUMP_DESIGNATION_ROLES = frozenset(
    {
        ProductRole.TARGET.value,
        ProductRole.EXISTING.value,
        ProductRole.ALTERNATIVE.value,
    }
)


def _recover_circulation_pump_designation_constraints(
    constraints: list[dict[str, Any]],
    products: list[dict[str, Any]],
    changes: list[str],
) -> list[dict[str, Any]]:
    """Recover facts encoded in an already typed circulation-pump model.

    Product typing and evidence grounding happen before this helper.  The
    deterministic parser only decodes the established size code inside that
    exact evidence; it never classifies a product from raw text.  Any same-name
    fact already present in the product scope wins regardless of status, so an
    LLM proposal or explicit ``unknown/refused/deferred`` fact is not silently
    overwritten.
    """

    recovered = list(constraints)
    for product_index, product in enumerate(products):
        canonical_type = _normalize_evidence(
            str(product.get("canonical_type") or "")
        ).replace("_", " ")
        if canonical_type not in _CIRCULATION_PUMP_CANONICAL_TYPES:
            continue
        if str(product.get("role") or "") not in _PUMP_DESIGNATION_ROLES:
            continue

        designation_facts = parse_pump_designation(product.get("evidence"))
        for fact_name, (value, unit, evidence) in designation_facts.items():
            already_present = any(
                _normalize_evidence(str(item.get("name") or "")).replace(" ", "_")
                == fact_name
                and item.get("applies_to_product") in {None, product_index}
                for item in recovered
            )
            if already_present:
                changes.append("pump_designation_existing_constraint_preserved")
                continue
            recovered.append(
                ConstraintFact(
                    name=fact_name,
                    value=value,
                    unit=unit,
                    status=ConstraintStatus.KNOWN,
                    polarity=ConstraintPolarity.REQUIRED,
                    applies_to_product=product_index,
                    evidence=evidence,
                ).model_dump(mode="json")
            )
            changes.append("pump_designation_constraint_recovered")
    return recovered


def validate_current_turn_evidence(
    understanding: TurnUnderstanding,
    current_message: str,
) -> None:
    """Reject facts that cannot be traced to the current customer message."""

    normalized_message = _normalize_evidence(current_message)
    evidence_values = [
        *(item.evidence for item in understanding.products),
        *(item.evidence for item in understanding.constraints),
        *(item.evidence for item in understanding.references),
        *(item.evidence for item in understanding.ambiguities),
        *(item.evidence for item in understanding.workflow_controls),
        *(item.evidence for item in understanding.information_requests),
    ]
    for evidence in evidence_values:
        normalized = _normalize_evidence(evidence)
        if not normalized or normalized not in normalized_message:
            raise ValueError(f"evidence is absent from current_message: {evidence!r}")


_PRODUCT_DIRECTED_ACTS = frozenset(
    {
        CustomerAct.FIND,
        CustomerAct.SELECT,
        CustomerAct.COMPARE,
        CustomerAct.CHECK_PRICE,
        CustomerAct.CHECK_STOCK,
        CustomerAct.GET_LINK,
        CustomerAct.REQUEST_QUOTE,
        CustomerAct.RESERVE_PRODUCT,
        CustomerAct.PLACE_ORDER,
    }
)
_CONTENT_COVERAGE_STOP_WORDS = _EVIDENCE_STOP_WORDS.difference(
    {"need", "нужен", "нужна", "нужно"}
)
_ACKNOWLEDGEMENT_TOKENS = frozenset(
    {
        "yes",
        "ok",
        "okay",
        "thanks",
        "understood",
        "da",
        "horosho",
        "ponyatno",
        "spasibo",
        "да",
        "ладно",
        "ок",
        "понял",
        "поняла",
        "понятно",
        "спасибо",
        "хорошо",
    }
)


def validate_semantic_content_coverage(
    understanding: TurnUnderstanding,
    current_message: str,
    structural_repairs: tuple[str, ...],
) -> None:
    """Reject lossy empty frames while allowing genuinely short replies."""

    if "typed_numeric_anchor_ambiguous" in structural_repairs:
        raise ValueError("typed numeric anchor has ambiguous product binding")

    if "target_product_missing_canonical_type_dropped" in structural_repairs:
        has_typed_target = any(
            product.role == ProductRole.TARGET and product.canonical_type
            for product in understanding.products
        )
        product_action_requested = any(
            act in _PRODUCT_DIRECTED_ACTS for act in understanding.acts
        )
        independent_action_remains = any(
            act not in _PRODUCT_DIRECTED_ACTS for act in understanding.acts
        )
        if not has_typed_target and (
            product_action_requested or not independent_action_remains
        ):
            raise ValueError(
                "target product is missing canonical_type after safe repair"
            )

    has_semantic_content = any(
        (
            understanding.acts,
            understanding.products,
            understanding.constraints,
            understanding.references,
            understanding.ambiguities,
            understanding.workflow_controls,
        )
    ) or understanding.answers_pending_question
    if has_semantic_content:
        return

    message_tokens = [item[0] for item in _source_tokens(current_message)]
    if message_tokens and all(
        item in _ACKNOWLEDGEMENT_TOKENS for item in message_tokens
    ):
        return
    content_tokens = [
        item for item in message_tokens if item not in _CONTENT_COVERAGE_STOP_WORDS
    ]
    # Acknowledgements such as one or two short words are valid empty continue
    # frames.  A multi-token substantive message is not: accepting it would
    # silently discard a request or correction before the reducer sees it.
    if len(content_tokens) >= 2 or len(message_tokens) >= 6:
        raise ValueError("contentful current_message reduced to empty semantic frame")


def validate_product_modifier_coverage(
    understanding: TurnUnderstanding,
) -> None:
    """Ensure numeric modifiers inside a product mention are not discarded.

    The gate does not interpret a designation or guess which engineering field
    a number belongs to.  It merely requires the LLM to preserve every explicit
    standalone numeric anchor in at least one typed constraint, where the
    contract-scoped canonicalizer can process it deterministically later.
    """

    missing: list[str] = []
    for product_index, product in enumerate(understanding.products):
        if product.role not in {
            ProductRole.TARGET,
            ProductRole.EXISTING,
            ProductRole.ALTERNATIVE,
        }:
            continue
        evidence = product.evidence
        # Model/designation numbers identify a typed product rather than state
        # an engineering constraint.  Preserve them in the exact product
        # evidence, but do not force the LLM to guess their internal meaning.
        # The check is deliberately syntactic: an alphanumeric Latin token
        # (ALPHA2) or a Latin-labelled multi-part designation (30/1-8).
        exempt_spans = [
            match.span() for match in _LATIN_ALPHANUMERIC_IDENTIFIER_RE.finditer(evidence)
        ]
        if _LATIN_WORD_RE.search(evidence):
            exempt_spans.extend(
                match.span() for match in _STRUCTURED_MODEL_NUMBER_RE.finditer(evidence)
            )
        constraint_numeric_anchors = {
            token
            for constraint in understanding.constraints
            if constraint.applies_to_product == product_index
            or (
                len(understanding.products) == 1
                and constraint.applies_to_product is None
            )
            for token, _start, _end in _source_tokens(constraint.evidence)
            if token.isdigit()
        }
        for token, start, end in _source_tokens(evidence):
            inside_model_designation = any(
                start >= exempt_start and end <= exempt_end
                for exempt_start, exempt_end in exempt_spans
            )
            if (
                token.isdigit()
                and not inside_model_designation
                and token not in constraint_numeric_anchors
            ):
                missing.append(token)
    if missing:
        raise ValueError(
            "explicit product modifier missing from constraints: "
            + ",".join(dict.fromkeys(missing))
        )


def repair_structural_enum_placement(
    raw: Any,
    current_message: str,
) -> tuple[Any, tuple[str, ...]]:
    """Repair only misplaced known enums; never infer a speech act from text."""

    if not isinstance(raw, dict):
        return raw, ()
    repaired = deepcopy(raw)
    acts = list(repaired.get("acts") or [])
    controls = list(repaired.get("workflow_controls") or [])
    operation = repaired.get("operation")
    act_values = {item.value for item in CustomerAct}
    control_values = {item.value for item in WorkflowControlKind}
    operation_values = {item.value for item in GoalOperation}
    evidence = str(current_message or "")[:240]
    changes: list[str] = []

    if operation in control_values:
        controls.append({"kind": operation, "evidence": evidence})
        repaired["operation"] = GoalOperation.CONTINUE.value
        changes.append("operation_control_moved_to_workflow_controls")
    elif operation in act_values and operation not in operation_values:
        acts.append(operation)
        repaired["operation"] = GoalOperation.UNKNOWN.value
        changes.append("operation_act_moved_to_acts")

    normalized_acts: list[Any] = []
    for item in acts:
        value = getattr(item, "value", item)
        if value in control_values:
            controls.append({"kind": value, "evidence": evidence})
            changes.append("act_control_moved_to_workflow_controls")
        else:
            normalized_acts.append(item)

    normalized_controls: list[Any] = []
    for item in controls:
        kind = item.get("kind") if isinstance(item, dict) else None
        if kind in act_values:
            normalized_acts.append(kind)
            changes.append("workflow_control_act_moved_to_acts")
        else:
            normalized_controls.append(item)

    repaired["acts"] = list(dict.fromkeys(normalized_acts))
    unique_controls: list[Any] = []
    seen_controls: set[tuple[object, object]] = set()
    for item in normalized_controls:
        key = (
            item.get("kind") if isinstance(item, dict) else None,
            item.get("evidence") if isinstance(item, dict) else None,
        )
        if key in seen_controls:
            continue
        seen_controls.add(key)
        unique_controls.append(item)
    repaired["workflow_controls"] = unique_controls
    return repaired, tuple(dict.fromkeys(changes))


def _repair_information_requests(
    raw_requests: Any,
    *,
    current_message: str,
    normalized_acts: set[str],
    product_index_map: dict[int, int],
    stale_typed_product_indexes: set[int],
    raw_products: Any,
    normalized_products: list[Any],
    changes: list[str],
) -> list[dict[str, Any]]:
    """Narrow unsafe information-request proposals without inferring them."""

    if raw_requests is None:
        return []
    if not isinstance(raw_requests, list):
        changes.append("information_requests_invalid_collection_dropped")
        return []

    allowed_acts = {item.value for item in InformationRequestAct}
    normalized: list[dict[str, Any]] = []
    for raw_item in raw_requests:
        if not isinstance(raw_item, dict):
            changes.append("information_request_invalid_schema_dropped")
            continue
        item = raw_item
        evidence = item.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            changes.append("information_request_invalid_evidence_dropped")
            continue
        grounded = _grounded_evidence_fragment(evidence, current_message)
        if grounded is None:
            changes.append("information_request_ungrounded_evidence_dropped")
            continue
        if grounded != evidence:
            item["evidence"] = grounded
            changes.append(
                "information_request_evidence_rebound_to_current_message"
            )

        act = getattr(item.get("act"), "value", item.get("act"))
        if not isinstance(act, str) or act not in allowed_acts:
            changes.append("information_request_invalid_act_dropped")
            continue
        item["act"] = act
        if act not in normalized_acts:
            changes.append("information_request_missing_turn_act_dropped")
            continue

        raw_outputs = item.get("requested_outputs")
        if isinstance(raw_outputs, list):
            output_values = {
                str(getattr(output, "value", output)) for output in raw_outputs
            }
            if (
                RequestedInformationOutput.VERIFIED_LINK.value in output_values
                and item.get("source_kind") is None
            ):
                changes.append(
                    "information_request_verified_link_without_source_dropped"
                )
                continue
            purpose = str(
                getattr(item.get("purpose"), "value", item.get("purpose"))
            )
            if (
                purpose == InformationPurpose.PROVENANCE.value
                and RequestedInformationOutput.VERIFIED_LINK.value
                not in output_values
            ):
                changes.append(
                    "information_request_provenance_without_verified_link_dropped"
                )
                continue

        product_index = item.get("applies_to_product")
        if (
            isinstance(product_index, int)
            and not isinstance(product_index, bool)
            and product_index in product_index_map
        ):
            rebound_index = product_index_map[product_index]
            if rebound_index != product_index:
                item["applies_to_product"] = rebound_index
                changes.append("information_request_product_binding_reindexed")
        elif (
            isinstance(product_index, int)
            and not isinstance(product_index, bool)
            and product_index in stale_typed_product_indexes
        ):
            item["applies_to_product"] = None
            changes.append("information_request_stale_product_binding_detached")
        elif (
            isinstance(product_index, int)
            and not isinstance(product_index, bool)
            and isinstance(raw_products, list)
            and 0 <= product_index < len(raw_products)
        ):
            # The referenced mention was dropped as untyped.  With no reliable
            # target scope, discarding the request is safer than rebinding it.
            changes.append("information_request_untyped_product_binding_dropped")
            continue
        elif product_index is not None and (
            isinstance(product_index, bool)
            or not isinstance(product_index, int)
            or product_index < 0
            or product_index >= len(normalized_products)
        ):
            changes.append("information_request_invalid_product_binding_dropped")
            continue

        try:
            validated = InformationRequest.model_validate(item)
        except (ValidationError, ValueError, TypeError):
            changes.append("information_request_invalid_schema_dropped")
            continue
        normalized.append(validated.model_dump(mode="json"))
    return normalized


def repair_grounded_semantic_payload(
    raw: Any,
    current_message: str,
    authoritative_product_hints: tuple[dict[str, str], ...] = (),
    shown_product_cards: tuple[Any, ...] = (),
    authoritative_dialogue_state: dict[str, Any] | None = None,
) -> tuple[Any, tuple[str, ...]]:
    """Apply bounded, source-preserving repairs before strict validation.

    Repairs normally narrow or detach unsafe proposals.  Additive repairs are
    limited to typed numeric anchors copied exactly from this turn and typed
    ambiguities that explain why an incompatible unit proposal was discarded.
    """

    repaired, placement_repairs = repair_structural_enum_placement(
        raw,
        current_message,
    )
    if not isinstance(repaired, dict):
        return repaired, placement_repairs
    changes = list(placement_repairs)
    operation = getattr(
        repaired.get("operation"),
        "value",
        repaired.get("operation"),
    )
    typed_characteristic_followup = _is_technical_characteristic_question(
        current_message,
        [],
        authoritative_product_hints,
    )
    if (
        typed_characteristic_followup
        and authoritative_product_hints
        and operation in {GoalOperation.NEW.value, GoalOperation.UNKNOWN.value}
    ):
        repaired["operation"] = GoalOperation.CONTINUE.value
        operation = GoalOperation.CONTINUE.value
        changes.append("typed_characteristic_question_rebound_to_active_goal")
    stale_product_may_be_inherited = operation in {
        GoalOperation.CONTINUE.value,
        GoalOperation.REFINE.value,
        GoalOperation.CORRECT.value,
        GoalOperation.RETURN.value,
    }

    raw_products = repaired.get("products")
    product_index_map: dict[int, int] = {}
    stale_typed_product_indexes: set[int] = set()
    normalized_products: list[Any] = []
    if isinstance(raw_products, list):
        for old_index, item in enumerate(raw_products):
            if not isinstance(item, dict):
                changes.append("product_invalid_schema_dropped")
                continue
            canonical_type = item.get("canonical_type")
            if not isinstance(canonical_type, str) or not canonical_type.strip():
                repair_code = (
                    "target_product_missing_canonical_type_dropped"
                    if item.get("role") == ProductRole.TARGET.value
                    else "product_missing_canonical_type_dropped"
                )
                changes.append(repair_code)
                continue
            evidence = item.get("evidence")
            grounded: str | None = None
            if isinstance(evidence, str) and evidence.strip():
                grounded = _grounded_evidence_fragment(evidence, current_message)
                if grounded is not None and grounded != evidence:
                    item["evidence"] = grounded
                    changes.append("product_evidence_rebound_to_current_message")
            if grounded is None and stale_product_may_be_inherited:
                stale_typed_product_indexes.add(old_index)
                changes.append("stale_typed_product_evidence_dropped")
                continue
            product_index_map[old_index] = len(normalized_products)
            normalized_products.append(item)
        repaired["products"] = normalized_products

    normalized_acts = [
        str(getattr(item, "value", item)) for item in (repaired.get("acts") or [])
    ]
    capability_actions = {
        str(rule.get("action") or "")
        for rule in semantic_ontology_payload().get("capability_constraints") or ()
        if isinstance(rule, dict) and rule.get("action")
    }
    for capability_action in capability_actions:
        if (
            capability_action in normalized_acts
            and not _capability_action_is_grounded(
                capability_action,
                current_message,
            )
        ):
            normalized_acts = [
                item for item in normalized_acts if item != capability_action
            ]
            changes.append("capability_action_without_turn_evidence_dropped")
    has_typed_product_scope = bool(
        any(_product_family(item) is not None for item in normalized_products)
        or authoritative_product_hints
    )
    if (
        CustomerAct.CHECK_DELIVERY.value in normalized_acts
        and not _DELIVERY_SCOPE_RE.search(current_message)
    ):
        normalized_acts = [
            item
            for item in normalized_acts
            if item != CustomerAct.CHECK_DELIVERY.value
        ]
        if has_typed_product_scope:
            normalized_acts.append(CustomerAct.EXPLAIN.value)
            changes.append("delivery_act_reclassified_as_technical_explain")
        else:
            changes.append("delivery_act_without_explicit_scope_dropped")

    typed_characteristic_followup = typed_characteristic_followup or (
        _is_technical_characteristic_question(
            current_message,
            normalized_products,
            authoritative_product_hints,
        )
    )
    if (
        typed_characteristic_followup
        and CustomerAct.EXPLAIN.value not in normalized_acts
    ):
        normalized_acts.append(CustomerAct.EXPLAIN.value)
        changes.append("typed_characteristic_question_explain_act_added")
    repaired["acts"] = list(dict.fromkeys(normalized_acts))
    acts = set(repaired["acts"])
    replacement_requested = bool(
        acts.intersection({CustomerAct.FIND.value, CustomerAct.SELECT.value})
        and _EXPLICIT_REPLACEMENT_RE.search(current_message)
        and not _NEGATED_REPLACEMENT_RE.search(current_message)
    )
    if replacement_requested and not any(
        str(item.get("role") or "")
        in {ProductRole.TARGET.value, ProductRole.ALTERNATIVE.value}
        for item in normalized_products
        if isinstance(item, dict)
    ):
        replacement_candidates = [
            item
            for item in normalized_products
            if isinstance(item, dict)
            and str(item.get("role") or "")
            in {ProductRole.EXISTING.value, ProductRole.CONTEXT.value}
        ]
        if len(replacement_candidates) == 1:
            replacement_candidates[0]["role"] = ProductRole.ALTERNATIVE.value
            changes.append("replacement_product_role_promoted_to_alternative")

    repaired["information_requests"] = _repair_information_requests(
        repaired.get("information_requests"),
        current_message=current_message,
        normalized_acts=acts,
        product_index_map=product_index_map,
        stale_typed_product_indexes=stale_typed_product_indexes,
        raw_products=raw_products,
        normalized_products=normalized_products,
        changes=changes,
    )

    collection_labels = {
        "references": "reference",
        "ambiguities": "ambiguity",
        "workflow_controls": "workflow_control",
    }
    for collection, label in collection_labels.items():
        items = repaired.get(collection)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            evidence = item.get("evidence")
            if not isinstance(evidence, str) or not evidence.strip():
                continue
            grounded = _grounded_evidence_fragment(evidence, current_message)
            if grounded is not None and grounded != evidence:
                item["evidence"] = grounded
                changes.append(f"{label}_evidence_rebound_to_current_message")

    raw_constraints = repaired.get("constraints")
    normalized_constraints: list[dict[str, Any]] = []
    pending_softenings: list[tuple[str, int | None, str]] = []
    unit_ambiguities: list[dict[str, Any]] = []
    categorical_ambiguities: list[dict[str, Any]] = []
    if isinstance(raw_constraints, list):
        valid_statuses = {item.value for item in ConstraintStatus}
        valid_polarities = {item.value for item in ConstraintPolarity}
        for item in raw_constraints:
            if not isinstance(item, dict):
                changes.append("constraint_invalid_schema_dropped")
                continue

            evidence = item.get("evidence")
            if not isinstance(evidence, str) or not evidence.strip():
                changes.append("constraint_invalid_evidence_dropped")
                continue
            grounded = _grounded_evidence_fragment(evidence, current_message)
            if grounded is None:
                # A stale fact copied from dialogue history must not poison an
                # otherwise useful current-turn interpretation.
                changes.append("constraint_ungrounded_evidence_dropped")
                continue
            if grounded != evidence:
                item["evidence"] = grounded
                changes.append("constraint_evidence_rebound_to_current_message")

            prevalidated_capability_rule = _capability_constraint_rule(
                str(item.get("name") or "")
            )
            availability_polarity: str | None = None
            if (
                prevalidated_capability_rule is not None
                and _normalize_schema_identifier(
                    prevalidated_capability_rule.get("canonical_name")
                )
                == "stock_availability"
                and prevalidated_capability_rule.get(
                    "retain_as_typed_requirement", False
                )
            ):
                availability_polarity = _availability_requirement_polarity(
                    str(item.get("evidence") or ""),
                    current_message,
                    prevalidated_capability_rule,
                )
                if availability_polarity is None:
                    changes.append(
                        "availability_constraint_without_durable_requirement_dropped"
                        if _capability_evidence_is_grounded(
                            str(item.get("evidence") or ""),
                            prevalidated_capability_rule,
                            aliases_field="constraint_evidence_aliases",
                        )
                        else "capability_constraint_without_turn_evidence_dropped"
                    )
                    continue
                # Strong current-turn evidence repairs enum drift from the LLM
                # and disambiguates rejection of an unavailable candidate from
                # permission to include one.  No catalogue value is inferred.
                if item.get("status") != ConstraintStatus.KNOWN.value:
                    changes.append("availability_requirement_status_repaired")
                if item.get("polarity") != availability_polarity:
                    changes.append("availability_requirement_polarity_repaired")
                if availability_polarity == ConstraintPolarity.EXCLUDED.value:
                    changes.append(
                        "availability_relaxation_canonicalized_to_excluded"
                    )
                item["status"] = ConstraintStatus.KNOWN.value
                item["polarity"] = availability_polarity
                item["value"] = True

            status = item.get("status", ConstraintStatus.KNOWN.value)
            if isinstance(status, str):
                normalized_status = status.strip().casefold()
                if normalized_status in valid_statuses:
                    if normalized_status != status:
                        item["status"] = normalized_status
                        changes.append("constraint_status_enum_normalized")
                else:
                    changes.append("constraint_invalid_status_dropped")
                    continue
            else:
                changes.append("constraint_invalid_status_dropped")
                continue

            polarity = item.get("polarity", ConstraintPolarity.REQUIRED.value)
            if isinstance(polarity, str):
                normalized_polarity = polarity.strip().casefold()
                if normalized_polarity in valid_polarities:
                    if normalized_polarity != polarity:
                        item["polarity"] = normalized_polarity
                        changes.append("constraint_polarity_enum_normalized")
                else:
                    changes.append("constraint_invalid_polarity_dropped")
                    continue
            else:
                changes.append("constraint_invalid_polarity_dropped")
                continue

            value = item.get("value")
            numeric_string = _parsed_numeric_string(value)
            numeric_schema = _numeric_constraint_schema(
                str(item.get("name") or "")
            )
            known_numeric_scalar = bool(
                normalized_status == ConstraintStatus.KNOWN.value
                and isinstance(value, (int, float))
                and not isinstance(value, bool)
            )
            known_numeric_string = bool(
                normalized_status == ConstraintStatus.KNOWN.value
                and numeric_string is not None
                and numeric_schema is not None
            )
            if known_numeric_string:
                numeric_string_issue = _numeric_string_grounding_issue(item)
                if numeric_string_issue is not None:
                    changes.append(numeric_string_issue)
                    continue

            if known_numeric_scalar or known_numeric_string:
                if known_numeric_scalar:
                    evidence_numbers = _explicit_numeric_values(item["evidence"])
                    numeric_fact_family = (
                        numeric_schema.get("unit_family")
                        if numeric_schema is not None
                        else _numeric_fact_unit_family(
                            str(item.get("name") or "")
                        )
                    )
                    if numeric_fact_family is not None and not any(
                        math.isclose(
                            float(value),
                            evidence_number,
                            rel_tol=1e-9,
                            abs_tol=1e-9,
                        )
                        for evidence_number in evidence_numbers
                    ):
                        changes.append(
                            "constraint_numeric_value_not_in_evidence_dropped"
                        )
                        continue
                unit_incompatibility = _numeric_constraint_unit_incompatibility(
                    item
                )
                if unit_incompatibility is not None:
                    expected_family, observed_families = unit_incompatibility
                    observed_text = ", ".join(observed_families)
                    unit_ambiguities.append(
                        TurnAmbiguity(
                            kind="constraint_unit_incompatible",
                            description=(
                                f"Numeric fact {item.get('name')!s} was not applied: "
                                f"unit family {observed_text} is incompatible with "
                                f"{expected_family}."
                            ),
                            evidence=str(item["evidence"]),
                        ).model_dump(mode="json")
                    )
                    changes.append("constraint_incompatible_unit_dropped")
                    continue
                if known_numeric_string and _numeric_string_declared_unit_is_ungrounded(
                    item
                ):
                    unit_ambiguities.append(
                        TurnAmbiguity(
                            kind="constraint_unit_not_grounded",
                            description=(
                                f"Numeric fact {item.get('name')!s} was not applied: "
                                "its declared unit is absent from the exact "
                                "current-turn evidence."
                            ),
                            evidence=str(item["evidence"]),
                        ).model_dump(mode="json")
                    )
                    changes.append(
                        "constraint_numeric_unit_not_in_evidence_dropped"
                    )
                    continue

            categorical_issue = _closed_categorical_constraint_issue(item)
            if categorical_issue is not None:
                ambiguity_kind, repair_code = categorical_issue
                categorical_ambiguities.append(
                    TurnAmbiguity(
                        kind=ambiguity_kind,
                        description=(
                            f"Known categorical fact {item.get('name')!s} was not "
                            "applied because its proposed value is not supported "
                            "by an approved value alias in the exact evidence."
                        ),
                        evidence=str(item["evidence"]),
                    ).model_dump(mode="json")
                )
                changes.append(repair_code)
                continue

            product_index = item.get("applies_to_product")
            if (
                isinstance(product_index, int)
                and not isinstance(product_index, bool)
                and product_index in product_index_map
            ):
                rebound_index = product_index_map[product_index]
                if rebound_index != product_index:
                    item["applies_to_product"] = rebound_index
                    changes.append("constraint_product_binding_reindexed")
            elif (
                isinstance(product_index, int)
                and not isinstance(product_index, bool)
                and product_index in stale_typed_product_indexes
            ):
                # A follow-up LLM may repeat the inherited active product with
                # stale evidence from history.  The product proposal is unsafe,
                # but a separately grounded current-turn fact remains useful to
                # the already typed V2 goal, so detach rather than discard it.
                item["applies_to_product"] = None
                changes.append("constraint_stale_product_binding_detached")
            elif (
                isinstance(product_index, int)
                and not isinstance(product_index, bool)
                and isinstance(raw_products, list)
                and 0 <= product_index < len(raw_products)
            ):
                # The explicitly referenced product was dropped because it was
                # untyped.  Detaching this fact could make it bind to a
                # different active goal, so discard the fact as well.
                changes.append("constraint_untyped_product_binding_dropped")
                continue
            elif product_index is not None and (
                isinstance(product_index, bool)
                or not isinstance(product_index, int)
                or product_index < 0
                or product_index >= len(normalized_products)
            ):
                item["applies_to_product"] = None
                changes.append("constraint_invalid_product_binding_removed")

            if normalized_status != ConstraintStatus.KNOWN.value and not (
                _has_explicit_non_known_status(
                    normalized_status,
                    str(item.get("evidence") or ""),
                )
            ):
                if (
                    normalized_status == ConstraintStatus.UNKNOWN.value
                    and normalized_polarity == ConstraintPolarity.PREFERRED.value
                    and _EXPLICIT_SOFTENING_RE.search(
                        str(item.get("evidence") or "")
                    )
                ):
                    pending_softenings.append(
                        (
                            str(item.get("name") or ""),
                            item.get("applies_to_product"),
                            str(item.get("evidence") or ""),
                        )
                    )
                changes.append("constraint_non_known_without_explicit_status_dropped")
                continue

            try:
                validated_constraint = ConstraintFact.model_validate(item)
            except (ValidationError, ValueError, TypeError):
                changes.append("constraint_invalid_schema_dropped")
                continue
            capability_rule = _capability_constraint_rule(
                validated_constraint.name
            )
            if capability_rule is not None:
                canonical_name = _normalize_schema_identifier(
                    capability_rule.get("canonical_name")
                )
                if (
                    _normalize_schema_identifier(validated_constraint.name)
                    != canonical_name
                ):
                    changes.append("capability_constraint_schema_alias_normalized")
                if (
                    validated_constraint.status == ConstraintStatus.KNOWN
                    and capability_rule.get("retain_as_typed_requirement", False)
                    and not _capability_evidence_is_grounded(
                        validated_constraint.evidence,
                        capability_rule,
                        aliases_field="constraint_evidence_aliases",
                    )
                ):
                    changes.append(
                        "capability_constraint_without_turn_evidence_dropped"
                    )
                    continue
                action = str(capability_rule.get("action") or "")
                if (
                    validated_constraint.status == ConstraintStatus.KNOWN
                    and validated_constraint.polarity
                    != ConstraintPolarity.EXCLUDED
                    and _capability_positive_value_matches(
                        validated_constraint.value,
                        capability_rule,
                    )
                    and action in {item.value for item in CustomerAct}
                ):
                    current_acts = list(repaired.get("acts") or [])
                    if action not in current_acts:
                        current_acts.append(action)
                        repaired["acts"] = current_acts
                        changes.append(
                            "typed_availability_requirement_added_check_stock"
                        )
                if capability_rule.get("retain_as_typed_requirement", False):
                    # Canonicalize an explicit relaxation to one stable fact
                    # value plus excluded polarity.  This lets the reducer
                    # replace the previous required fact without inventing a
                    # second Boolean coordinate.  The fact remains outside
                    # product-contract readiness and is consumed only by the
                    # typed capability planner.
                    normalized = validated_constraint.model_dump(mode="json")
                    normalized["name"] = canonical_name
                    if availability_polarity is not None:
                        normalized["status"] = ConstraintStatus.KNOWN.value
                        normalized["value"] = True
                        normalized["polarity"] = availability_polarity
                    if (
                        validated_constraint.status == ConstraintStatus.KNOWN
                        and _capability_positive_value_matches(
                            validated_constraint.value,
                            capability_rule,
                        )
                    ):
                        normalized["value"] = True
                    elif validated_constraint.status == ConstraintStatus.KNOWN:
                        normalized["value"] = True
                        normalized["polarity"] = ConstraintPolarity.EXCLUDED.value
                        changes.append(
                            "availability_relaxation_canonicalized_to_excluded"
                        )
                    normalized_constraints.append(normalized)
                    changes.append("availability_requirement_retained_as_typed_fact")
                    continue
                if capability_rule.get("remove_from_technical_constraints", False):
                    changes.append("capability_constraint_removed_from_facts")
                    continue
            normalized_constraints.append(
                validated_constraint.model_dump(mode="json")
            )
        # Some models encode an explicit relaxation as ``unknown preferred``.
        # That non-known proposal has been removed above because it does not
        # assert customer ignorance.  Its separately grounded softening signal
        # may still relax exactly one known fact in the same scope.
        constraints_by_scope: dict[tuple[str, int | None], list[int]] = {}
        for index, constraint in enumerate(normalized_constraints):
            scope = (
                str(constraint.get("name") or ""),
                constraint.get("applies_to_product"),
            )
            constraints_by_scope.setdefault(scope, []).append(index)
        for name, product_index, softening_evidence in pending_softenings:
            indexes = constraints_by_scope.get((name, product_index), [])
            known_indexes = [
                index
                for index in indexes
                if normalized_constraints[index].get("status")
                == ConstraintStatus.KNOWN.value
            ]
            if len(known_indexes) != 1:
                continue
            known_index = known_indexes[0]
            combined_evidence = _combined_grounded_evidence_fragment(
                normalized_constraints[known_index]["evidence"],
                softening_evidence,
                current_message,
            )
            if combined_evidence is None:
                continue
            normalized_constraints[known_index]["polarity"] = (
                ConstraintPolarity.PREFERRED.value
            )
            normalized_constraints[known_index]["evidence"] = combined_evidence
            changes.append("constraint_known_value_preferred_unknown_merged")

    if unit_ambiguities:
        ambiguities = repaired.get("ambiguities")
        if isinstance(ambiguities, list):
            existing_keys = {
                (
                    str(item.get("kind") or ""),
                    str(item.get("evidence") or ""),
                    str(item.get("description") or ""),
                )
                for item in ambiguities
                if isinstance(item, dict)
            }
            for ambiguity in unit_ambiguities:
                key = (
                    ambiguity["kind"],
                    ambiguity["evidence"],
                    ambiguity["description"],
                )
                if key in existing_keys or len(ambiguities) >= 12:
                    continue
                ambiguities.append(ambiguity)
                existing_keys.add(key)
                changes.append("constraint_unit_ambiguity_added")

    if categorical_ambiguities:
        ambiguities = repaired.get("ambiguities")
        if isinstance(ambiguities, list):
            existing_keys = {
                (
                    str(item.get("kind") or ""),
                    str(item.get("evidence") or ""),
                    str(item.get("description") or ""),
                )
                for item in ambiguities
                if isinstance(item, dict)
            }
            for ambiguity in categorical_ambiguities:
                key = (
                    ambiguity["kind"],
                    ambiguity["evidence"],
                    ambiguity["description"],
                )
                if key in existing_keys or len(ambiguities) >= 12:
                    continue
                ambiguities.append(ambiguity)
                existing_keys.add(key)
                changes.append("constraint_categorical_ambiguity_added")

    normalized_constraints = _dedupe_equivalent_numeric_constraints(
        normalized_constraints,
        changes,
    )
    repaired["constraints"] = _recover_typed_numeric_constraints(
        normalized_constraints,
        normalized_products,
        current_message,
        authoritative_product_hints,
        changes,
    )
    repaired["constraints"] = _recover_circulation_pump_designation_constraints(
        repaired["constraints"],
        normalized_products,
        changes,
    )
    repaired["constraints"] = _resolve_shown_card_identifier_constraints(
        repaired["constraints"],
        normalized_products,
        current_message,
        authoritative_product_hints,
        shown_product_cards,
        changes,
    )
    repaired["constraints"] = _dedupe_equivalent_numeric_constraints(
        repaired["constraints"],
        changes,
    )
    _promote_unambiguous_constraint_correction(
        repaired,
        normalized_products,
        authoritative_dialogue_state,
        changes,
    )

    references = repaired.get("references")
    if isinstance(references, list):
        for item in references:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            evidence = item.get("evidence")
            if (
                isinstance(text, str)
                and len(text) > 240
                and isinstance(evidence, str)
            ):
                # Reference text is descriptive; evidence is the auditable
                # source mention.  Prefer the latter over truncating copied
                # historical prose into a seemingly authoritative reference.
                item["text"] = evidence[:240]
                changes.append("reference_text_replaced_with_grounded_evidence")
            target_hint = item.get("target_hint")
            if (
                isinstance(target_hint, str)
                and len(target_hint) > 160
                and isinstance(evidence, str)
            ):
                item["target_hint"] = evidence[:160]
                changes.append("reference_target_hint_replaced_with_grounded_evidence")

    return repaired, tuple(dict.fromkeys(changes))


_PII_FACT_NAME_MARKERS = frozenset(
    {
        "address",
        "contact",
        "customer_name",
        "email",
        "full_name",
        "person_name",
        "phone",
        "recipient",
        "адрес",
        "контакт",
        "почта",
        "телефон",
        "фио",
    }
)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _bounded_context_text(value: Any, max_length: int) -> str:
    return redact_pii_for_model(str(value or ""))[:max_length]


def _safe_typed_fact_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    source = str(value)
    redacted = redact_pii_for_model(source)
    if redacted != source:
        return None
    return redacted[:160]


def _authoritative_dialogue_state_context(
    state: SessionState,
) -> dict[str, Any] | None:
    """Expose only bounded typed task facts, never raw dialogue or PII."""

    typed_state = state.live_dialogue_state_v2 or state.dialogue_state_v2
    if typed_state is None:
        return None

    active_goal_id = typed_state.active_goal_id
    active_task_id = typed_state.task_stack.active_task_id
    ordered_goals = sorted(
        typed_state.product_goals,
        key=lambda item: item.goal_id != active_goal_id,
    )[:8]
    ordered_tasks = sorted(
        typed_state.tasks,
        key=lambda item: item.task_id != active_task_id,
    )[:12]
    active_facts = []
    for fact in typed_state.constraints:
        if not fact.active:
            continue
        normalized_name = fact.name.casefold()
        if any(marker in normalized_name for marker in _PII_FACT_NAME_MARKERS):
            continue
        safe_value = _safe_typed_fact_value(fact.value)
        if fact.value is not None and safe_value is None:
            continue
        active_facts.append(
            {
                "fact_id": _bounded_context_text(fact.fact_id, 120),
                "name": _bounded_context_text(fact.name, 120),
                "value": safe_value,
                "unit": (
                    _bounded_context_text(fact.unit, 40)
                    if fact.unit is not None
                    else None
                ),
                "status": _enum_value(fact.status),
                "polarity": _enum_value(fact.polarity),
                "strength": _enum_value(fact.strength),
                "goal_id": (
                    _bounded_context_text(fact.goal_id, 120)
                    if fact.goal_id is not None
                    else None
                ),
                "task_id": (
                    _bounded_context_text(fact.task_id, 120)
                    if fact.task_id is not None
                    else None
                ),
                "source_turn": fact.source_turn,
            }
        )
        if len(active_facts) >= 24:
            break

    return {
        "schema_version": typed_state.schema_version,
        "turn_number": typed_state.turn_number,
        "active_goal_id": (
            _bounded_context_text(active_goal_id, 120)
            if active_goal_id is not None
            else None
        ),
        "task_stack": {
            "active_task_id": (
                _bounded_context_text(active_task_id, 120)
                if active_task_id is not None
                else None
            ),
            "pending_task_ids": [
                _bounded_context_text(item, 120)
                for item in typed_state.task_stack.pending_task_ids[:12]
            ],
            "suspended_task_ids": [
                _bounded_context_text(item, 120)
                for item in typed_state.task_stack.suspended_task_ids[:12]
            ],
        },
        "goals": [
            {
                "goal_id": _bounded_context_text(goal.goal_id, 120),
                "canonical_type": (
                    _bounded_context_text(goal.canonical_type, 120)
                    if goal.canonical_type is not None
                    else None
                ),
                "category": _enum_value(goal.category),
                "role": _enum_value(goal.role),
                "confidence": goal.confidence,
                "confirmed_turn": goal.confirmed_turn,
                "type_locked": goal.type_locked,
                "category_locked": goal.category_locked,
            }
            for goal in ordered_goals
        ],
        "tasks": [
            {
                "task_id": _bounded_context_text(task.task_id, 120),
                "act": _enum_value(task.act),
                "target_goal_id": (
                    _bounded_context_text(task.target_goal_id, 120)
                    if task.target_goal_id is not None
                    else None
                ),
                "priority": task.priority,
                "status": _enum_value(task.status),
                "source_turn": task.source_turn,
                "last_addressed_turn": task.last_addressed_turn,
            }
            for task in ordered_tasks
        ],
        "active_facts": active_facts,
    }


def _active_product_skus(state: SessionState) -> list[str]:
    """Return a bounded, stable union of cards exposed by either live path."""

    result: list[str] = []
    seen: set[str] = set()
    for card in (*state.v2_last_products, *state.last_products):
        sku = _bounded_context_text(card.sku, 120)
        key = unicodedata.normalize("NFKC", sku).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(sku)
        if len(result) >= 8:
            break
    return result


def _last_committed_presentation_context(
    state: SessionState,
) -> dict[str, Any] | None:
    """Expose exact committed cards, never an unselected shadow candidate set."""

    typed_state = state.live_dialogue_state_v2 or state.dialogue_state_v2
    if typed_state is None or typed_state.answer_plan_summary is None:
        return None
    summary = typed_state.answer_plan_summary
    if (
        _enum_value(summary.delivery_status) != "committed_to_session"
        or not summary.presented_candidates
    ):
        return None

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in summary.presented_candidates:
        sku = _bounded_context_text(candidate.sku, 120)
        key = unicodedata.normalize("NFKC", sku).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "sku": sku,
                "name": _bounded_context_text(candidate.name, 180),
                "product_kind": _enum_value(candidate.product_kind),
                "role": _enum_value(candidate.role),
                "task_id": _bounded_context_text(candidate.task_id, 120),
                "goal_id": (
                    _bounded_context_text(candidate.goal_id, 120)
                    if candidate.goal_id is not None
                    else None
                ),
                "source_turn": candidate.source_turn,
            }
        )
        if len(candidates) >= 8:
            break
    if not candidates:
        return None
    return {
        "plan_id": _bounded_context_text(summary.plan_id, 120),
        "source_turn": summary.source_turn,
        "candidates": candidates,
    }


def semantic_context(state: SessionState) -> dict[str, Any]:
    """Return bounded, PII-free context needed to interpret a short answer."""

    pending = state.pending_question_state
    recent_dialogue = []
    for item in state.history[-4:]:
        recent_dialogue.append(
            {
                "role": str(item.get("role") or ""),
                "content": redact_pii_for_model(str(item.get("content") or ""))[:600],
            }
        )
    return {
        "active_category": state.category,
        "last_intent": state.last_intent,
        "pending_question": (
            {
                "question_id": pending.question_id,
                "text": redact_pii_for_model(pending.text)[:400],
                "expected_slots": list(pending.expected_slots),
                "category": pending.category,
            }
            if pending is not None
            else None
        ),
        "active_product_skus": _active_product_skus(state),
        "last_committed_presentation": (
            _last_committed_presentation_context(state)
        ),
        "authoritative_dialogue_state_v2": (
            _authoritative_dialogue_state_context(state)
        ),
        "recent_dialogue": recent_dialogue,
    }


class SemanticInterpreter:
    """LLM semantic parser used only as an observable shadow component."""

    def __init__(
        self,
        llm_client: OpenRouterClient,
        *,
        model: str | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.model = model

    def interpret(
        self,
        current_message: str,
        state_before: SessionState,
    ) -> SemanticInterpretationResult:
        started = monotonic()
        model = self.model or self.llm_client.settings.llm_model
        safe_message = redact_pii_for_model(current_message)
        authoritative_product_hints = _authoritative_product_hints(state_before)
        shown_product_cards = _shown_product_cards(state_before)
        context_before_turn = semantic_context(state_before)
        authoritative_dialogue_state = context_before_turn.get(
            "authoritative_dialogue_state_v2"
        )
        payload = {
            "current_message": safe_message,
            "context_before_turn": context_before_turn,
            "ontology": semantic_ontology_payload(),
            "output_schema": TurnUnderstanding.model_json_schema(),
        }
        fallback: dict[str, Any] = {
            "schema_version": "1.2",
            "language": "ru",
            "operation": "unknown",
            "acts": [],
            "products": [],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "information_requests": [],
            "answers_pending_question": False,
            "confidence": 0.0,
        }
        requested = bool(self.llm_client.settings.llm_enabled)
        try:
            raw, transport_succeeded = self.llm_client.complete_json(
                "SemanticInterpreter.shadow",
                [
                    {"role": "system", "content": SEMANTIC_INTERPRETER_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                fallback=fallback,
                model=self.model,
            )
            if not transport_succeeded:
                return SemanticInterpretationResult(
                    status="skipped" if not requested else "rejected",
                    requested=requested,
                    transport_succeeded=False,
                    output_accepted=False,
                    model=model,
                    latency_ms=int((monotonic() - started) * 1000),
                    fallback_reason=getattr(
                        self.llm_client, "last_fallback_reason", None
                    ),
                )
            audit_payload = {
                **payload,
                # The audit receives even a schema-invalid first attempt.  Its
                # purpose includes repairing swapped enum fields or omitted
                # required values before the strict local validator decides.
                "candidate": raw,
            }
            audited_raw, audit_transport = self.llm_client.complete_json(
                "SemanticInterpreter.shadow.audit",
                [
                    {"role": "system", "content": SEMANTIC_AUDIT_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(audit_payload, ensure_ascii=False),
                    },
                ],
                fallback=raw,
                model=self.model,
            )
            audit_accepted = False
            audit_rejection: str | None = None
            understanding: TurnUnderstanding | None = None
            validation_errors: list[str] = []
            structural_repairs: tuple[str, ...] = ()
            if audit_transport:
                try:
                    repaired_audit, audit_repairs = repair_grounded_semantic_payload(
                        audited_raw,
                        safe_message,
                        authoritative_product_hints,
                        shown_product_cards,
                        authoritative_dialogue_state,
                    )
                    audited = TurnUnderstanding.model_validate(repaired_audit)
                    validate_current_turn_evidence(audited, safe_message)
                    validate_semantic_content_coverage(
                        audited,
                        safe_message,
                        audit_repairs,
                    )
                    validate_product_modifier_coverage(audited)
                    understanding = audited
                    audit_accepted = True
                    structural_repairs = audit_repairs
                except (ValidationError, ValueError, TypeError) as exc:
                    audit_rejection = str(exc)[:1200]
                    validation_errors.append(f"audit: {exc}")
            if understanding is None:
                try:
                    repaired_first, first_repairs = repair_grounded_semantic_payload(
                        raw,
                        safe_message,
                        authoritative_product_hints,
                        shown_product_cards,
                        authoritative_dialogue_state,
                    )
                    first_pass = TurnUnderstanding.model_validate(repaired_first)
                    validate_current_turn_evidence(first_pass, safe_message)
                    validate_semantic_content_coverage(
                        first_pass,
                        safe_message,
                        first_repairs,
                    )
                    validate_product_modifier_coverage(first_pass)
                    understanding = first_pass
                    structural_repairs = first_repairs
                except (ValidationError, ValueError, TypeError) as exc:
                    validation_errors.append(f"first_pass: {exc}")
            if understanding is None:
                raise ValueError("; ".join(validation_errors)[:2000])
            return SemanticInterpretationResult(
                status="accepted",
                requested=True,
                transport_succeeded=True,
                output_accepted=True,
                model=model,
                latency_ms=int((monotonic() - started) * 1000),
                understanding=understanding,
                audit_requested=True,
                audit_output_accepted=audit_accepted,
                structural_repairs=structural_repairs,
                audit_rejection_reason=(
                    audit_rejection
                    or (
                        None
                        if audit_transport
                        else getattr(self.llm_client, "last_fallback_reason", None)
                    )
                ),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            return SemanticInterpretationResult(
                status="rejected",
                requested=requested,
                transport_succeeded=True,
                output_accepted=False,
                model=model,
                latency_ms=int((monotonic() - started) * 1000),
                rejection_reason=str(exc)[:1200],
            )
        except Exception as exc:  # shadow failures must never escape
            return SemanticInterpretationResult(
                status="rejected",
                requested=requested,
                transport_succeeded=False,
                output_accepted=False,
                model=model,
                latency_ms=int((monotonic() - started) * 1000),
                rejection_reason=f"{type(exc).__name__}: {exc}"[:1200],
            )
