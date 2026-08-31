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

from app.catalog_v2.normalization import normalize_unit_label, parse_pump_designation
from app.catalog_v2.registry import (
    DEFAULT_CONTRACTS,
    canonical_brand,
    resolve_brand_mentions,
)
from app.dialogue_v2.contracts import GoalReactivationResolution
from app.dialogue_v2.reactivation import resolve_goal_reactivation
from app.models import SessionState
from app.openrouter_client import OpenRouterClient
from app.pii import redact_pii_for_model
from app.semantic_v2.contracts import SemanticGateResult, SemanticTurnDeltaV1
from app.sku_resolution import CatalogSkuAnchor, resolve_catalog_sku_anchors

from .domain_ontology import (
    RANGE_CAPABLE_CONSTRAINT_FACTS,
    action_aliases,
    canonical_product_type,
    closed_value_aliases,
    semantic_ontology_payload,
)
from .numeric_semantics import extract_spoken_cardinal_mentions


SEMANTIC_PROMPT_VERSION = "turn-understanding-v1.23"
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
- словесное число рядом с явно названной единицей или в ответе на typed
  pending-вопрос не теряй: сохрани исходный фрагмент, предполагаемый predicate
  и единицу. Не вычисляй значение и не назначай ему другую размерность — это
  проверит детерминированный normalizer после тебя;
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
- если last_committed_presentation содержит минимум две карточки, фразы
  «чем они отличаются», «в чём разница», «какие отличия» и «сравните их» —
  это compare по уже показанным карточкам, а не explain и не возвращение к
  анкете; не выбирай победителя без явно названного критерия;
- при Compare перечисляй в references *каждую* явно названную карточку:
  «первый и третий», «второй с четвёртым», «№1 и №4», точный/частичный SKU,
  точное название или «этот». Для каждого reference сохраняй kind и text как
  дословный span текущей реплики и evidence с тем же span. Не превращай
  «самый мощный», «лучший», марку или собственное предположение в ordinal/SKU:
  если карточка не названа однозначно, оставь ambiguity. Модель только
  распознаёт ссылку; конкретный SKU всегда разрешается после неё по реально
  показанным карточкам;
- явный вопрос «подойдёт ли X к Y», «можно соединить X и Y» или «совместимы
  ли X и Y» — это compatibility, а не explain, если покупатель назвал две
  конкретные позиции; сам не выбирай эти позиции и не выноси вердикт;
- «какой/какую ... смотреть, подобрать, посоветовать» о ещё не определённом
  типе товара — select, а не explain: вопрос о характеристике требует
  одновременно предмета и характеристики;
- длинная реплика может содержать несколько независимых target-товаров.
  Сохрани все target в порядке покупателя и не теряй действие select/find.
  «сначала ... затем/потом ...» задаёт порядок этих задач; не смешивай их
  характеристики и не превращай вторую цель в контекст первой;
- точный длинный числовой артикул внутри явно названного товара (например
  2202210) — это product identity, а не техническая характеристика. Не
  придумывай ему constraint; дальнейшая проверка существования артикула
  выполняется вне тебя по каталогу;
- явное разрешение ослабить ранее заданное условие сохрани как refine/correct
  и preferred-ограничение, а не как новое обязательное required-условие;
- явную просьбу продолжить подбор только по уже подтверждённым данным, не
  задавая сейчас недостающий вопрос, сохрани в selection_controls как
  continue_with_confirmed_facts. Это не означает, что отсутствующий параметр
  известен, неважен или что hard-ограничение можно ослабить;
- selection_control допустим и в первой реплике с operation=new: уже
  подтверждёнными данными могут быть только явно названные тип товара и
  несколько характеристик. Если покупатель одновременно просит показать
  варианты и говорит, что параметр неизвестен, отказался его уточнять или
  хочет предварительную выдачу без уточнений, сохрани и эпистемический статус
  явно названного параметра, и continue_with_confirmed_facts;
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
- простое сообщение о потребности («нужен товар») без явной просьбы показать,
  найти или открыть ассортимент кодируй как select: покупатель описал задачу,
  но ещё не попросил немедленную выдачу. Явная просьба показать/найти варианты
  или ассортимент — find;
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

Как кодировать управление подбором:
- selection_controls содержит только явную просьбу продолжить поиск, показ или
  подбор по уже подтверждённым фактам без очередного уточнения сейчас;
- такой control допустим вместе с operation=new и новой target-задачей. Он не
  требует старого pending-вопроса: в первой реплике подтверждёнными фактами уже
  могут быть тип товара или отдельные явно сообщённые характеристики;
- используй kind=continue_with_confirmed_facts для перефразирований вроде
  просьбы показать по имеющимся данным или продолжить без ответа на последний
  typed-вопрос. Короткую ссылку вроде «без этого» связывай только с
  authoritative_dialogue_state_v2.pending_decision_question; если такой
  типизированной связи нет и смысл неоднозначен, зарегистрируй ambiguity;
- control не превращает отсутствующий факт в unknown/refused/deferred, не
  снимает уже известное hard-условие и не разрешает выдумывать значение;
- если в той же реплике покупатель явно говорит, что конкретный параметр не
  знает, отказывается сообщать или уточнит позже, сохрани одновременно и
  соответствующий constraint со status=unknown/refused/deferred, и отдельный
  selection_control. Control не заменяет эпистемический статус параметра;
- каждый control сохраняет дословное evidence из current_message.

Как кодировать предпочтения подбора:
- selection_preferences описывает только порядок или явный коммерческий
  фильтр среди технически допустимых товаров; это не замена характеристикам
  товара и не разрешение ослабить их;
- для брендов используй только канонические значения из brand_values ontology;
  не придумывай производителя и не используй неразрешённое fuzzy-сопоставление;
- «только <бренд>» — brand_required со значением этого бренда и одновременно
  constraint brand=<бренд>, polarity=required;
- «нужен/покажите <товар> <бренд>» с одним явно названным брендом — также
  brand_required: в запросе на подбор это требование к товару, а не
  предпочтение магазина по умолчанию;
- «<бренд> желательно/предпочтительно» — brand_preferred со значением бренда
  и одновременно constraint brand=<бренд>, polarity=preferred;
- «подешевле/самый дешёвый» для нового или продолжаемого подбора —
  price_lowest; «есть вариант дешевле этого/показанных?» —
  price_below_reference;
- «только из наличия» — stock_required и одновременно существующий required
  constraint stock_availability=true. Обычный вопрос «есть ли в наличии?»
  остаётся check_stock и не является предпочтением;
- «какой из показанных дешевле?» — compare по показанным карточкам, а не
  новая price-preference и не новый поиск;
- при выборе по цене или бренду сохраняй технические ограничения и категорию:
  нельзя подменять ими топливо, размер, назначение или совместимость.

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
- конструкция «для X» задаёт требуемое назначение/применение X и имеет
  polarity=required, если в самой реплике нет явного отрицания или запрета;
  эллиптический ответ вроде «для холодной» сам по себе не означает excluded;
- polarity=excluded используй только для явно запрещённого или исключённого
  значения, а не для краткого положительного ответа о назначении;
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

Верни объект с schema_version="1.3", language, operation, acts, products,
constraints, references, ambiguities, workflow_controls, selection_controls,
selection_strategy, information_requests, answers_pending_question и confidence.
selection_strategy обязателен на каждом ходе: standard без control,
continue_with_confirmed_facts с ровно одним согласованным selection_control
либо ambiguous с типизированной ambiguity. Не пропускай этот verdict.
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
   Положительная конструкция «для X», включая краткий ответ о назначении,
   имеет polarity=required. Excluded допустим только при явном отрицании или
   запрете X; не переворачивай смысл эллиптического ответа.
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
12. Явную просьбу продолжить подбор по уже подтверждённым данным без очередного
    уточнения сохрани в selection_controls как continue_with_confirmed_facts.
    Это отдельное управление подбором, не commerce workflow и не предметный
    act. Не превращай из-за него отсутствующий параметр в unknown/refused/
    deferred и не снимай hard-ограничения. Местоименную ссылку «без этого»
    разрешай только через typed pending_decision_question из authoritative
    dialogue state; без такой связи сохраняй неоднозначность.
    Если эта же реплика явно содержит unknown/refused/deferred конкретного
    параметра, сохрани и constraint, и selection_control: ни один из них не
    заменяет другой.
    Это правило действует и для operation=new: первая реплика с target и
    просьбой сразу показать предварительные варианты по известному должна
    содержать control. Старый pending-вопрос для этого не требуется.
13. Проверь различие find/select: простая потребность в подходящем товаре без
    просьбы немедленно показать ассортимент — select; явная просьба показать,
    найти или открыть варианты — find.
14. Независимо от candidate заново определи обязательный selection_strategy:
    standard, continue_with_confirmed_facts или ambiguous. Для continue verdict
    верни ровно один согласованный selection_control с тем же evidence; для
    ambiguous — типизированную ambiguity с тем же evidence; для standard — ни
    control, ни evidence. Schema 1.3 без этого verdict неполна.

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
    COMPATIBILITY = "compatibility"
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


class SelectionControlKind(str, Enum):
    CONTINUE_WITH_CONFIRMED_FACTS = "continue_with_confirmed_facts"


class SelectionPreferenceKind(str, Enum):
    """A customer preference for ordering an otherwise safe selection.

    The values deliberately describe *how to order or constrain an already
    compatible candidate set*.  They are not technical facts and never give
    permission to relax a product contract.
    """

    BRAND_REQUIRED = "brand_required"
    BRAND_PREFERRED = "brand_preferred"
    PRICE_LOWEST = "price_lowest"
    PRICE_BELOW_REFERENCE = "price_below_reference"
    STOCK_REQUIRED = "stock_required"


class SelectionStrategyKind(str, Enum):
    STANDARD = "standard"
    CONTINUE_WITH_CONFIRMED_FACTS = "continue_with_confirmed_facts"
    AMBIGUOUS = "ambiguous"


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


class SelectionControl(StrictModel):
    """Explicit customer control of selection strategy, not a product fact."""

    kind: SelectionControlKind
    evidence: str = Field(min_length=1, max_length=240)


class SelectionPreference(StrictModel):
    """One explicit, source-grounded preference within a selection task."""

    kind: SelectionPreferenceKind
    value: str | bool | None = None
    evidence: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def preference_has_the_required_value(self) -> "SelectionPreference":
        if self.kind in {
            SelectionPreferenceKind.BRAND_REQUIRED,
            SelectionPreferenceKind.BRAND_PREFERRED,
        } and not isinstance(self.value, str):
            raise ValueError("brand preference requires a brand value")
        if self.kind == SelectionPreferenceKind.STOCK_REQUIRED and self.value is not True:
            raise ValueError("stock preference requires value=true")
        if self.kind in {
            SelectionPreferenceKind.PRICE_LOWEST,
            SelectionPreferenceKind.PRICE_BELOW_REFERENCE,
        } and self.value is not None:
            raise ValueError("price preference must not carry an arbitrary value")
        return self


class SelectionStrategyDecision(StrictModel):
    """Mandatory semantic verdict about how product selection may proceed."""

    kind: SelectionStrategyKind
    evidence: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def evidence_matches_kind(self) -> "SelectionStrategyDecision":
        if self.kind == SelectionStrategyKind.STANDARD:
            if self.evidence is not None:
                raise ValueError("standard selection strategy cannot have evidence")
        elif self.evidence is None:
            raise ValueError("non-standard selection strategy requires evidence")
        return self


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

    schema_version: Literal["1.0", "1.1", "1.2", "1.3"] = "1.3"
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
    selection_controls: list[SelectionControl] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "Explicit request to continue selection using confirmed facts only. "
            "It never supplies, relaxes or changes a technical fact."
        ),
    )
    selection_preferences: list[SelectionPreference] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "Explicit brand, price or availability preference for the active "
            "selection. Preferences never replace technical constraints."
        ),
    )
    selection_strategy: SelectionStrategyDecision | None = Field(
        default=None,
        description=(
            "Required for schema 1.3. An explicit verdict independent of the "
            "optional selection-control collection."
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
        decision = self.selection_strategy
        if self.schema_version == "1.3" and decision is None:
            raise ValueError("schema 1.3 requires selection_strategy")
        if decision is None:
            return self

        controls = self.selection_controls
        def normalize(value: object) -> str:
            return " ".join(str(value).casefold().split())
        if decision.kind == SelectionStrategyKind.STANDARD:
            if controls:
                raise ValueError("standard strategy cannot contain selection controls")
        elif decision.kind == SelectionStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS:
            if len(controls) != 1 or controls[0].kind != (
                SelectionControlKind.CONTINUE_WITH_CONFIRMED_FACTS
            ):
                raise ValueError(
                    "continue strategy requires exactly one matching control"
                )
            if normalize(controls[0].evidence) != normalize(decision.evidence):
                raise ValueError("selection strategy/control evidence mismatch")
        elif decision.kind == SelectionStrategyKind.AMBIGUOUS:
            if controls:
                raise ValueError("ambiguous strategy cannot contain selection controls")
            if not any(
                normalize(item.evidence) == normalize(decision.evidence)
                for item in self.ambiguities
            ):
                raise ValueError(
                    "ambiguous strategy requires a matching typed ambiguity"
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
    semantic_delta: SemanticTurnDeltaV1 | None = None
    semantic_gate: SemanticGateResult | None = None
    goal_reactivation: GoalReactivationResolution | None = None
    rejection_reason: str | None = None
    fallback_reason: str | None = None


def _normalize_evidence(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


_SOURCE_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_NUMERIC_ARTICLE_TOKEN_RE = re.compile(r"(?<!\d)\d{6,}(?!\d)")
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
            r"^\s*(?:°\s*[cс]|[cс]|℃|celsius|цельси\w*)(?![\w])",
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
            r"(?:определ\w*|уточн\w*|измер\w*|сказ\w*)\b|"
            r"\b(?:выясн\w*|узна\w*|уточн\w*|определ\w*|измер\w*)\s+"
            r"не\s+получ\w*\b|\bне\s+получ\w*\s+"
            r"(?:выясн\w*|узна\w*|уточн\w*|определ\w*|измер\w*)\b|"
            r"\bневозмож\w*\s+"
            r"(?:выясн\w*|узна\w*|уточн\w*|определ\w*|измер\w*)\b|"
            r"\b(?:неоткуда|негде)\s+"
            r"(?:выясн\w*|узна\w*|уточн\w*|определ\w*|измер\w*)\b",
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
            r"\b(?:сообщ\w*|говор\w*|уточн\w*|предостав\w*)\s+"
            r"не\s+(?:хоч\w*|буд\w*|стан\w*)\b|"
            r"\bне\s+(?:скаж\w*|сообщ\w*|предостав\w*)\b|"
            r"\bне\s*важ\w*\b|\bне\s+принципиаль\w*\b|"
            r"\bбез\s+разниц\w*\b",
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
            r"\b(?:позже|потом|поздн\w*)\s+"
            r"(?:уточн\w*|скаж\w*|сообщ\w*|измер\w*|провер\w*)\b|"
            r"\b(?:уточн\w*|скаж\w*|сообщ\w*|измер\w*|провер\w*)\s+"
            r"(?:позже|потом|поздн\w*)\b|\b(?:отлож\w*|остав\w*)\s+"
            r"(?:это\s+)?(?:на\s+потом|пока)\b|"
            r"\bверн\w*\s+к\s+(?:этому|параметр\w*)\s+"
            r"(?:позже|потом|поздн\w*)\b|"
            r"\bс\s+[\w-]+\s+верн\w*\s+"
            r"(?:позже|потом|поздн\w*)\b|"
            r"\b(?:позже|потом|поздн\w*)\s+верн\w*\b",
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
_SPOKEN_POWER_UNIT_RE = re.compile(
    r"\s*(?P<unit>к\s*вт|киловатт\w*)(?![\w])",
    flags=re.IGNORECASE,
)
_SPOKEN_MILLIMETRE_UNIT_RE = re.compile(
    r"\s*(?P<unit>мм|миллиметр\w*)(?![\w])",
    flags=re.IGNORECASE,
)
_AREA_ANCHOR_RE = re.compile(
    r"(?<![\w])(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>(?:м|m)\s*[²2]|кв\.?\s*(?:м|m)|"
    r"квадрат\w*(?:\s+(?:м|m)етр\w*)?)(?![\w])",
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
_SEWER_LENGTH_ANCHOR_RE = re.compile(
    r"(?iu)\b(?:длин\w*|отрез\w*|участ\w*)\s*[:=]?\s*"
    r"(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>мм|mm|см|cm|м|m|миллиметр\w*|сантиметр\w*|метр\w*)\b"
)
_RADIATOR_VALVE_SHAPE_RE = re.compile(
    r"(?iu)\b(?P<shape>прям\w*|углов\w*)\b"
)
_THERMOSTATIC_HEAD_KIT_RE = re.compile(
    r"(?iu)\b(?:с\s+)?термо(?:статическ\w*\s+)?головк\w*\b"
)
_BOILER_CIRCUITS_UNKNOWN_RE = re.compile(
    r"(?iu)\b(?:количеств\w*\s+)?контур\w*[^.!?\n]{0,32}"
    r"\b(?:не\s+зна\w*|неизвест\w*|пока\s+не\s+определ\w*)\b"
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
_MOUNTING_LENGTH_ANCHOR_RE = re.compile(
    r"\b(?:монтажн\w*\s+длин\w*|монтажн\w*\s+размер\w*|"
    r"длин\w*\s+между\s+(?:присоединени\w*|патрубк\w*))\s*[:=]?\s*"
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>мм|mm)\b",
    flags=re.IGNORECASE,
)
_RADIATOR_CENTER_DISTANCE_ANCHOR_RE = re.compile(
    r"\b(?:межосев\w*\s+(?:расстояни\w*|размер\w*)|"
    r"расстояни\w*\s+между\s+осями)\s*[:=]?\s*"
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>мм|mm)\b",
    flags=re.IGNORECASE,
)
_PIPE_PRESSURE_ANCHOR_RE = re.compile(
    r"\b(?:рабоч\w*\s+)?давлен\w*\s*[:=]?\s*"
    r"(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<unit>бар(?:а|ов)?|bar(?:s)?)\b",
    flags=re.IGNORECASE,
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
# ``DN25`` is also a normal way to state a circulation pump's nominal
# connection diameter.  Keep it separate from the pipe patterns above: an
# outer pipe diameter is not automatically a pump connection diameter, while
# an explicit DN notation is sufficiently precise for both families.
_PUMP_CONNECTION_DIAMETER_ANCHOR_RE = re.compile(
    r"\b(?:dn|ду|дн)\s*-?\s*(?P<value>\d{1,3})(?:\s*(?P<unit>мм|mm))?\b",
    flags=re.IGNORECASE,
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


_CONTEXTUAL_UNIT_FAMILIES: dict[str, frozenset[str]] = {
    "degree": frozenset({"angle", "temperature"}),
    "degrees": frozenset({"angle", "temperature"}),
    "deg": frozenset({"angle", "temperature"}),
    "градус": frozenset({"angle", "temperature"}),
    "градуса": frozenset({"angle", "temperature"}),
    "градусов": frozenset({"angle", "temperature"}),
    "градусы": frozenset({"angle", "temperature"}),
}
_CANONICAL_UNIT_FOR_FAMILY = {
    "angle": "deg",
    "temperature": "C",
}


def _contextual_unit_families_for_numeric_evidence(
    evidence: str,
    values: tuple[float, ...],
) -> tuple[frozenset[str], ...]:
    """Return declared contextual units attached to exact numeric anchors."""

    contextual: list[frozenset[str]] = []
    for match in _NUMERIC_LITERAL_RE.finditer(evidence):
        evidence_value = float(match.group(0).replace(",", "."))
        if not any(
            math.isclose(
                evidence_value,
                value,
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
            for value in values
        ):
            continue
        suffix = evidence[match.end() :]
        tokens = _source_tokens(suffix)
        if not tokens:
            continue
        unit_token, token_start, _token_end = tokens[0]
        # The unit must be immediately attached to the numeric coordinate;
        # punctuation and whitespace are harmless, intervening words are not.
        if any(character.isalnum() for character in suffix[:token_start]):
            continue
        families = _CONTEXTUAL_UNIT_FAMILIES.get(unit_token)
        if families is not None and families not in contextual:
            contextual.append(families)
    return tuple(contextual)


def _repair_known_numeric_pending_answer(
    constraint: dict[str, Any],
    authoritative_state: dict[str, Any] | None,
    repaired_turn: dict[str, Any],
    changes: list[str],
) -> bool:
    """Use a committed typed question to disambiguate a numeric answer unit."""

    if constraint.get("status") != ConstraintStatus.KNOWN.value:
        return False
    pending = (authoritative_state or {}).get("pending_decision_question")
    if not isinstance(pending, dict):
        return False
    pending_name = _canonical_constraint_fact_name(
        str(pending.get("fact_name") or "")
    )
    proposed_name = _canonical_constraint_fact_name(
        str(constraint.get("name") or "")
    )
    if proposed_name != pending_name or not _numeric_coordinates(constraint):
        return False

    contextual_unit_was_repaired = False
    issue = _numeric_constraint_unit_incompatibility(constraint)
    if issue is not None:
        expected_family, observed_families = issue
        raw_unit = constraint.get("unit")
        unit_key = _normalize_evidence(str(raw_unit or ""))
        contextual_candidates = [
            candidate
            for candidate in (
                _CONTEXTUAL_UNIT_FAMILIES.get(unit_key),
                *_contextual_unit_families_for_numeric_evidence(
                    str(constraint.get("evidence") or ""),
                    _numeric_coordinates(constraint),
                ),
            )
            if candidate is not None
        ]
        contextual_families = next(
            (
                candidate
                for candidate in contextual_candidates
                if expected_family in candidate
                and set(observed_families).issubset(candidate)
            ),
            None,
        )
        canonical_unit = _CANONICAL_UNIT_FOR_FAMILY.get(expected_family)
        if (
            contextual_families is None
            or canonical_unit is None
        ):
            return False
        if constraint.get("unit") != canonical_unit:
            constraint["unit"] = canonical_unit
            changes.append("pending_numeric_contextual_unit_canonicalized")
        else:
            changes.append("pending_numeric_contextual_evidence_disambiguated")
        contextual_unit_was_repaired = True

    if not repaired_turn.get("answers_pending_question"):
        repaired_turn["answers_pending_question"] = True
        changes.append("pending_numeric_answer_confirmed")
    return contextual_unit_was_repaired


def _normalize_terminal_pending_selection_strategy(
    repaired_turn: dict[str, Any],
    authoritative_state: dict[str, Any] | None,
    changes: list[str],
) -> None:
    """Ignore an unrelated malformed strategy on one terminal pending answer.

    A committed typed decision question supplies the unique fact scope.  Once
    the current turn has a grounded terminal status for that exact fact, a
    model-only ``ambiguous`` strategy without its required typed ambiguity is
    irrelevant to state reduction and must not invalidate the entire turn.
    Genuine typed ambiguity and selection-control conflicts still fail closed.
    """

    pending = (authoritative_state or {}).get("pending_decision_question")
    if not isinstance(pending, dict):
        return
    pending_name = _canonical_constraint_fact_name(
        str(pending.get("fact_name") or "")
    )
    if not pending_name:
        return

    terminal_statuses = {
        ConstraintStatus.UNKNOWN.value,
        ConstraintStatus.REFUSED.value,
        ConstraintStatus.DEFERRED.value,
    }
    matching_terminal = [
        item
        for item in repaired_turn.get("constraints") or ()
        if isinstance(item, dict)
        and _canonical_constraint_fact_name(str(item.get("name") or ""))
        == pending_name
        and str(item.get("status") or "") in terminal_statuses
    ]
    if len(matching_terminal) != 1:
        return

    if not repaired_turn.get("answers_pending_question"):
        repaired_turn["answers_pending_question"] = True
        changes.append("terminal_pending_answer_confirmed")
    if repaired_turn.get("selection_controls"):
        return

    strategy = repaired_turn.get("selection_strategy")
    if not isinstance(strategy, dict):
        return
    strategy_kind = str(getattr(strategy.get("kind"), "value", strategy.get("kind")))
    strategy_evidence = _normalize_evidence(str(strategy.get("evidence") or ""))
    if strategy_kind == SelectionStrategyKind.AMBIGUOUS.value:
        has_matching_ambiguity = any(
            isinstance(item, dict)
            and _normalize_evidence(str(item.get("evidence") or ""))
            == strategy_evidence
            for item in repaired_turn.get("ambiguities") or ()
        )
        if has_matching_ambiguity:
            return
    elif strategy_kind == SelectionStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS.value:
        # A missing verdict evidence cannot authorize a selection control.  It
        # is safe to discard only because the exact pending terminal fact is
        # already independently grounded and no control proposal exists.
        if strategy_evidence:
            return
    elif strategy_kind == SelectionStrategyKind.STANDARD.value:
        return
    else:
        return

    repaired_turn["selection_strategy"] = {
        "kind": SelectionStrategyKind.STANDARD.value,
        "evidence": None,
    }
    changes.append("terminal_pending_answer_selection_strategy_normalized")


def _reconcile_selection_strategy_contract(
    repaired_turn: dict[str, Any],
    current_message: str,
    changes: list[str],
) -> None:
    """Reconcile the redundant strategy verdict with grounded typed controls.

    ``selection_strategy`` is a semantic-model verdict, while
    ``selection_controls`` is the typed fact consumed downstream.  Rejecting
    an otherwise useful turn merely because the model omitted the latter makes
    continuation needlessly brittle.  This repair may derive the typed control
    from a complete verdict, but only when its continue evidence is an exact
    fragment of the current message.  The reverse direction remains a strict
    schema conflict because a control alone lacks the required independent
    verdict.

    No permission is invented from prose here: an ungrounded or evidence-less
    continue verdict is narrowed to ``standard``.  Multiple controls and a
    genuine typed ambiguity remain untouched so strict validation can reject
    the conflict fail-closed.
    """

    raw_controls = repaired_turn.get("selection_controls")
    if raw_controls is None:
        raw_controls = []
        repaired_turn["selection_controls"] = raw_controls
    if not isinstance(raw_controls, list):
        return

    valid_continue_controls: list[dict[str, Any]] = []
    invalid_or_other_controls = False
    discarded_ungrounded_continue = False
    for item in raw_controls:
        if not isinstance(item, dict):
            invalid_or_other_controls = True
            continue
        kind = str(getattr(item.get("kind"), "value", item.get("kind")))
        evidence = item.get("evidence")
        grounded = (
            _grounded_evidence_fragment(evidence, current_message)
            if isinstance(evidence, str) and evidence.strip()
            else None
        )
        if kind == SelectionControlKind.CONTINUE_WITH_CONFIRMED_FACTS.value:
            if grounded is not None:
                valid_continue_controls.append(
                    {
                        "kind": (
                            SelectionControlKind.CONTINUE_WITH_CONFIRMED_FACTS.value
                        ),
                        "evidence": grounded,
                    }
                )
            else:
                # An evidence-less control has no authority.  Dropping it is
                # a narrowing repair and preserves independently grounded
                # facts from the same turn.
                discarded_ungrounded_continue = True
        else:
            invalid_or_other_controls = True

    if invalid_or_other_controls or len(valid_continue_controls) > 1:
        return
    if discarded_ungrounded_continue:
        repaired_turn["selection_controls"] = valid_continue_controls
        changes.append("ungrounded_selection_control_dropped")

    strategy = repaired_turn.get("selection_strategy")
    strategy_kind = ""
    strategy_evidence: str | None = None
    grounded_strategy_evidence: str | None = None
    if isinstance(strategy, dict):
        strategy_kind = str(
            getattr(strategy.get("kind"), "value", strategy.get("kind"))
        )
        raw_evidence = strategy.get("evidence")
        if isinstance(raw_evidence, str) and raw_evidence.strip():
            strategy_evidence = raw_evidence
            grounded_strategy_evidence = _grounded_evidence_fragment(
                raw_evidence,
                current_message,
            )

    if len(valid_continue_controls) == 1:
        control = valid_continue_controls[0]
        if (
            strategy_kind
            == SelectionStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS.value
            and strategy_evidence is None
        ):
            # The typed control is already an exact fragment of this message
            # and the independent verdict agrees on the operation.  Copying
            # that same evidence into the redundant verdict reconciles two
            # representations of one grounded permission; it does not infer a
            # permission from prose or from dialogue history.
            repaired_turn["selection_strategy"] = {
                "kind": SelectionStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS.value,
                "evidence": control["evidence"],
            }
            changes.append("selection_strategy_evidence_recovered_from_control")
        # A control plus an inconsistent or absent verdict is still a schema
        # conflict.  Likewise, two different grounded fragments are left for
        # strict validation instead of choosing between model outputs.
        return

    if (
        strategy_kind == SelectionStrategyKind.AMBIGUOUS.value
        and not raw_controls
    ):
        normalized_strategy_evidence = _normalize_evidence(
            strategy_evidence or ""
        )
        has_matching_typed_ambiguity = bool(
            normalized_strategy_evidence
            and any(
                isinstance(item, dict)
                and _normalize_evidence(str(item.get("evidence") or ""))
                == normalized_strategy_evidence
                for item in (repaired_turn.get("ambiguities") or ())
            )
        )
        if not has_matching_typed_ambiguity:
            # ``ambiguous`` is a redundant strategy verdict, not permission to
            # change or relax product facts.  When its required typed evidence
            # is absent, narrowing only that verdict to ``standard`` preserves
            # independently grounded products, constraints and direct
            # questions from the same LLM frame.  Real typed ambiguities and
            # any conflicting selection controls still validate fail-closed.
            repaired_turn["selection_strategy"] = {
                "kind": SelectionStrategyKind.STANDARD.value,
                "evidence": None,
            }
            changes.append(
                "untyped_ambiguous_selection_strategy_defaulted_to_standard"
            )
        return

    if (
        strategy_kind == SelectionStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS.value
        and grounded_strategy_evidence is not None
    ):
        control = {
            "kind": SelectionControlKind.CONTINUE_WITH_CONFIRMED_FACTS.value,
            "evidence": grounded_strategy_evidence,
        }
        repaired_turn["selection_controls"] = [control]
        repaired_turn["selection_strategy"] = {
            "kind": SelectionStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS.value,
            "evidence": grounded_strategy_evidence,
        }
        changes.append("selection_control_recovered_from_grounded_strategy")
        return

    if strategy_kind != SelectionStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS.value:
        return

    canonical_standard = {
        "kind": SelectionStrategyKind.STANDARD.value,
        "evidence": None,
    }
    if strategy_evidence is None and strategy != canonical_standard:
        repaired_turn["selection_strategy"] = canonical_standard
        changes.append("selection_strategy_safely_defaulted_to_standard")


_SHOW_ACTION_ALIAS_PATTERN = "|".join(
    re.escape(alias).replace(r"\ ", r"\s+")
    for alias in sorted(action_aliases("show"), key=len, reverse=True)
)
_CALCULATE_ACTION_ALIAS_PATTERN = "|".join(
    re.escape(alias).replace(r"\ ", r"\s+")
    for alias in sorted(action_aliases("calculate"), key=len, reverse=True)
)
_COMPATIBILITY_ACTION_ALIAS_PATTERN = "|".join(
    re.escape(alias).replace(r"\ ", r"\s+")
    for alias in sorted(action_aliases("compatibility"), key=len, reverse=True)
)
_EXPLICIT_CALCULATION_RE = re.compile(
    rf"(?iu)(?<![\w-])(?:{_CALCULATE_ACTION_ALIAS_PATTERN})(?![\w-])"
)
_VISIBLE_SCOPE_COMPATIBILITY_RE = re.compile(
    rf"(?iu)(?<![\w-])(?:{_COMPATIBILITY_ACTION_ALIAS_PATTERN})(?![\w-])"
)
# A five-digit number is normally too broad to declare a SKU globally: it may
# be a measurement or an address fragment.  In the very narrow context of an
# explicit two-sided compatibility request it is only an *action anchor*.
# Identity is still resolved later, against the frozen source snapshot, by the
# existing Compatibility request builder.  This lets valid catalogue articles
# such as ``53843`` reach that safe resolver without expanding general SKU
# extraction or allowing the semantic layer to select a product.
_COMPATIBILITY_NUMERIC_REFERENCE_RE = re.compile(r"(?<!\d)\d{5,}(?!\d)")


def _explicit_compatibility_reference_count(message: str) -> int:
    """Count only high-precision identity-shaped spans for action recovery.

    This intentionally does *not* resolve an item or mutate product scope.
    Structured SKU-shaped spans use the shared extractor's conservative
    grammar.  A five-digit numeric span is admitted solely for an explicit
    two-sided compatibility phrase because some current catalogue articles
    have five digits; the request builder subsequently proves its existence
    against the source snapshot before any compatibility rule can execute.
    """

    structured = [
        match.group(0)
        for match in _MIXED_IDENTIFIER_TOKEN_RE.finditer(message)
        if "." in match.group(0) and any(char.isdigit() for char in match.group(0))
    ]
    numeric = [match.group(0) for match in _COMPATIBILITY_NUMERIC_REFERENCE_RE.finditer(message)]
    return len(dict.fromkeys((*structured, *numeric)))
# The modifier-coverage gate checks product evidence syntactically.  A count
# in a total-price request is commercial input, not a hidden characteristic of
# the product.  This deliberately recognises only an explicit count/length
# unit; dimensions such as 20 mm remain subject to the ordinary gate.
_CALCULATION_QUANTITY_IN_EVIDENCE_RE = re.compile(
    r"(?iu)\b\d+(?:[.,]\d+)?\s*(?:шт(?:\.|\b|ук\w*)|штук\w*|"
    r"единиц\w*|м(?:етр(?:а|ов)?)?\b|метр\w*)"
)
_EXPLICIT_SHOW_SELECTION_RE = re.compile(
    r"(?iu)(?<![\w-])(?:"
    r"(?:покаж(?:ите|и-ка|и|ы)|подбер(?:ите|и)|предлож(?:ите|и)|выда(?:йте|й))"
    r"(?:\s+(?:мне|нам))?(?:\s+(?:вариант(?:ы|ов)?|товар(?:ы|ов)?|модел(?:и|ей)?|"
    r"подходящ\w*(?:\s+позици\w*)?|доступн\w*(?:\s+позици\w*)?|наличие|"
    r"что\s+(?:у\s+вас\s+)?есть))?|"
    r"что\s+(?:(?:у\s+вас\s+)?есть|можно\s+(?:взять|купить))|"
    r"можно\s+вариант(?:ы|ов)?|"
    rf"{_SHOW_ACTION_ALIAS_PATTERN}"
    r")"
)
_VISIBLE_SCOPE_COMPARE_RE = re.compile(
    r"(?iu)(?:"
    r"\bсравн\w*\b|"
    r"\bчем\s+(?:(?:они|эти|показанн\w*)\s+)?отлич\w*\b|"
    r"\bв\s+ч[её]м\s+(?:разниц\w*|отлич\w*)\b|"
    r"\bкакие\s+отлич\w*\b|"
    r"\b(?:какой|какая)\s+из\s+(?:них|показанн\w*)\s+"
    r"(?:дешев\w*|дороже|лучше)\b|"
    r"\bчто\s+лучше\b"
    r")"
)
_PRICE_PREFERENCE_RE = re.compile(
    r"(?iu)\b(?:подешевле|деш[её]\w*|сам\w*\s+деш[её]\w*|"
    r"бюджетн\w*|недорог\w*|(?:цен\w*\s+)?ниже\s+по\s+цене|"
    r"не\s+дороже)\b"
)
_RELATIVE_PRICE_RE = re.compile(
    r"(?iu)\b(?:эт(?:от|ого|их)|показан\w*|вариант\w*)\b"
)
_STOCK_REQUIRED_RE = re.compile(
    r"(?iu)\b(?:только|исключительно|именно)\b[^.!?]{0,48}"
    r"\b(?:в\s+наличии|из\s+наличия)\b"
)
_STOCK_CHECK_QUESTION_RE = re.compile(
    r"(?iu)(?:\b(?:есть|имеется|остал(?:ся|ись)|будет)\b[^.!?]{0,48}"
    r"\b(?:в\s+наличии|на\s+складе)\b|"
    r"\b(?:в\s+наличии|на\s+складе)\s*\?\s*$)"
)
_GENERIC_PRODUCT_SELECTION_QUESTION_RE = re.compile(
    r"(?iu)\b(?:какой|какую|какие)\b[\s\S]{0,96}?"
    r"\b(?:смотреть|подобрать|выбрать|посоветовать)\w*\b"
)
_PPR_RE = re.compile(
    r"(?iu)(?<![\w-])(?:ппр|pp[-\s]?r|пп[эе]ровск\w*|полипропилен\w*)(?![\w-])"
)
_RADIATOR_MAIN_RE = re.compile(
    r"(?iu)(?<![\w-])(?:"
    r"радиаторн\w*\s+(?:магистрал\w*|контур\w*|отоплен\w*|систем\w*|разводк\w*)|"
    r"(?:на|для)\s+батаре\w*"
    r")"
)
_GLASS_FIBER_RE = re.compile(
    r"(?iu)(?:армирован\w*\s+)?(?:стекло[-\s]?(?:волок|валак)\w*|"
    r"стекло(?:волок|валак)\w*|со\s+стекл\w*)"
)
_INTERNAL_INTERNAL_RE = re.compile(
    r"(?iu)(?<![\w-])(?:"
    r"(?:вн|вр|внутренняя)\s*(?:[-/–—]|\s+)\s*(?:вн|вр|внутренняя)|"
    r"обе\s+резьб\w*\s+внутренн\w*|"
    r"внутренн\w*\s+резьб\w*\s+с\s+обеих\s+сторон"
    r")(?![\w-])"
)
_SEWER_CONTEXT_RE = re.compile(
    r"(?iu)(?<![\w-])(?:"
    r"канализац\w*|канализац[ыи]я\w*|канали(?:я|и)\w*|септик\w*|сиптик\w*|сток\w*|"
    r"туалет\w*(?:\s+труб\w*)?|"
    r"труб\w*[^.!?\n]{0,80}туалет\w*|"
    r"от\s+дом\w*[^.!?\n]{0,60}(?:на\s+улиц\w*|до\s+септик\w*)"
    r")(?![\w-])"
)
_EXTERNAL_SEWER_RE = re.compile(
    r"(?iu)(?<![\w-])(?:"
    r"наружн\w*\s+канализац\w*|"
    r"(?:на|для)\s+улиц\w*|"
    r"(?:до|к)\s+септик\w*|"
    r"наруж\w*[^.!?\n]{0,40}(?:от|из)\s+дом\w*|"
    r"(?:от|из)\s+дом\w*[^.!?\n]{0,60}(?:на\s+улиц\w*|наруж\w*|до\s+септик\w*)|"
    r"вывод\w*\s+сток\w*[^.!?\n]{0,40}(?:наруж\w*|из\s+дом\w*)"
    r")(?![\w-])"
)
_CIRCULATION_PUMP_RE = re.compile(
    r"(?iu)(?<![\w-])(?:циркуляци\w*\s+нас+ос\w*|циркуляционник\w*|"
    r"насос\w*\s+(?:для|на)\s+(?:радиатор\w*|отоплен\w*))"
)
_BALL_VALVE_RE = re.compile(
    r"(?iu)(?<![\w-])(?:valtec\s+base|кран\w*\s+base|шаров\w*\s+(?:кран\w*\s+)?(?:base|б[эе]йс))"
)
_SPOKEN_PIPE_DIAMETER_RE = re.compile(
    r"(?iu)(?<![\w-])(?:двадцать\s+пят\w*|двадцат\w+|25)(?![\w-])"
)
_SPOKEN_SEWER_DIAMETER_RE = re.compile(
    r"(?iu)(?<![\w-])(?:сто\s+десят\w*|110)(?![\w-])"
    r"(?:\s*(?:мм|миллиметр\w*|диаметр\w*))?"
)
_SPOKEN_TEMPERATURE_RE = re.compile(
    r"(?iu)(?:температур\w*|подач\w*)?[^.!?\n]{0,24}?"
    r"(?P<value>90|девяност\w*)(?:\s*(?P<unit>°?\s*[cс]|градус\w*))?"
)
_SPOKEN_PUMP_FLOW_RE = re.compile(
    r"(?iu)(?<![\w-])(?:q\s*=\s*)?(?P<value>полтора|1[,.]5)\s*"
    r"(?P<unit>куб\w*(?:\s+в\s+час)?|м\s*[³3]\s*/\s*ч)"
)
_SPOKEN_PUMP_HEAD_RE = re.compile(
    r"(?iu)(?:(?:напор\w*|h\s*=)[^.!?\n]{0,20})?"
    r"(?P<value>четыр(?:е|ё|ех|ёх)|4)\s*(?P<unit>м|метр\w*)"
    r"(?:\s+напор\w*)?"
)
_VALVE_SIZE_RE = re.compile(
    r"(?iu)(?<![\w-])(?P<value>g\s*1\s*/\s*2|dn\s*15|полдюйм\w*|1\s*/\s*2)(?![\w-])"
)


def _active_authoritative_goal(
    authoritative_state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Read the active typed goal from the bounded semantic context."""

    if not isinstance(authoritative_state, dict):
        return None
    # Keep support for narrow unit-test fixtures while production always uses
    # the versioned ``goals`` collection emitted by semantic_context().
    direct = authoritative_state.get("active_goal")
    if isinstance(direct, dict):
        return direct
    active_goal_id = authoritative_state.get("active_goal_id")
    goals = authoritative_state.get("goals")
    if not isinstance(goals, list):
        return None
    if active_goal_id is not None:
        selected = next(
            (
                item
                for item in goals
                if isinstance(item, dict)
                and item.get("goal_id") == active_goal_id
            ),
            None,
        )
        if selected is not None:
            return selected
    typed = [item for item in goals if isinstance(item, dict)]
    return typed[0] if len(typed) == 1 else None


def _bounded_fact_followup_targets_active_goal(
    current_message: str,
    authoritative_state: dict[str, Any] | None,
) -> bool:
    """Recognize a short, exact fact answer for the active typed goal."""

    active_goal = _active_authoritative_goal(authoritative_state)
    if active_goal is None:
        return False
    category = str(active_goal.get("category") or "")
    canonical_type = str(active_goal.get("canonical_type") or "").casefold()
    if category == ProductCategory.VALVES.value or canonical_type == "ball_valve":
        return bool(
            _VALVE_SIZE_RE.search(current_message)
            or _INTERNAL_INTERNAL_RE.search(current_message)
        )
    if category == ProductCategory.SEWER.value or canonical_type in {
        "sewer_pipe",
        "sewer pipe",
    }:
        return bool(
            _EXTERNAL_SEWER_RE.search(current_message)
            or _SPOKEN_SEWER_DIAMETER_RE.search(current_message)
            or _SEWER_LENGTH_ANCHOR_RE.search(current_message)
        )
    if category == ProductCategory.PIPES.value or canonical_type in {
        "pipe",
        "ppr_pipe",
        "ppr pipe",
    }:
        return bool(
            _SPOKEN_PIPE_DIAMETER_RE.search(current_message)
            or _GLASS_FIBER_RE.search(current_message)
            or _RADIATOR_MAIN_RE.search(current_message)
            or _SPOKEN_TEMPERATURE_RE.search(current_message)
        )
    if category == ProductCategory.PUMPS.value or canonical_type in {
        "circulation_pump",
        "circulation pump",
    }:
        return bool(
            _SPOKEN_PUMP_FLOW_RE.search(current_message)
            or _SPOKEN_PUMP_HEAD_RE.search(current_message)
        )
    if category == ProductCategory.RADIATOR_FITTINGS.value or canonical_type in {
        "radiator_valve",
        "radiator_valve_kit",
        "thermostatic_head",
    }:
        return bool(
            _VALVE_SIZE_RE.search(current_message)
            or _RADIATOR_VALVE_SHAPE_RE.search(current_message)
            or _THERMOSTATIC_HEAD_KIT_RE.search(current_message)
        )
    if category == ProductCategory.BOILERS.value or canonical_type in {
        "boiler",
        "gas_boiler",
        "electric_boiler",
    }:
        return _BOILER_CIRCUITS_UNKNOWN_RE.search(current_message) is not None
    return False


def _has_constraint_name(
    constraints: list[dict[str, Any]],
    names: set[str],
) -> bool:
    return any(
        _normalize_schema_identifier(item.get("name")) in names
        for item in constraints
        if isinstance(item, dict)
    )


def _append_known_constraint(
    constraints: list[dict[str, Any]],
    *,
    name: str,
    value: str | int | float | bool,
    evidence: str,
    applies_to_product: int | None,
    unit: str | None = None,
    polarity: ConstraintPolarity = ConstraintPolarity.REQUIRED,
) -> None:
    constraints.append(
        ConstraintFact(
            name=name,
            value=value,
            unit=unit,
            status=ConstraintStatus.KNOWN,
            polarity=polarity,
            applies_to_product=applies_to_product,
            evidence=evidence,
        ).model_dump(mode="json")
    )


def _canonical_registry_length_mm(
    raw_value: str,
    raw_unit: str,
) -> tuple[int | float, str] | None:
    """Convert one explicit length through the contract registry.

    This is intentionally a small adapter over ``DEFAULT_CONTRACTS`` rather
    than another unit table in the semantic layer.  The word-form handling
    only identifies the unit written by the customer; the multiplier and the
    canonical storage unit remain the catalog contract's authority.
    """

    unit_text = _normalize_evidence(raw_unit)
    if unit_text.startswith("милли"):
        unit = "mm"
    elif unit_text.startswith("сантим"):
        unit = "cm"
    elif unit_text.startswith("метр"):
        unit = "m"
    else:
        unit = normalize_unit_label(raw_unit)
    if unit is None:
        return None
    try:
        number = float(raw_value.replace(",", "."))
    except ValueError:
        return None
    definitions = [
        definition
        for contract in DEFAULT_CONTRACTS
        for definition in contract.fact_definitions
        if definition.name == "length_mm"
    ]
    if not definitions:
        return None
    conversions = definitions[0].unit_conversions
    factor = conversions.get(unit)
    canonical_unit = next(
        (label for label, multiplier in conversions.items() if multiplier == 1.0),
        None,
    )
    if factor is None or canonical_unit is None:
        return None
    converted = number * factor
    value: int | float = int(converted) if converted.is_integer() else converted
    return value, canonical_unit


def _recover_selection_preferences(
    repaired_turn: dict[str, Any],
    current_message: str,
    constraints: list[dict[str, Any]],
    normalized_products: list[dict[str, Any]],
    changes: list[str],
) -> None:
    """Recover a small high-precision preference vocabulary before V2 state.

    Legacy's price/brand ordering is useful, but it must enter V2 as a typed
    choice attached to a discovery task. These anchors do not search the
    catalogue or relax a technical coordinate. A visible-scope comparison owns
    phrases such as «какой из них дешевле?» and is excluded here so Compare
    retains its higher priority.
    """

    raw_preferences = repaired_turn.get("selection_preferences")
    if not isinstance(raw_preferences, list):
        raw_preferences = []
        repaired_turn["selection_preferences"] = raw_preferences

    # Brand values must be proved by the same closed feed vocabulary as
    # catalogue facts.  An LLM may supply a useful candidate, but an unknown
    # supplier name is not allowed to become a typed filter or ranking signal.
    current_brand_mentions = resolve_brand_mentions(current_message)
    current_brand_values = {item.canonical for item in current_brand_mentions}
    sanitized_preferences: list[dict[str, Any]] = []
    for item in raw_preferences:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "")
        if kind not in {
            SelectionPreferenceKind.BRAND_REQUIRED.value,
            SelectionPreferenceKind.BRAND_PREFERRED.value,
        }:
            sanitized_preferences.append(item)
            continue
        canonical = canonical_brand(item.get("value"))
        if canonical is None and len(current_brand_values) == 1:
            canonical = next(iter(current_brand_values))
        if canonical is None or canonical not in current_brand_values:
            changes.append("unknown_or_ungrounded_brand_preference_dropped")
            continue
        normalized = dict(item)
        normalized["value"] = canonical
        sanitized_preferences.append(normalized)
        if item.get("value") != canonical:
            changes.append("brand_preference_value_canonicalized")
    raw_preferences[:] = sanitized_preferences

    # A stock enquiry must keep an explicitly named product visible even when
    # its quantity is zero.  Models occasionally treat the words «в наличии»
    # as a selection filter, so remove only that current-turn interpretation
    # before it can reach the typed state.  Past goal facts are never touched.
    direct_stock_question = (
        _STOCK_CHECK_QUESTION_RE.search(current_message) is not None
        and _STOCK_REQUIRED_RE.search(current_message) is None
    )
    if direct_stock_question:
        raw_preferences[:] = [
            item
            for item in raw_preferences
            if not (
                isinstance(item, dict)
                and str(item.get("kind") or "")
                == SelectionPreferenceKind.STOCK_REQUIRED.value
            )
        ]
        constraints[:] = [
            item
            for item in constraints
            if not (
                str(item.get("name") or "") == "stock_availability"
                and str(item.get("polarity") or "")
                == ConstraintPolarity.REQUIRED.value
            )
        ]
        changes.append("stock_check_not_treated_as_stock_filter")

    def append(
        kind: SelectionPreferenceKind,
        evidence: str,
        value: object = None,
    ) -> None:
        grounded = _grounded_evidence_fragment(evidence, current_message)
        if grounded is None:
            return
        candidate = {"kind": kind.value, "value": value, "evidence": grounded}
        if any(
            isinstance(item, dict)
            and str(item.get("kind") or "") == kind.value
            and item.get("value") == value
            for item in raw_preferences
        ):
            return
        raw_preferences.append(candidate)
        changes.append(f"selection_preference_recovered:{kind.value}")

    def ensure_selection_execution(evidence: str) -> None:
        """Let a preference refine the current discovery task, never Compare.

        This makes a terse follow-up such as «только VALTEC» meaningful after
        a prior selection even when the LLM only classified it as a generic
        continuation.  Explicit higher-priority actions stay untouched.
        """

        acts = [
            str(getattr(item, "value", item))
            for item in (repaired_turn.get("acts") or [])
        ]
        protected = {
            CustomerAct.COMPARE.value,
            CustomerAct.CALCULATE.value,
            CustomerAct.CHECK_PRICE.value,
            CustomerAct.CHECK_STOCK.value,
        }
        if protected.intersection(acts):
            return
        if CustomerAct.FIND.value not in acts and CustomerAct.SELECT.value not in acts:
            acts.append(CustomerAct.FIND.value)
            repaired_turn["acts"] = acts
            changes.append("selection_preference_selection_action_recovered")
        controls = repaired_turn.get("selection_controls")
        if isinstance(controls, list) and not controls:
            controls.append(
                {
                    "kind": SelectionControlKind.CONTINUE_WITH_CONFIRMED_FACTS.value,
                    "evidence": evidence,
                }
            )
            repaired_turn["selection_strategy"] = {
                "kind": SelectionStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS.value,
                "evidence": evidence,
            }
            changes.append("selection_preference_selection_control_recovered")

    def explicit_brand_preference() -> tuple[SelectionPreferenceKind, str, str] | None:
        """Return one known-brand rule only when the buyer expressed one.

        Two known brands in one message are intentionally left to the LLM as
        an ambiguity: a single brand preference must never silently erase an
        expressed alternative.  A lone brand in an explicit selection request
        is a required product constraint (the Legacy ``query.brand`` meaning),
        not the store's default VALTEC tie-break.  We deliberately do not make
        the same inference in an open-ended factual or comparison question.
        """

        if len(current_brand_mentions) != 1:
            return None
        mention = current_brand_mentions[0]
        before = current_message[max(0, mention.start - 64) : mention.start]
        after = current_message[mention.end : mention.end + 64]
        if re.search(r"(?iu)\b(?:только|именно)\s*$", before):
            return (
                SelectionPreferenceKind.BRAND_REQUIRED,
                mention.alias,
                mention.canonical,
            )
        if (
            re.search(r"(?iu)\b(?:желательн\w*|предпочтительн\w*)\s*$", before)
            or re.search(r"(?iu)^\s*(?:желательн\w*|предпочтительн\w*)\b", after)
        ):
            return (
                SelectionPreferenceKind.BRAND_PREFERRED,
                mention.alias,
                mention.canonical,
            )
        current_acts = {
            str(getattr(item, "value", item))
            for item in (repaired_turn.get("acts") or [])
        }
        explicit_selection = bool(
            {CustomerAct.FIND.value, CustomerAct.SELECT.value}.intersection(
                current_acts
            )
            or _EXPLICIT_SHOW_SELECTION_RE.search(current_message)
        )
        if explicit_selection:
            return (
                SelectionPreferenceKind.BRAND_REQUIRED,
                mention.alias,
                mention.canonical,
            )
        return None

    def set_brand_constraint(
        *,
        brand: str,
        evidence: str,
        polarity: ConstraintPolarity,
        reason_code: str,
    ) -> None:
        # This is a current-turn deterministic correction.  It cannot alter a
        # fact stored by an earlier turn, but it prevents a wrong LLM brand
        # value from conflicting with an explicit buyer phrase.
        constraints[:] = [
            item for item in constraints if str(item.get("name") or "") != "brand"
        ]
        _append_known_constraint(
            constraints,
            name="brand",
            value=brand,
            evidence=evidence,
            applies_to_product=applies_to_product,
            polarity=polarity,
        )
        changes.append(reason_code)

    applies_to_product = 0 if normalized_products else None
    brand_preference = explicit_brand_preference()
    if brand_preference is not None:
        kind, evidence, brand = brand_preference
        append(kind, evidence, brand)
        set_brand_constraint(
            brand=brand,
            evidence=evidence,
            polarity=(
                ConstraintPolarity.REQUIRED
                if kind == SelectionPreferenceKind.BRAND_REQUIRED
                else ConstraintPolarity.PREFERRED
            ),
            reason_code=(
                "required_brand_constraint_recovered"
                if kind == SelectionPreferenceKind.BRAND_REQUIRED
                else "preferred_brand_constraint_recovered"
            ),
        )
        ensure_selection_execution(evidence)

    stock_required = _STOCK_REQUIRED_RE.search(current_message)
    if stock_required is not None and not direct_stock_question:
        evidence = stock_required.group(0)
        append(SelectionPreferenceKind.STOCK_REQUIRED, evidence, True)
        if not _has_constraint_name(constraints, {"stock_availability"}):
            _append_known_constraint(
                constraints,
                name="stock_availability",
                value=True,
                evidence=evidence,
                applies_to_product=applies_to_product,
            )
            changes.append("required_stock_constraint_recovered")
        ensure_selection_execution(evidence)

    price = _PRICE_PREFERENCE_RE.search(current_message)
    if price is None or _VISIBLE_SCOPE_COMPARE_RE.search(current_message) is not None:
        return
    kind = (
        SelectionPreferenceKind.PRICE_BELOW_REFERENCE
        if _RELATIVE_PRICE_RE.search(current_message) is not None
        else SelectionPreferenceKind.PRICE_LOWEST
    )
    append(kind, price.group(0))
    ensure_selection_execution(price.group(0))


def _recover_explicit_show_selection_control(
    repaired_turn: dict[str, Any],
    current_message: str,
    changes: list[str],
) -> None:
    """Turn an explicit show command into the existing typed control.

    This does not relax a technical fact.  It records the customer's explicit
    choice to see a preliminary result using only already confirmed facts.
    Direct information requests keep their higher priority and are never
    rewritten into selection here.
    """

    match = _EXPLICIT_SHOW_SELECTION_RE.search(current_message)
    if match is None:
        return
    remainder = current_message[: match.start()] + current_message[match.end() :]
    generic_whole_turn = not remainder.strip(" \t\r\n.,!?;:-—–")
    if repaired_turn.get("information_requests"):
        # A generic command such as «Что есть?» or «Покажи варианты» is not a
        # direct characteristic question.  Some model samples attach the
        # previously pending fact as a fresh information request, which lets
        # ProductFact steal the turn.  Only when the show phrase covers the
        # whole utterance (apart from punctuation) may that stale proposal be
        # removed; compound turns keep every explicit request.
        if not generic_whole_turn:
            return
        repaired_turn["information_requests"] = []
        acts = [
            str(getattr(item, "value", item))
            for item in (repaired_turn.get("acts") or [])
            if str(getattr(item, "value", item)) != CustomerAct.EXPLAIN.value
        ]
        if CustomerAct.FIND.value not in acts:
            acts.append(CustomerAct.FIND.value)
        repaired_turn["acts"] = acts
        changes.append("generic_show_stale_information_request_removed")
    if generic_whole_turn:
        evidence = match.group(0).strip()
        repaired_turn["acts"] = list(
            dict.fromkeys(
                [
                    *[
                        str(getattr(item, "value", item))
                        for item in (repaired_turn.get("acts") or [])
                        if str(getattr(item, "value", item))
                        not in {
                            CustomerAct.EXPLAIN.value,
                            CustomerAct.CHECK_STOCK.value,
                        }
                    ],
                    CustomerAct.FIND.value,
                ]
            )
        )
        repaired_turn["selection_controls"] = [
            {
                "kind": SelectionControlKind.CONTINUE_WITH_CONFIRMED_FACTS.value,
                "evidence": evidence,
            }
        ]
        repaired_turn["selection_strategy"] = {
            "kind": SelectionStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS.value,
            "evidence": evidence,
        }
        ambiguities = repaired_turn.get("ambiguities")
        if isinstance(ambiguities, list):
            repaired_turn["ambiguities"] = [
                item
                for item in ambiguities
                if not (
                    isinstance(item, dict)
                    and "selection" in str(item.get("kind") or "").casefold()
                )
            ]
        changes.append("explicit_show_selection_control_recovered")
        changes.append("generic_show_anchor_forced_continue")
        return
    raw_strategy = repaired_turn.get("selection_strategy")
    strategy_kind = (
        str(getattr(raw_strategy.get("kind"), "value", raw_strategy.get("kind")))
        if isinstance(raw_strategy, dict)
        else ""
    )
    raw_controls = repaired_turn.get("selection_controls")
    if strategy_kind == SelectionStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS.value:
        return
    if raw_controls or strategy_kind not in {
        "",
        SelectionStrategyKind.STANDARD.value,
    }:
        return
    evidence = match.group(0).strip()
    control = {
        "kind": SelectionControlKind.CONTINUE_WITH_CONFIRMED_FACTS.value,
        "evidence": evidence,
    }
    repaired_turn["selection_controls"] = [control]
    repaired_turn["selection_strategy"] = {
        "kind": SelectionStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS.value,
        "evidence": evidence,
    }
    changes.append("explicit_show_selection_control_recovered")


def _recover_bounded_selection_category_and_facts(
    repaired_turn: dict[str, Any],
    current_message: str,
    normalized_products: list[dict[str, Any]],
    constraints: list[dict[str, Any]],
    authoritative_state: dict[str, Any] | None,
    changes: list[str],
    catalog_sku_anchors: tuple[CatalogSkuAnchor[Any], ...] = (),
) -> None:
    """Recover a small closed vocabulary of unambiguous selection facts.

    Each recovered value is backed by an exact current-turn fragment.  The
    helper deliberately covers only the phrases required by the accepted QA
    scenarios; it is not a fuzzy product or category resolver.
    """

    active_goal = _active_authoritative_goal(authoritative_state)
    active_category = str((active_goal or {}).get("category") or "")
    active_type = str((active_goal or {}).get("canonical_type") or "")
    current_operation = str(
        getattr(
            repaired_turn.get("operation"),
            "value",
            repaired_turn.get("operation") or "",
        )
    )
    radiator_main = _RADIATOR_MAIN_RE.search(current_message)
    if active_category == ProductCategory.PIPES.value and radiator_main is not None:
        # In an established pipe task, phrases such as «для батарей» describe
        # the pipe service.  A semantic-model radiator mention grounded only
        # in that phrase must not open a competing radiator goal.  Preserve a
        # real topic switch such as «нужен радиатор для батарей» by looking for
        # an explicit radiator noun outside the application span.
        residual = (
            current_message[: radiator_main.start()]
            + " "
            + current_message[radiator_main.end() :]
        )
        explicit_radiator_outside_service = re.search(
            r"(?iu)(?<![\w-])(?:радиатор\w*|батаре(?:я|ю|и))(?![\w-])",
            residual,
        )
        if explicit_radiator_outside_service is None:
            retained_products = [
                item
                for item in normalized_products
                if not (
                    item.get("role") == ProductRole.TARGET.value
                    and item.get("category") == ProductCategory.RADIATORS.value
                )
            ]
            if len(retained_products) != len(normalized_products):
                normalized_products[:] = retained_products
                changes.append("pipe_service_radiator_product_false_positive_dropped")

    # A short answer to an established radiator-valve task may say
    # ``с термоголовкой``.  It is a requirement for the same assembly, not a
    # new conversation topic.  Keeping an LLM-proposed ``radiator_valve_kit``
    # as a second target would make unscoped facts such as ``1/2`` bind to a
    # newly-created goal, leaving the actual valve task unchanged.  Preserve
    # the component requirement below and let the facts inherit the active
    # valve goal instead.  An explicit new/switch request remains a real new
    # target and is deliberately not changed here.
    if (
        active_category == ProductCategory.RADIATOR_FITTINGS.value
        and active_type.casefold()
        in {"radiator_valve", "radiator valve", "radiator_valve_kit", "radiator valve kit"}
        and current_operation in {GoalOperation.CONTINUE.value, GoalOperation.REFINE.value}
        and _THERMOSTATIC_HEAD_KIT_RE.search(current_message) is not None
    ):
        retained_products = [
            item
            for item in normalized_products
            if not (
                item.get("role") == ProductRole.TARGET.value
                and str(item.get("canonical_type") or "").casefold()
                in {"radiator_valve_kit", "radiator valve kit"}
            )
        ]
        if len(retained_products) != len(normalized_products):
            normalized_products[:] = retained_products
            changes.append("radiator_valve_kit_followup_bound_to_active_goal")
    target_indexes = [
        index
        for index, item in enumerate(normalized_products)
        if item.get("role") == ProductRole.TARGET.value
    ]
    target_index = target_indexes[0] if len(target_indexes) == 1 else None
    # The LLM can omit a canonical type for a valid article, especially for
    # numeric and slash-only SKUs. A catalogue-bound anchor is the authority
    # for recovering that product scope. Keep the older structured-token
    # fallback only when no catalogue was supplied, so it can never widen
    # numeric/slash extraction in the live path.
    resolved_catalog_anchors = tuple(
        anchor
        for anchor in catalog_sku_anchors
        if anchor.canonical_sku is not None
        and anchor.resolution.status.value in {"exact", "unique_prefix"}
    )
    catalog_anchor = (
        resolved_catalog_anchors[0]
        if len(resolved_catalog_anchors) == 1
        else None
    )
    fallback_sku_tokens = [
        match.group(0)
        for match in _MIXED_IDENTIFIER_TOKEN_RE.finditer(current_message)
        if "." in match.group(0) and any(char.isdigit() for char in match.group(0))
    ]
    fallback_sku = (
        fallback_sku_tokens[0]
        if not catalog_sku_anchors and len(fallback_sku_tokens) == 1
        else None
    )
    explicit_sku = catalog_anchor.text if catalog_anchor is not None else fallback_sku
    canonical_sku = (
        catalog_anchor.canonical_sku if catalog_anchor is not None else explicit_sku
    )
    if explicit_sku is not None and canonical_sku is not None:
        if not normalized_products:
            normalized_products.append(
                ProductMention(
                    text=explicit_sku,
                    canonical_type="catalog_product",
                    category=ProductCategory.OTHER,
                    role=ProductRole.TARGET,
                    evidence=explicit_sku,
                ).model_dump(mode="json")
            )
            target_index = 0
            changes.append(
                "catalog_bound_sku_product_scope_recovered"
                if catalog_anchor is not None
                else "explicit_sku_product_scope_recovered"
            )
        if not _has_constraint_name(constraints, {"sku", "article", "артикул"}):
            _append_known_constraint(
                constraints,
                name="sku",
                value=canonical_sku,
                evidence=explicit_sku,
                applies_to_product=target_index,
            )
            changes.append(
                "catalog_bound_sku_constraint_recovered"
                if catalog_anchor is not None
                else "explicit_sku_constraint_recovered"
            )
        if active_goal is not None:
            repaired_turn["operation"] = GoalOperation.SWITCH.value
            changes.append(
                "catalog_bound_sku_overrode_stale_goal"
                if catalog_anchor is not None
                else "explicit_sku_overrode_stale_goal"
            )

    pump_match = _CIRCULATION_PUMP_RE.search(current_message)
    if pump_match is not None and not normalized_products:
        normalized_products.append(
            ProductMention(
                text=pump_match.group(0),
                canonical_type="circulation_pump",
                category=ProductCategory.PUMPS,
                role=ProductRole.TARGET,
                evidence=pump_match.group(0),
            ).model_dump(mode="json")
        )
        target_index = 0
        changes.append("pump_product_goal_recovered")

    valve_match = _BALL_VALVE_RE.search(current_message)
    if valve_match is not None and not normalized_products:
        normalized_products.append(
            ProductMention(
                text=valve_match.group(0),
                canonical_type="ball_valve",
                category=ProductCategory.VALVES,
                role=ProductRole.TARGET,
                evidence=valve_match.group(0),
            ).model_dump(mode="json")
        )
        target_index = 0
        changes.append("valve_product_goal_recovered")

    sewer_match = _SEWER_CONTEXT_RE.search(current_message)
    external_match = _EXTERNAL_SEWER_RE.search(current_message)
    has_pipe_scope = bool(
        active_category in {ProductCategory.PIPES.value, ProductCategory.SEWER.value}
        or active_type.casefold() in {"pipe", "ppr pipe", "sewer pipe"}
        or any(
            str(item.get("category") or "")
            in {ProductCategory.PIPES.value, ProductCategory.SEWER.value}
            or str(item.get("canonical_type") or "").casefold()
            in {"pipe", "ppr pipe", "sewer pipe"}
            for item in normalized_products
        )
    )
    if (sewer_match is not None or external_match is not None) and (
        has_pipe_scope
        or "труб" in current_message.casefold()
        or any(
            marker in current_message.casefold().replace("ё", "е")
            for marker in ("канал", "септик", "сиптик", "сток")
        )
    ):
        evidence_match = sewer_match or external_match
        assert evidence_match is not None
        evidence = evidence_match.group(0).strip()
        if target_index is None and not normalized_products:
            normalized_products.append(
                ProductMention(
                    text=evidence,
                    canonical_type="sewer_pipe",
                    category=ProductCategory.SEWER,
                    role=ProductRole.TARGET,
                    evidence=evidence,
                ).model_dump(mode="json")
            )
            target_index = 0
        elif target_index is not None:
            normalized_products[target_index]["canonical_type"] = "sewer_pipe"
            normalized_products[target_index]["category"] = ProductCategory.SEWER.value
            if _grounded_evidence_fragment(
                str(normalized_products[target_index].get("evidence") or ""),
                current_message,
            ) is None:
                normalized_products[target_index]["evidence"] = evidence
                normalized_products[target_index]["text"] = evidence
                changes.append("product_evidence_rebound_to_current_message")
        if active_category == ProductCategory.PIPES.value:
            repaired_turn["operation"] = GoalOperation.CORRECT.value
        changes.append("external_sewer_goal_recovered")
        if external_match is not None and not _has_constraint_name(
            constraints,
            {"sewer_scope", "installation_scope", "sewer_type"},
        ):
            _append_known_constraint(
                constraints,
                name="sewer_scope",
                value="external",
                evidence=external_match.group(0).strip(),
                applies_to_product=target_index,
            )
            changes.append("external_sewer_scope_recovered")

    product_types = {
        str(item.get("canonical_type") or "").casefold()
        for item in normalized_products
    }
    product_categories = {
        str(item.get("category") or "") for item in normalized_products
    }
    ppr_match = _PPR_RE.search(current_message)
    if ppr_match is not None and not normalized_products:
        normalized_products.append(
            ProductMention(
                text=ppr_match.group(0),
                canonical_type="pipe",
                category=ProductCategory.PIPES,
                role=ProductRole.TARGET,
                evidence=ppr_match.group(0),
            ).model_dump(mode="json")
        )
        target_index = 0
        product_types.add("pipe")
        product_categories.add(ProductCategory.PIPES.value)
        changes.append("ppr_product_goal_recovered")
    pipe_target = bool(
        product_types.intersection(
            {
                "pipe",
                "ppr pipe",
                "polypropylene pipe",
                "sewer_pipe",
                "sewer pipe",
            }
        )
        or product_categories.intersection(
            {ProductCategory.PIPES.value, ProductCategory.SEWER.value}
        )
        or active_category
        in {ProductCategory.PIPES.value, ProductCategory.SEWER.value}
    )
    sewer_target = bool(
        product_types.intersection({"sewer_pipe", "sewer pipe"})
        or ProductCategory.SEWER.value in product_categories
        or active_category == ProductCategory.SEWER.value
        or active_type.casefold() in {"sewer_pipe", "sewer pipe"}
    )
    sewer_length = _SEWER_LENGTH_ANCHOR_RE.search(current_message)
    if (
        sewer_target
        and sewer_length is not None
        and not _has_constraint_name(constraints, {"length_mm", "length", "pipe_length"})
    ):
        canonical_length = _canonical_registry_length_mm(
            sewer_length.group("value"),
            sewer_length.group("unit"),
        )
        if canonical_length is not None:
            value, unit = canonical_length
            _append_known_constraint(
                constraints,
                name="length_mm",
                value=value,
                unit=unit,
                evidence=sewer_length.group(0).strip(),
                applies_to_product=target_index,
            )
            changes.append("sewer_length_anchor_recovered")

    pending = (authoritative_state or {}).get("pending_decision_question")
    pending_name = (
        _canonical_constraint_fact_name(str(pending.get("fact_name") or ""))
        if isinstance(pending, dict)
        else ""
    )
    if sewer_target and pending_name == "diameter_mm":
        spoken_metric_mentions = extract_spoken_cardinal_mentions(
            current_message,
            minimum=1,
            maximum=1000,
        )
        metric_candidates: list[tuple[float, int, int]] = []
        for mention in spoken_metric_mentions:
            unit_match = _SPOKEN_MILLIMETRE_UNIT_RE.match(
                current_message[mention.end :]
            )
            if unit_match is None:
                continue
            metric_candidates.append(
                (mention.value, mention.start, mention.end + unit_match.end())
            )
        if len(metric_candidates) == 1:
            raw_value, start, end = metric_candidates[0]
            value = int(raw_value) if raw_value.is_integer() else raw_value
            evidence = current_message[start:end].strip()
            matching = [
                item
                for item in constraints
                if _normalize_schema_identifier(item.get("name"))
                in {"diameter_mm", "diameter"}
                and item.get("status") == ConstraintStatus.KNOWN.value
            ]
            if len(matching) <= 1:
                constraints[:] = [
                    item
                    for item in constraints
                    if _normalize_schema_identifier(item.get("name"))
                    not in {"diameter_mm", "diameter"}
                ]
                _append_known_constraint(
                    constraints,
                    name="diameter_mm",
                    value=value,
                    unit="mm",
                    evidence=evidence,
                    applies_to_product=target_index,
                )
                repaired_turn["answers_pending_question"] = True
                if current_operation in {
                    GoalOperation.NEW.value,
                    GoalOperation.UNKNOWN.value,
                }:
                    repaired_turn["operation"] = GoalOperation.CONTINUE.value
                changes.append("pending_spoken_metric_answer_recovered")
            else:
                changes.append("pending_spoken_metric_answer_ambiguous")

    def ontology_evidence(aliases: tuple[str, ...]) -> str | None:
        normalized_message = _normalize_evidence(current_message)
        for alias in sorted(aliases, key=len, reverse=True):
            normalized_alias = _normalize_evidence(alias)
            if not normalized_alias:
                continue
            # Closed numeric aliases such as ``1`` for a boiler circuit must
            # be whole literals.  A substring match against ``150 м²`` would
            # otherwise invent an unrelated one-circuit requirement.
            if normalized_alias.isdecimal():
                match = re.search(
                    rf"(?<![\w]){re.escape(alias)}(?![\w])",
                    current_message,
                    flags=re.IGNORECASE,
                )
                if match is not None:
                    return match.group(0)
                continue
            if normalized_alias in normalized_message:
                match = re.search(re.escape(alias), current_message, flags=re.IGNORECASE)
                return match.group(0) if match is not None else alias
        return None

    radiator_target = bool(
        product_types.intersection({"radiator", "heating radiator"})
        or ProductCategory.RADIATORS.value in product_categories
        or active_category == ProductCategory.RADIATORS.value
        or active_type.casefold() in {"radiator", "heating radiator"}
    )
    if radiator_target:
        material_matches = [
            (value, ontology_evidence(closed_value_aliases("radiator", "material", value)))
            for value in ("биметалл", "aluminium", "сталь")
        ]
        grounded_materials = [
            (value, evidence)
            for value, evidence in material_matches
            if evidence is not None
        ]
        if len(grounded_materials) == 1:
            material, evidence = grounded_materials[0]
            # An explicit material is a correction of a prior unknown, not a
            # competing requirement.  It is only recovered inside an already
            # typed radiator task and from an approved exact alias.
            constraints[:] = [
                item
                for item in constraints
                if _normalize_schema_identifier(item.get("name")) != "material"
            ]
            _append_known_constraint(
                constraints,
                name="material",
                value=material,
                evidence=str(evidence),
                applies_to_product=target_index,
            )
            changes.append("radiator_material_recovered_from_explicit_alias")

    boiler_target = bool(
        product_types.intersection({"boiler", "gas_boiler", "electric_boiler"})
        or ProductCategory.BOILERS.value in product_categories
        or active_category == ProductCategory.BOILERS.value
        or active_type.casefold() in {"boiler", "gas_boiler", "electric_boiler"}
    )
    if boiler_target:
        # A short reply to the just asked fuel question (``Газовый, только
        # отопление``) carries two independent closed ontology facts.  The
        # LLM occasionally retained the circuit fact and dropped the fuel
        # fact, which made the policy ask the same question again.  Recover a
        # fuel type only inside an established boiler task and only from a
        # closed value alias; it cannot classify an arbitrary mention of gas
        # as a boiler requirement.
        grounded_boiler_types = [
            (
                value,
                ontology_evidence(closed_value_aliases("boiler", "boiler_type", value)),
            )
            for value in ("gas", "electric", "solid_fuel")
        ]
        grounded_boiler_types = [
            (value, evidence)
            for value, evidence in grounded_boiler_types
            if evidence is not None
        ]
        if (
            len(grounded_boiler_types) == 1
            and not _has_constraint_name(
                constraints,
                {"boiler_type", "fuel_type", "boiler_fuel_type"},
            )
        ):
            boiler_type, evidence = grounded_boiler_types[0]
            _append_known_constraint(
                constraints,
                name="boiler_type",
                value=boiler_type,
                evidence=str(evidence),
                applies_to_product=target_index,
            )
            changes.append("boiler_type_recovered_from_closed_alias")

        # The registry also contains compact aliases ("1", "2", "one", "two")
        # for structured model output.  They are not safe linguistic evidence on
        # their own: "2 кВт" is power, not a two-circuit requirement.  Likewise,
        # bare "ГВС" may describe nearby equipment rather than the customer's
        # demand.  Only an unambiguous circuit phrase may trigger deterministic
        # recovery; the schema validator remains responsible for every other
        # candidate proposed by the LLM.
        unsafe_standalone_circuit_aliases = {
            "1",
            "2",
            "one",
            "two",
            "один",
            "два",
            "гвс",
            "с гвс",
        }

        def grounded_circuit_evidence(value: int) -> str | None:
            aliases = tuple(
                alias
                for alias in closed_value_aliases("boiler", "circuits", value)
                if _normalize_evidence(alias)
                not in unsafe_standalone_circuit_aliases
            )
            return ontology_evidence(aliases)

        grounded_circuits = [
            (
                value,
                grounded_circuit_evidence(value),
            )
            for value in (1, 2)
        ]
        grounded_circuits = [
            (value, evidence)
            for value, evidence in grounded_circuits
            if evidence is not None
        ]
        if len(grounded_circuits) == 1:
            circuits, evidence = grounded_circuits[0]
            # A direct answer to the pending contour question is a confirmed
            # customer requirement, not an explanation of how to determine
            # it.  Resolve it only in an established boiler task and from a
            # closed ontology alias; an unrelated pipe's hot-water service
            # therefore cannot create a boiler fact.
            constraints[:] = [
                item
                for item in constraints
                if _normalize_schema_identifier(item.get("name")) != "circuits"
            ]
            _append_known_constraint(
                constraints,
                name="circuits",
                value=circuits,
                evidence=str(evidence),
                applies_to_product=target_index,
            )
            changes.append("boiler_circuits_recovered_from_closed_alias")

        unknown_circuits = _BOILER_CIRCUITS_UNKNOWN_RE.search(current_message)
        if (
            unknown_circuits is not None
            and not _has_constraint_name(constraints, {"circuits"})
        ):
            # This is a bounded terminal answer to the current question, not
            # an absence of all boiler facts.  The reducer's monotonic merge
            # then leaves fuel, power and every other accepted requirement in
            # the active boiler goal untouched.
            constraints.append(
                ConstraintFact(
                    name="circuits",
                    value=None,
                    status=ConstraintStatus.UNKNOWN,
                    polarity=ConstraintPolarity.REQUIRED,
                    applies_to_product=target_index,
                    evidence=unknown_circuits.group(0).strip(),
                ).model_dump(mode="json")
            )
            repaired_turn["answers_pending_question"] = True
            changes.append("boiler_circuits_unknown_recovered")

    spoken_diameter = _SPOKEN_PIPE_DIAMETER_RE.search(current_message)
    if (
        pipe_target
        and spoken_diameter is not None
        and not _has_constraint_name(constraints, {"diameter_mm", "diameter"})
    ):
        _append_known_constraint(
            constraints,
            name="diameter_mm",
            value=25,
            unit="mm",
            evidence=spoken_diameter.group(0),
            applies_to_product=target_index,
        )
        changes.append("spoken_numeric_anchor_recovered")
    spoken_sewer_diameter = _SPOKEN_SEWER_DIAMETER_RE.search(current_message)
    sewer_diameter_constraints = [
        item
        for item in constraints
        if _normalize_schema_identifier(item.get("name"))
        in {"diameter_mm", "diameter"}
        and item.get("status") == ConstraintStatus.KNOWN.value
    ]
    if (
        pipe_target
        and (
            active_category == ProductCategory.SEWER.value
            or ProductCategory.SEWER.value in product_categories
            or product_types.intersection({"sewer_pipe", "sewer pipe"})
        )
        and spoken_sewer_diameter is not None
    ):
        evidence = spoken_sewer_diameter.group(0).strip()
        if len(sewer_diameter_constraints) == 1:
            constraint = sewer_diameter_constraints[0]
            constraint.update(
                {
                    "name": "diameter_mm",
                    "value": 110,
                    "unit": "mm",
                    "evidence": evidence,
                }
            )
            changes.append("spoken_sewer_diameter_anchor_canonicalized")
        elif not sewer_diameter_constraints:
            _append_known_constraint(
                constraints,
                name="diameter_mm",
                value=110,
                unit="mm",
                evidence=evidence,
                applies_to_product=target_index,
            )
            changes.append("spoken_sewer_diameter_anchor_recovered")
        else:
            changes.append("spoken_sewer_diameter_anchor_ambiguous")
    radiator_ontology_evidence = ontology_evidence(
        closed_value_aliases("pipe", "pipe_service", "heating")
    )
    if (
        pipe_target
        and (radiator_main is not None or radiator_ontology_evidence is not None)
        and not _has_constraint_name(
            constraints,
            {"pipe_service", "application", "application_type", "service_type"},
        )
    ):
        _append_known_constraint(
            constraints,
            name="pipe_service",
            value="heating",
            evidence=(
                radiator_main.group(0)
                if radiator_main is not None
                else str(radiator_ontology_evidence)
            ),
            applies_to_product=target_index,
        )
        changes.append("pipe_service_recovered_from_radiator_main")
    glass = _GLASS_FIBER_RE.search(current_message)
    glass_ontology_evidence = ontology_evidence(
        closed_value_aliases("pipe", "reinforcement", "glass_fiber")
    )
    if (
        pipe_target
        and (glass is not None or glass_ontology_evidence is not None)
        and not _has_constraint_name(
            constraints,
            {"reinforcement", "reinforcement_type", "pipe_reinforcement"},
        )
    ):
        _append_known_constraint(
            constraints,
            name="reinforcement",
            value="glass_fiber",
            evidence=(
                glass.group(0)
                if glass is not None
                else str(glass_ontology_evidence)
            ),
            applies_to_product=target_index,
        )
        changes.append("glass_fiber_reinforcement_recovered")

    spoken_temperature = _SPOKEN_TEMPERATURE_RE.search(current_message)
    if (
        pipe_target
        and spoken_temperature is not None
        and not _has_constraint_name(
            constraints,
            {"operating_temperature_c", "maximum_operating_temperature_c"},
        )
    ):
        _append_known_constraint(
            constraints,
            name="operating_temperature_c",
            value=90,
            unit="c",
            evidence=spoken_temperature.group(0).strip(),
            applies_to_product=target_index,
        )
        changes.append("spoken_numeric_anchor_recovered")

    pump_target = bool(
        product_types.intersection({"circulation_pump", "circulation pump"})
        or ProductCategory.PUMPS.value in product_categories
        or active_type.casefold() in {"circulation_pump", "circulation pump"}
    )
    spoken_flow = _SPOKEN_PUMP_FLOW_RE.search(current_message)
    if pump_target and spoken_flow is not None:
        constraints[:] = [
            item
            for item in constraints
            if _normalize_schema_identifier(item.get("name"))
            not in {"duty_point_flow_l_h", "max_flow_l_h", "flow"}
        ]
        _append_known_constraint(
            constraints,
            name="duty_point_flow_l_h",
            value=1.5,
            unit="m3/h",
            evidence=spoken_flow.group(0).strip(),
            applies_to_product=target_index,
        )
        changes.append("spoken_numeric_anchor_recovered")
    spoken_head = _SPOKEN_PUMP_HEAD_RE.search(current_message)
    if pump_target and spoken_head is not None:
        constraints[:] = [
            item
            for item in constraints
            if _normalize_schema_identifier(item.get("name"))
            not in {"duty_point_head_m", "max_head_m", "head"}
        ]
        _append_known_constraint(
            constraints,
            name="duty_point_head_m",
            value=4,
            unit="m",
            evidence=spoken_head.group(0).strip(),
            applies_to_product=target_index,
        )
        changes.append("spoken_numeric_anchor_recovered")

    ball_valve_target = bool(
        product_types.intersection({"ball valve", "ball_valve", "valve"})
        or ProductCategory.VALVES.value in product_categories
        or active_category == ProductCategory.VALVES.value
    )
    radiator_valve_target = bool(
        product_types.intersection(
            {"radiator_valve", "radiator valve", "radiator_valve_kit", "radiator valve kit"}
        )
        or ProductCategory.RADIATOR_FITTINGS.value in product_categories
        or active_category == ProductCategory.RADIATOR_FITTINGS.value
        or active_type.casefold()
        in {"radiator_valve", "radiator valve", "radiator_valve_kit", "radiator valve kit"}
    )
    kit_match = _THERMOSTATIC_HEAD_KIT_RE.search(current_message)
    if radiator_valve_target and kit_match is not None:
        # A thermic head is a typed requirement for the same valve assembly.
        # Do not turn a bounded follow-up into another target goal: the
        # reducer must retain the established task so all facts are bound and
        # readiness can ask only for the next genuinely missing interface
        # fact.
        if not _has_constraint_name(
            constraints,
            {"thermostatic_head", "thermostatic_head_required"},
        ):
            _append_known_constraint(
                constraints,
                name="thermostatic_head",
                value=True,
                evidence=kit_match.group(0).strip(),
                applies_to_product=target_index,
            )
        changes.append("radiator_valve_kit_requirement_recovered")

    if ball_valve_target and re.search(r"\bтип\s+резьб\w*\b", current_message, re.IGNORECASE):
        terminal_statuses = {
            ConstraintStatus.UNKNOWN.value,
            ConstraintStatus.REFUSED.value,
            ConstraintStatus.DEFERRED.value,
        }
        for item in constraints:
            if (
                _normalize_schema_identifier(item.get("name")) == "connection_size"
                and item.get("status") in terminal_statuses
            ):
                # «Тип резьбы» names the two-port pattern, not the already
                # known nominal size.  The explicit phrase is narrow enough
                # to repair this recurrent model label drift safely.
                item["name"] = "connection_pattern"
                changes.append("valve_thread_type_unknown_rebound_to_pattern")
    connection_pair = _INTERNAL_INTERNAL_RE.search(current_message)
    connection_ontology_evidence = ontology_evidence(
        closed_value_aliases("ball_valve", "connection_pattern", "female_female")
    )
    if (
        ball_valve_target
        and (
            connection_pair is not None
            or connection_ontology_evidence is not None
        )
        and not _has_constraint_name(
            constraints,
            {"connection_pattern", "thread_pair", "connection_type", "thread_type"},
        )
    ):
        _append_known_constraint(
            constraints,
            name="connection_pattern",
            value="female_female",
            evidence=(
                connection_pair.group(0)
                if connection_pair is not None
                else str(connection_ontology_evidence)
            ),
            applies_to_product=target_index,
        )
        changes.append("connection_pattern_recovered_from_explicit_pair")
    valve_size = _VALVE_SIZE_RE.search(current_message)
    if (
        (ball_valve_target or radiator_valve_target)
        and valve_size is not None
        and not _has_constraint_name(constraints, {"connection_size", "thread_size"})
    ):
        _append_known_constraint(
            constraints,
            name="connection_size",
            value="1/2",
            evidence=valve_size.group(0),
            applies_to_product=target_index,
        )
        changes.append(
            "radiator_valve_connection_size_recovered"
            if radiator_valve_target and not ball_valve_target
            else "valve_connection_size_recovered"
        )
    radiator_shape = _RADIATOR_VALVE_SHAPE_RE.search(current_message)
    if (
        radiator_valve_target
        and radiator_shape is not None
        and not _has_constraint_name(constraints, {"valve_shape", "shape", "body_shape"})
    ):
        shape_text = radiator_shape.group("shape").casefold().replace("ё", "е")
        _append_known_constraint(
            constraints,
            name="valve_shape",
            value="straight" if shape_text.startswith("прям") else "angle",
            evidence=radiator_shape.group(0),
            applies_to_product=target_index,
            polarity=ConstraintPolarity.PREFERRED,
        )
        changes.append("radiator_valve_shape_recovered")


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
    if category == ProductCategory.RADIATORS.value or canonical_type in {
        "radiator",
        "heating_radiator",
    }:
        return "radiator"
    if "радиатор" in canonical_type:
        return "radiator"
    if category == ProductCategory.PIPES.value and (
        canonical_type in {"pipe", "pex_pipe", "труба"}
        or "pex" in canonical_type
        or "pe-x" in canonical_type
    ):
        return "pipe"
    return None


def _non_known_fact_definitions(
    constraint: dict[str, Any],
    products: list[dict[str, Any]],
    authoritative_hints: tuple[dict[str, str], ...],
) -> list[dict[str, Any]] | None:
    """Return product-scoped declarative vocabulary, or None for compatibility."""

    product_index = constraint.get("applies_to_product")
    scoped_product: dict[str, Any] | None = None
    if (
        isinstance(product_index, int)
        and not isinstance(product_index, bool)
        and 0 <= product_index < len(products)
    ):
        scoped_product = products[product_index]
    elif len(products) == 1:
        scoped_product = products[0]
    elif not products and len(authoritative_hints) == 1:
        scoped_product = authoritative_hints[0]
    if scoped_product is None:
        # Multiple typed products with vocabularies are an unresolved scope,
        # not a legacy product type without a vocabulary.  An empty definition
        # set therefore makes the caller reject instead of failing open.
        vocabulary = semantic_ontology_payload().get("constraint_vocabulary") or {}
        if any(
            vocabulary.get(
                _normalize_schema_identifier(item.get("canonical_type"))
            )
            is not None
            for item in (*products, *authoritative_hints)
        ):
            return []
        return None

    product_type = _normalize_schema_identifier(
        scoped_product.get("canonical_type")
    )
    vocabulary = semantic_ontology_payload().get("constraint_vocabulary") or {}
    definitions = vocabulary.get(product_type)
    if definitions is None:
        family = _product_family(scoped_product)
        definitions = vocabulary.get(family) if family is not None else None
    return definitions if isinstance(definitions, list) else None


def _non_known_alias_matches(
    definitions: list[dict[str, Any]],
    evidence: str,
) -> set[str]:
    matches: set[str] = set()
    for definition in definitions:
        if not isinstance(definition, dict):
            continue
        fact_name = _normalize_schema_identifier(definition.get("name"))
        if not fact_name:
            continue
        aliases = [definition.get("name"), *(definition.get("aliases") or ())]
        if any(
            isinstance(alias, str)
            and _declared_alias_matches_text(alias, evidence)
            for alias in aliases
        ):
            matches.add(fact_name)
    return matches


_NON_KNOWN_GROUP_COORDINATORS = frozenset(
    {"and", "both", "nor", "и", "ни", "оба", "обе"}
)
_SEMANTIC_GROUP_RE = re.compile(r"[^.!?;\n—–]+")


def _non_known_alias_spans(
    definitions: list[dict[str, Any]],
    evidence: str,
) -> dict[str, tuple[int, int]]:
    """Locate the strongest declared alias for each fact in exact evidence."""

    text_tokens = _source_tokens(evidence)
    strongest: dict[str, tuple[int, int, int]] = {}
    for definition in definitions:
        if not isinstance(definition, dict):
            continue
        fact_name = _normalize_schema_identifier(definition.get("name"))
        if not fact_name:
            continue
        aliases = [definition.get("name"), *(definition.get("aliases") or ())]
        for alias in aliases:
            if not isinstance(alias, str):
                continue
            alias_tokens = [item[0] for item in _source_tokens(alias)]
            if not alias_tokens or len(alias_tokens) > len(text_tokens):
                continue
            width = len(alias_tokens)
            for start in range(len(text_tokens) - width + 1):
                window = text_tokens[start : start + width]
                if not all(
                    _source_tokens_match(alias_token, source_token[0])
                    for alias_token, source_token in zip(alias_tokens, window)
                ):
                    continue
                score = width * 1000 + len(_normalize_evidence(alias))
                candidate = (score, window[0][1], window[-1][2])
                if candidate > strongest.get(fact_name, (-1, -1, -1)):
                    strongest[fact_name] = candidate
    return {
        fact_name: (start, end)
        for fact_name, (_score, start, end) in strongest.items()
    }


def _coordinated_non_known_group(
    definitions: list[dict[str, Any]],
    evidence: str,
) -> tuple[str, tuple[str, ...]] | None:
    """Resolve one status governing a fully coordinated group of fact aliases."""

    status_spans: set[tuple[str, int, int]] = set()
    for status, patterns in _EXPLICIT_NON_KNOWN_PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(evidence):
                status_spans.add((status, match.start(), match.end()))
    if len(status_spans) != 1:
        return None
    status, status_start, status_end = next(iter(status_spans))

    alias_spans = _non_known_alias_spans(definitions, evidence)
    if len(alias_spans) < 2:
        return None
    all_before = all(end <= status_start for start, end in alias_spans.values())
    all_after = all(start >= status_end for start, end in alias_spans.values())
    if not (all_before or all_after):
        return None

    first_alias = min(start for start, _end in alias_spans.values())
    last_alias = max(end for _start, end in alias_spans.values())
    group_tokens = {
        token for token, _start, _end in _source_tokens(evidence[first_alias:last_alias])
    }
    if not group_tokens.intersection(_NON_KNOWN_GROUP_COORDINATORS):
        return None
    return status, tuple(sorted(alias_spans))


def _pending_non_known_fact_name(
    authoritative_state: dict[str, Any] | None,
    definitions: list[dict[str, Any]],
) -> str | None:
    pending = (authoritative_state or {}).get("pending_decision_question")
    if not isinstance(pending, dict):
        return None
    pending_name = _canonical_constraint_fact_name(
        str(pending.get("fact_name") or "")
    )
    allowed_names = {
        _normalize_schema_identifier(item.get("name"))
        for item in definitions
        if isinstance(item, dict)
    }
    normalized_pending = _normalize_schema_identifier(pending_name)
    return pending_name if normalized_pending in allowed_names else None


def _grounded_non_known_fact_name(
    constraint: dict[str, Any],
    products: list[dict[str, Any]],
    authoritative_hints: tuple[dict[str, str], ...],
    authoritative_state: dict[str, Any] | None,
) -> tuple[Literal["compatible", "grounded", "rebound", "reject"], str | None]:
    """Validate a non-known fact name against one declarative product scope.

    Product types without a vocabulary retain backward compatibility.  Once a
    vocabulary exists, zero or multiple current-turn aliases fail closed.  An
    ellipsis may bind only to a committed typed pending decision fact.
    """

    if constraint.get("status") == ConstraintStatus.KNOWN.value:
        return "compatible", None
    definitions = _non_known_fact_definitions(
        constraint,
        products,
        authoritative_hints,
    )
    if definitions is None:
        return "compatible", None

    matches = _non_known_alias_matches(
        definitions,
        str(constraint.get("evidence") or ""),
    )
    if len(matches) == 1:
        grounded_name = next(iter(matches))
        proposed_name = _canonical_constraint_fact_name(
            str(constraint.get("name") or "")
        )
        if _normalize_schema_identifier(proposed_name) == grounded_name:
            return "grounded", proposed_name
        return "rebound", grounded_name
    if not matches:
        pending_name = _pending_non_known_fact_name(
            authoritative_state,
            definitions,
        )
        if pending_name is not None:
            return "rebound", pending_name
    return "reject", None


_SEMANTIC_CLAUSE_RE = re.compile(r"[^.!?;,\n—–]+")


def _recover_explicit_non_known_constraints(
    constraints: list[dict[str, Any]],
    products: list[dict[str, Any]],
    current_message: str,
    authoritative_hints: tuple[dict[str, str], ...],
    authoritative_state: dict[str, Any] | None,
    ambiguities: list[dict[str, Any]],
    changes: list[str],
) -> list[dict[str, Any]]:
    """Recover one unambiguous epistemic fact from declarative evidence.

    This is a fail-closed completeness guard for a semantic-model omission. It
    uses the existing language-level status markers plus product-scoped fact
    aliases; it never supplies a value or guesses across multiple products,
    clauses, facts, or statuses.
    """

    if len(products) > 1 or (not products and len(authoritative_hints) != 1):
        return constraints
    scope_constraint = {"applies_to_product": 0 if products else None}
    definitions = _non_known_fact_definitions(
        scope_constraint,
        products,
        authoritative_hints,
    )
    if definitions is None:
        return constraints

    recovered = list(constraints)
    existing_names = {
        _normalize_schema_identifier(item.get("name")) for item in recovered
    }
    for group_match in _SEMANTIC_GROUP_RE.finditer(current_message):
        evidence = group_match.group(0).strip()
        coordinated = _coordinated_non_known_group(definitions, evidence)
        if coordinated is None:
            continue
        status, fact_names = coordinated
        for fact_name in fact_names:
            if fact_name in existing_names:
                continue
            recovered.append(
                ConstraintFact(
                    name=fact_name,
                    value=None,
                    unit=None,
                    status=ConstraintStatus(status),
                    polarity=ConstraintPolarity.REQUIRED,
                    applies_to_product=0 if products else None,
                    evidence=evidence,
                ).model_dump(mode="json")
            )
            existing_names.add(fact_name)
            changes.append("constraint_coordinated_non_known_fact_recovered")
        if all(fact_name in existing_names for fact_name in fact_names):
            before = len(ambiguities)
            ambiguities[:] = [
                item
                for item in ambiguities
                if not (
                    item.get("kind") == "constraint_non_known_fact_unresolved"
                    and _normalize_evidence(str(item.get("evidence") or ""))
                    == _normalize_evidence(evidence)
                )
            ]
            if len(ambiguities) != before:
                changes.append("constraint_non_known_group_ambiguity_resolved")

    for clause_match in _SEMANTIC_CLAUSE_RE.finditer(current_message):
        evidence = clause_match.group(0).strip()
        if not evidence:
            continue
        statuses = [
            status
            for status in (
                ConstraintStatus.UNKNOWN.value,
                ConstraintStatus.REFUSED.value,
                ConstraintStatus.DEFERRED.value,
            )
            if _has_explicit_non_known_status(status, evidence)
        ]
        if len(statuses) != 1:
            continue

        names = _non_known_alias_matches(definitions, evidence)
        if names and names.issubset(existing_names):
            continue
        if len(names) == 1:
            fact_name = next(iter(names))
        elif not names:
            pending_name = _pending_non_known_fact_name(
                authoritative_state,
                definitions,
            )
            fact_name = (
                _normalize_schema_identifier(pending_name)
                if pending_name is not None
                else ""
            )
        else:
            fact_name = ""
        if not fact_name:
            ambiguity = TurnAmbiguity(
                kind="constraint_non_known_fact_unresolved",
                description=(
                    "The unavailable fact could not be bound uniquely to the "
                    "typed product vocabulary."
                ),
                evidence=evidence,
            ).model_dump(mode="json")
            if ambiguity not in ambiguities and len(ambiguities) < 12:
                ambiguities.append(ambiguity)
                changes.append("constraint_non_known_fact_ambiguity_added")
            continue
        if fact_name in existing_names:
            continue
        recovered.append(
            ConstraintFact(
                name=fact_name,
                value=None,
                unit=None,
                status=ConstraintStatus(statuses[0]),
                polarity=ConstraintPolarity.REQUIRED,
                applies_to_product=0 if products else None,
                evidence=evidence,
            ).model_dump(mode="json")
        )
        existing_names.add(fact_name)
        changes.append("constraint_explicit_non_known_fact_recovered")
    return recovered


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

    This performs only syntax-bound normalization, including a bounded Russian
    cardinal grammar.  It never assigns a dimension without an explicit unit
    or typed product context.  Every evidence fragment is an exact slice of
    the current message.
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
    for mention in extract_spoken_cardinal_mentions(current_message):
        unit_match = _SPOKEN_POWER_UNIT_RE.match(current_message[mention.end :])
        if unit_match is None:
            continue
        end = mention.end + unit_match.end()
        anchors.append(
            {
                "family": "boiler",
                "name": "power_kw",
                "value": int(mention.value) if mention.value.is_integer() else mention.value,
                "unit": "kW",
                "evidence": current_message[mention.start : end].strip(),
                "start": mention.start,
                "end": end,
                "polarity": _anchor_polarity(current_message, mention.start, end),
                "recovery_code": "spoken_boiler_power_anchor_recovered",
            }
        )
    for match in _POWER_ANCHOR_RE.finditer(current_message):
        if any(
            start <= match.start() and match.end() <= end
            for start, end in power_range_spans
        ):
            continue
        add(match, family="boiler", name="power_kw")
    for match in _AREA_ANCHOR_RE.finditer(current_message):
        add(match, family="boiler", name="area_m2", unit_override="m2")
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
    for match in _MOUNTING_LENGTH_ANCHOR_RE.finditer(current_message):
        add(match, family="pump", name="mounting_length_mm", unit_override="mm")
    for match in _PUMP_CONNECTION_DIAMETER_ANCHOR_RE.finditer(current_message):
        add(match, family="pump", name="diameter_mm", unit_override="mm")
    for match in _RADIATOR_CENTER_DISTANCE_ANCHOR_RE.finditer(current_message):
        add(match, family="radiator", name="center_distance_mm", unit_override="mm")
    for match in _PIPE_PRESSURE_ANCHOR_RE.finditer(current_message):
        # A bare pressure is intentionally attached only in a confirmed pipe
        # context by ``_anchor_binding``.  The same number may instead mean
        # an inlet pressure or a valve rating in another product family.
        add(match, family="pipe", name="operating_pressure_bar", unit_override="bar")
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
    if anchor_name == "area_m2":
        return normalized in {
            "area",
            "area_m2",
            "heated_area_m2",
            "building_area_m2",
            "heating_area_m2",
        } or "площад" in normalized
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
        changes.append(
            str(anchor.get("recovery_code") or "constraint_typed_numeric_anchor_recovered")
        )
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
        *(item.evidence for item in understanding.selection_controls),
        *(
            (understanding.selection_strategy.evidence,)
            if understanding.selection_strategy is not None
            and understanding.selection_strategy.evidence is not None
            else ()
        ),
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
            understanding.selection_controls,
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
        exempt_spans.extend(
            match.span()
            for match in _MIXED_IDENTIFIER_TOKEN_RE.finditer(evidence)
            if "." in match.group(0)
        )
        # A six-or-more digit literal inside an explicit product mention is a
        # possible numeric catalogue article, not an engineering value that
        # the semantic model must arbitrarily assign to a constraint.  This is
        # only a syntactic exemption: the shared SKU resolver still has to
        # confirm an exact item before any state, retrieval or answer uses it.
        exempt_spans.extend(
            match.span()
            for match in _NUMERIC_ARTICLE_TOKEN_RE.finditer(evidence)
        )
        # Compatibility is a bounded two-sided identity operation.  Some
        # catalogue articles have five digits (for example ``53843``), while
        # the general identity grammar correctly requires six to avoid turning
        # ordinary measurements into products.  When the typed action is
        # already compatibility, preserve such a span as a possible identity
        # rather than demanding the LLM invent an engineering constraint for
        # it.  The source-bound Compatibility builder still has to resolve it
        # exactly before any two-product rule runs.
        if CustomerAct.COMPATIBILITY in understanding.acts:
            exempt_spans.extend(
                match.span()
                for match in _COMPATIBILITY_NUMERIC_REFERENCE_RE.finditer(evidence)
            )
        if _LATIN_WORD_RE.search(evidence):
            exempt_spans.extend(
                match.span() for match in _STRUCTURED_MODEL_NUMBER_RE.finditer(evidence)
            )
        quantity_spans = (
            [match.span() for match in _CALCULATION_QUANTITY_IN_EVIDENCE_RE.finditer(evidence)]
            if CustomerAct.CALCULATE in understanding.acts
            else []
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
            inside_calculation_quantity = any(
                start >= quantity_start and end <= quantity_end
                for quantity_start, quantity_end in quantity_spans
            )
            if (
                token.isdigit()
                and not inside_model_designation
                and not inside_calculation_quantity
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

    normalized_acts: list[str] = []
    for item in acts:
        value = getattr(item, "value", item)
        # This is a pre-validation repair boundary.  A malformed LLM field
        # must reach strict validation as a rejected field, not make the
        # entire semantic turn crash while de-duplicating an unhashable dict.
        if not isinstance(value, str):
            changes.append("invalid_act_schema_dropped")
            continue
        if value in control_values:
            controls.append({"kind": value, "evidence": evidence})
            changes.append("act_control_moved_to_workflow_controls")
        else:
            normalized_acts.append(item)

    normalized_controls: list[dict[str, str]] = []
    for item in controls:
        if not isinstance(item, dict):
            changes.append("invalid_workflow_control_schema_dropped")
            continue
        kind = item.get("kind")
        control_evidence = item.get("evidence")
        if not isinstance(kind, str) or not isinstance(control_evidence, str):
            changes.append("invalid_workflow_control_schema_dropped")
            continue
        if kind in act_values:
            normalized_acts.append(kind)
            changes.append("workflow_control_act_moved_to_acts")
        else:
            normalized_controls.append({"kind": kind, "evidence": control_evidence})

    repaired["acts"] = list(dict.fromkeys(normalized_acts))
    unique_controls: list[dict[str, str]] = []
    seen_controls: set[tuple[str, str]] = set()
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


def _migrate_selection_strategy_schema(
    payload: dict[str, Any],
    current_message: str,
) -> None:
    """Migrate accepted legacy frames without repairing incomplete v1.3 output."""

    version = str(payload.get("schema_version") or "1.0")
    if version not in {"1.0", "1.1", "1.2"}:
        return

    controls = payload.get("selection_controls")
    controls = controls if isinstance(controls, list) else []
    if not controls:
        decision = {
            "kind": SelectionStrategyKind.STANDARD.value,
            "evidence": None,
        }
        payload["selection_controls"] = []
    elif (
        len(controls) == 1
        and isinstance(controls[0], dict)
        and controls[0].get("kind")
        == SelectionControlKind.CONTINUE_WITH_CONFIRMED_FACTS.value
        and isinstance(controls[0].get("evidence"), str)
        and controls[0]["evidence"].strip()
    ):
        decision = {
            "kind": SelectionStrategyKind.CONTINUE_WITH_CONFIRMED_FACTS.value,
            "evidence": controls[0]["evidence"],
        }
    else:
        # An old malformed collection cannot be promoted to a real control.
        # Preserve the uncertainty as typed data grounded in this turn.
        evidence = str(current_message or "")[:240]
        decision = {
            "kind": SelectionStrategyKind.AMBIGUOUS.value,
            "evidence": evidence,
        }
        payload["selection_controls"] = []
        ambiguities = payload.get("ambiguities")
        if not isinstance(ambiguities, list):
            ambiguities = []
            payload["ambiguities"] = ambiguities
        if len(ambiguities) < 12:
            ambiguities.append(
                {
                    "kind": "legacy_selection_strategy_ambiguous",
                    "description": (
                        "Legacy selection controls could not be migrated "
                        "unambiguously."
                    ),
                    "evidence": evidence,
                }
            )
    payload["selection_strategy"] = decision
    payload["schema_version"] = "1.3"


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


def _discard_spurious_information_requests_for_pending_terminal_answer(
    repaired_turn: dict[str, Any],
    current_message: str,
    changes: list[str],
) -> None:
    """Keep a customer's ``I don't know`` answer out of the fact-answer path.

    A terminal answer to a question we just asked is a state update, not a
    request to explain the missing fact.  Otherwise an LLM can register a
    fabricated ``explain`` request for the same fact; seller policy correctly
    gives that request priority and the safe preliminary-selection path never
    receives its SelectionResult/outcome gate.

    An explicit question still wins.  This rule therefore only removes an
    information request when the turn both records an answer to a pending
    question and contains a typed unknown/refused/deferred fact without any
    direct-question anchor.
    """

    if not repaired_turn.get("answers_pending_question"):
        return
    if _DIRECT_QUESTION_RE.search(current_message):
        return
    constraints = repaired_turn.get("constraints")
    terminal_statuses = {
        ConstraintStatus.UNKNOWN.value,
        ConstraintStatus.REFUSED.value,
        ConstraintStatus.DEFERRED.value,
    }
    has_terminal_fact = bool(
        isinstance(constraints, list)
        and any(
            isinstance(item, dict)
            and str(getattr(item.get("status"), "value", item.get("status")))
            in terminal_statuses
            for item in constraints
        )
    )
    if not has_terminal_fact or not repaired_turn.get("information_requests"):
        return
    repaired_turn["information_requests"] = []
    repaired_turn["acts"] = [
        str(getattr(item, "value", item))
        for item in (repaired_turn.get("acts") or [])
        if str(getattr(item, "value", item)) != CustomerAct.EXPLAIN.value
    ]
    changes.append("pending_terminal_answer_spurious_information_request_removed")


def repair_grounded_semantic_payload(
    raw: Any,
    current_message: str,
    authoritative_product_hints: tuple[dict[str, str], ...] = (),
    shown_product_cards: tuple[Any, ...] = (),
    authoritative_dialogue_state: dict[str, Any] | None = None,
    catalog_sku_anchors: tuple[CatalogSkuAnchor[Any], ...] = (),
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
    _migrate_selection_strategy_schema(repaired, current_message)
    operation = getattr(
        repaired.get("operation"),
        "value",
        repaired.get("operation"),
    )
    show_match = _EXPLICIT_SHOW_SELECTION_RE.search(current_message)
    if show_match is not None:
        show_remainder = (
            current_message[: show_match.start()]
            + current_message[show_match.end() :]
        )
        if (
            not show_remainder.strip(" \t\r\n.,!?;:-—–")
            and _active_authoritative_goal(authoritative_dialogue_state) is not None
            and operation in {GoalOperation.NEW.value, GoalOperation.UNKNOWN.value}
        ):
            repaired["operation"] = GoalOperation.CONTINUE.value
            operation = GoalOperation.CONTINUE.value
            changes.append("generic_show_rebound_to_active_goal")
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
    if (
        _bounded_fact_followup_targets_active_goal(
            current_message,
            authoritative_dialogue_state,
        )
        and operation in {GoalOperation.NEW.value, GoalOperation.UNKNOWN.value}
    ):
        repaired["operation"] = GoalOperation.CONTINUE.value
        operation = GoalOperation.CONTINUE.value
        changes.append("bounded_fact_followup_rebound_to_active_goal")
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
    authoritative_active_goal = _active_authoritative_goal(
        authoritative_dialogue_state
    )
    if isinstance(raw_products, list):
        for old_index, item in enumerate(raw_products):
            if not isinstance(item, dict):
                changes.append("product_invalid_schema_dropped")
                continue
            canonical_type = item.get("canonical_type")
            if not isinstance(canonical_type, str) or not canonical_type.strip():
                inherited_untyped_followup = bool(
                    authoritative_active_goal is not None
                    and stale_product_may_be_inherited
                )
                repair_code = (
                    "inherited_untyped_product_proposal_dropped"
                    if inherited_untyped_followup
                    else (
                        "target_product_missing_canonical_type_dropped"
                        if item.get("role") == ProductRole.TARGET.value
                        else "product_missing_canonical_type_dropped"
                    )
                )
                if inherited_untyped_followup:
                    stale_typed_product_indexes.add(old_index)
                changes.append(repair_code)
                continue
            canonical_identity = canonical_product_type(canonical_type)
            if canonical_identity is not None:
                canonical_name, canonical_category = canonical_identity
                if canonical_name != canonical_type:
                    item["canonical_type"] = canonical_name
                    changes.append("product_type_canonicalized_from_registry")
                if item.get("category") != canonical_category:
                    item["category"] = canonical_category
                    changes.append("product_category_canonicalized_from_registry")
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

    # The model sometimes labels a natural comparison as a broad explanation.
    # That is not an interchangeable downstream action: Compare is only safe
    # when it is bound to cards that were actually delivered in this session.
    # With that scope and an unambiguous current-turn phrase we can repair the
    # action before the reducer creates its task, while leaving product choice,
    # values and any recommendation to the existing comparison evidence gate.
    if (
        len(shown_product_cards) >= 2
        and _VISIBLE_SCOPE_COMPARE_RE.search(current_message) is not None
        and CustomerAct.COMPARE.value not in normalized_acts
    ):
        normalized_acts.append(CustomerAct.COMPARE.value)
        changes.append("visible_scope_compare_action_recovered")

    # A compatibility request is safe to recover either from an already
    # delivered multi-card scope or from two explicit identity-shaped spans in
    # this utterance.  The latter does not resolve a SKU here: the existing
    # Compatibility request builder still validates both sides against the
    # frozen source snapshot, and its evidence gate remains solely responsible
    # for the verdict.
    if (
        (
            len(shown_product_cards) >= 2
            or _explicit_compatibility_reference_count(current_message) >= 2
        )
        and _VISIBLE_SCOPE_COMPATIBILITY_RE.search(current_message) is not None
        and CustomerAct.COMPATIBILITY.value not in normalized_acts
    ):
        normalized_acts.append(CustomerAct.COMPATIBILITY.value)
        changes.append(
            (
                "visible_scope_compatibility_action_recovered"
                if len(shown_product_cards) >= 2
                else "explicit_pair_compatibility_action_recovered"
            )
        )

    # Preserve an explicit total-price request even if the model reduced it to
    # a generic product question.  This only creates a typed action; product
    # scope, quantity, price basis and arithmetic remain the responsibility of
    # the grounded calculation request/result gates.
    if (
        _EXPLICIT_CALCULATION_RE.search(current_message) is not None
        and CustomerAct.CALCULATE.value not in normalized_acts
    ):
        normalized_acts.append(CustomerAct.CALCULATE.value)
        changes.append("explicit_calculation_action_recovered")

    # ``Какой котёл смотреть?`` is a selection request, not a question about a
    # property of an individual product.  Do not infer a catalogue family from
    # raw wording here: this repair is allowed only after the semantic model
    # has already supplied a typed target.  It prevents a spurious Explain
    # task from routing an otherwise valid first selection to the product-fact
    # boundary, where a SKU would correctly be required but be unhelpful.
    has_typed_target = any(
        isinstance(item, dict)
        and str(item.get("role") or "") == ProductRole.TARGET.value
        and _product_family(item) is not None
        for item in normalized_products
    )
    if (
        has_typed_target
        and _GENERIC_PRODUCT_SELECTION_QUESTION_RE.search(current_message)
        is not None
    ):
        if CustomerAct.SELECT.value not in normalized_acts:
            normalized_acts.append(CustomerAct.SELECT.value)
            changes.append("generic_typed_product_selection_action_recovered")
        # A frame that put this generic selection in an information-request
        # slot contains no predicate to answer.  Dropping that incomplete
        # Explain act keeps the reducer on the typed Selection path.  The
        # wording itself is a stronger anchor than a generic ontology alias
        # such as "котёл", which must not turn this into a product fact.
        if CustomerAct.EXPLAIN.value in normalized_acts:
            normalized_acts = [
                item
                for item in normalized_acts
                if item != CustomerAct.EXPLAIN.value
            ]
            changes.append("generic_typed_product_selection_explain_dropped")
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
    _discard_spurious_information_requests_for_pending_terminal_answer(
        repaired,
        current_message,
        changes,
    )

    collection_labels = {
        "references": "reference",
        "ambiguities": "ambiguity",
        "workflow_controls": "workflow_control",
        "selection_controls": "selection_control",
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

    strategy = repaired.get("selection_strategy")
    if isinstance(strategy, dict):
        evidence = strategy.get("evidence")
        if isinstance(evidence, str) and evidence.strip():
            grounded = _grounded_evidence_fragment(evidence, current_message)
            if grounded is not None and grounded != evidence:
                strategy["evidence"] = grounded
                changes.append(
                    "selection_strategy_evidence_rebound_to_current_message"
                )

    raw_constraints = repaired.get("constraints")
    normalized_constraints: list[dict[str, Any]] = []
    pending_softenings: list[tuple[str, int | None, str]] = []
    unit_ambiguities: list[dict[str, Any]] = []
    categorical_ambiguities: list[dict[str, Any]] = []
    non_known_ambiguities: list[dict[str, Any]] = []
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
                pending_contextual_unit_repaired = (
                    _repair_known_numeric_pending_answer(
                        item,
                        authoritative_dialogue_state,
                        repaired,
                        changes,
                    )
                )
                unit_incompatibility = (
                    None
                    if pending_contextual_unit_repaired
                    else _numeric_constraint_unit_incompatibility(item)
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

            product_index = item.get("applies_to_product")
            invalid_product_binding_removed = bool(
                product_index is not None
                and product_index not in stale_typed_product_indexes
                and (
                    isinstance(product_index, bool)
                    or not isinstance(product_index, int)
                    or product_index < 0
                    or product_index >= len(normalized_products)
                )
            )
            if invalid_product_binding_removed:
                # Preserve the independently grounded fact while removing the
                # model's invalid mention index.  Downstream semantic/state
                # gates still see an unbound fact and therefore cannot treat
                # the bad index as authority for a product choice.
                item["applies_to_product"] = None
                changes.append("constraint_invalid_product_binding_removed")

            if normalized_status != ConstraintStatus.KNOWN.value:
                definitions = _non_known_fact_definitions(
                    item,
                    normalized_products,
                    authoritative_product_hints,
                )
                evidence_matches = (
                    _non_known_alias_matches(definitions, str(item["evidence"]))
                    if definitions
                    else set()
                )
                if definitions and not evidence_matches:
                    proposed_name = _normalize_schema_identifier(
                        _canonical_constraint_fact_name(
                            str(item.get("name") or "")
                        )
                    )
                    full_message_spans = _non_known_alias_spans(
                        definitions,
                        current_message,
                    )
                    if set(full_message_spans) == {proposed_name}:
                        start, end = full_message_spans[proposed_name]
                        predicate_evidence = current_message[start:end]
                        combined_evidence = _combined_grounded_evidence_fragment(
                            str(item["evidence"]),
                            predicate_evidence,
                            current_message,
                        )
                        if combined_evidence is not None:
                            item["evidence"] = combined_evidence
                            changes.append(
                                "constraint_non_known_evidence_expanded"
                            )

            non_known_grounding, grounded_non_known_name = (
                ("compatible", None)
                if invalid_product_binding_removed
                else _grounded_non_known_fact_name(
                    item,
                    normalized_products,
                    authoritative_product_hints,
                    authoritative_dialogue_state,
                )
            )
            if non_known_grounding == "reject":
                non_known_ambiguities.append(
                    TurnAmbiguity(
                        kind="constraint_non_known_fact_unresolved",
                        description=(
                            "The unavailable fact could not be bound uniquely "
                            "to the typed product vocabulary."
                        ),
                        evidence=str(item["evidence"]),
                    ).model_dump(mode="json")
                )
                changes.append("constraint_non_known_fact_unresolved_dropped")
                continue
            if (
                non_known_grounding == "rebound"
                and grounded_non_known_name is not None
                and _normalize_schema_identifier(item.get("name"))
                != _normalize_schema_identifier(grounded_non_known_name)
            ):
                item["name"] = grounded_non_known_name
                changes.append("constraint_non_known_fact_name_rebound")

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

    if non_known_ambiguities:
        ambiguities = repaired.get("ambiguities")
        if isinstance(ambiguities, list):
            for ambiguity in non_known_ambiguities:
                if ambiguity in ambiguities or len(ambiguities) >= 12:
                    continue
                ambiguities.append(ambiguity)
                changes.append("constraint_non_known_fact_ambiguity_added")

    ambiguities = repaired.get("ambiguities")
    if not isinstance(ambiguities, list):
        ambiguities = []
        repaired["ambiguities"] = ambiguities
    normalized_constraints = _recover_explicit_non_known_constraints(
        normalized_constraints,
        normalized_products,
        current_message,
        authoritative_product_hints,
        authoritative_dialogue_state,
        ambiguities,
        changes,
    )
    _recover_bounded_selection_category_and_facts(
        repaired,
        current_message,
        normalized_products,
        normalized_constraints,
        authoritative_dialogue_state,
        changes,
        catalog_sku_anchors,
    )
    _recover_selection_preferences(
        repaired,
        current_message,
        normalized_constraints,
        normalized_products,
        changes,
    )
    # The bounded recovery may add or correct the one current target product;
    # keep the strict validator and all later stages on the same typed frame.
    repaired["products"] = normalized_products
    normalized_constraints = _dedupe_equivalent_numeric_constraints(
        normalized_constraints,
        changes,
    )
    active_numeric_goal = _active_authoritative_goal(
        authoritative_dialogue_state
    )
    numeric_product_hints = (
        (
            {
                "canonical_type": str(active_numeric_goal.get("canonical_type") or ""),
                "category": str(active_numeric_goal.get("category") or ""),
            },
        )
        if active_numeric_goal is not None
        else authoritative_product_hints
    )
    repaired["constraints"] = _recover_typed_numeric_constraints(
        normalized_constraints,
        normalized_products,
        current_message,
        numeric_product_hints,
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
    _normalize_terminal_pending_selection_strategy(
        repaired,
        authoritative_dialogue_state,
        changes,
    )
    _recover_explicit_show_selection_control(
        repaired,
        current_message,
        changes,
    )
    _reconcile_selection_strategy_contract(
        repaired,
        current_message,
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

    pending_decision_question = None
    answer_summary = typed_state.answer_plan_summary
    if (
        answer_summary is not None
        and _enum_value(answer_summary.delivery_status) == "committed_to_session"
        and answer_summary.question_fact is not None
    ):
        pending_decision_question = {
            "question_id": (
                _bounded_context_text(answer_summary.question_id, 120)
                if answer_summary.question_id is not None
                else None
            ),
            "fact_name": _bounded_context_text(answer_summary.question_fact, 120),
            "task_id": (
                _bounded_context_text(answer_summary.question_task_id, 120)
                if answer_summary.question_task_id is not None
                else None
            ),
            "goal_id": (
                _bounded_context_text(answer_summary.question_goal_id, 120)
                if answer_summary.question_goal_id is not None
                else None
            ),
        }

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
        "pending_decision_question": pending_decision_question,
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
        catalog_products: tuple[Any, ...] | list[Any] = (),
    ) -> None:
        self.llm_client = llm_client
        self.model = model
        self._catalog_products: tuple[Any, ...] = tuple(catalog_products)

    def set_catalog_products(self, products: tuple[Any, ...] | list[Any]) -> None:
        """Set the read-only catalogue view used to prove SKU anchors."""

        self._catalog_products = tuple(products)

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
        catalog_sku_anchors = resolve_catalog_sku_anchors(
            safe_message,
            self._catalog_products,
        )
        context_before_turn = semantic_context(state_before)
        authoritative_dialogue_state = context_before_turn.get(
            "authoritative_dialogue_state_v2"
        )
        payload = {
            "current_message": safe_message,
            "context_before_turn": context_before_turn,
            "deterministic_sku_anchors": [
                {
                    "text": item.text,
                    "canonical_sku": item.canonical_sku,
                    "match_kind": item.match_kind,
                    "reason_code": item.resolution.reason_code,
                }
                for item in catalog_sku_anchors
            ],
            "ontology": semantic_ontology_payload(),
            "output_schema": TurnUnderstanding.model_json_schema(),
        }
        fallback: dict[str, Any] = {
            "schema_version": "1.3",
            "language": "ru",
            "operation": "unknown",
            "acts": [],
            "products": [],
            "constraints": [],
            "references": [],
            "ambiguities": [],
            "workflow_controls": [],
            "selection_controls": [],
            "selection_strategy": {
                "kind": "standard",
                "evidence": None,
            },
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
                        catalog_sku_anchors,
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
                        catalog_sku_anchors,
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
            # Import locally to keep the compatibility bridge dependent on the
            # legacy TurnUnderstanding schema without creating an import cycle
            # while this module is initialised.
            from app.semantic_v2.bridge import build_semantic_turn_delta

            semantic_turn_id = "semantic:" + hashlib.sha256(
                (
                    f"{state_before.session_id}:"
                    f"{getattr(state_before, 'session_revision', 0)}:"
                    f"{safe_message}"
                ).encode("utf-8")
            ).hexdigest()[:24]
            semantic_delta, semantic_gate = build_semantic_turn_delta(
                understanding,
                message=safe_message,
                turn_id=semantic_turn_id,
                session_id=state_before.session_id,
                semantic_repairs=structural_repairs,
            )
            if not semantic_gate.accepted:
                raise ValueError(
                    "semantic_gate:" + ",".join(semantic_gate.reason_codes)
                )
            typed_state = (
                state_before.live_dialogue_state_v2
                or state_before.dialogue_state_v2
            )
            goal_reactivation = resolve_goal_reactivation(
                safe_message,
                typed_state,
            )
            return SemanticInterpretationResult(
                status="accepted",
                requested=True,
                transport_succeeded=True,
                output_accepted=True,
                model=model,
                latency_ms=int((monotonic() - started) * 1000),
                understanding=understanding,
                semantic_delta=semantic_delta,
                semantic_gate=semantic_gate,
                goal_reactivation=goal_reactivation,
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
