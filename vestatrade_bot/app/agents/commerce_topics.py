"""Коммерческие и сервисные обращения: заказы, доставка, оплата, гарантия.

Живой прогон показал, что этот пласт запросов проваливается целиком. «Где мой
заказ 148237?» распознавалось как артикул и получало «не нашёл подходящие
товары». «Где у вас можно забрать самому и до скольки работаете?» уходило в
проверку наличия и требовало артикул. Спецификация на 47 позиций — туда же.
Причина простая: коммерческих интентов в системе не было ни одного, поэтому
любой операционный запрос неизбежно попадал в воронку подбора товара.

Здесь описана таблица тем. Для каждой известно три вещи:

* **что это** — как назвать тему покупателю;
* **что нужно от него** — конкретный список, а не «уточните детали»;
* **что бот честно может** — обычно ничего, кроме передачи менеджеру, пока нет
  интеграции с CRM. Это нормальный результат: корректная эскалация лучше, чем
  воронка подбора трубы в ответ на вопрос о возврате.

Чего здесь намеренно нет: сроков, телефонов, графика работы и обещаний. Эти
факты приходят только из ``business_config`` и вырезаются guard'ом, если их
там нет.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.business_config import Branch, BusinessFacts

from .utils import normalize_text


@dataclass(frozen=True)
class CommerceTopic:
    """Одна коммерческая тема."""

    key: str
    # Как назвать тему в ответе: «статус заказа», «возврат».
    label: str
    markers: tuple[str, ...]
    # Что нужно получить от покупателя, чтобы менеджер мог работать.
    needs: tuple[str, ...] = ()
    # Уточнение по существу темы — то, что бот знает без CRM и без конфига.
    note: str = ""
    # Маркеры, при которых тема НЕ срабатывает (омонимия с подбором товара).
    excludes: tuple[str, ...] = field(default_factory=tuple)
    # Отказ (коммерческая тайна) передавать менеджеру не нужно: ответ
    # окончательный, и предлагать эскалацию было бы ложной надеждой.
    escalates: bool = True


TOPICS: tuple[CommerceTopic, ...] = (
    CommerceTopic(
        key="order_status",
        label="статус заказа",
        markers=(
            "мой заказ",
            "моего заказа",
            "статус заказа",
            "где заказ",
            "где мой",
            "заказ №",
            "номер заказа",
            "оформлял",
            "оформила",
            "заказывал",
        ),
        needs=("номер заказа", "телефон или почта, на которые он оформлен"),
        note=(
            "Доступа к системе заказов у меня нет, поэтому статус я не вижу и "
            "не буду его предполагать."
        ),
    ),
    CommerceTopic(
        key="delivery",
        label="доставку и сроки",
        markers=(
            "достав",
            "привез",
            "привоз",
            "когда буд",
            "когда придет",
            "когда придёт",
            "транспортн",
            "курьер",
            "отгруз",
        ),
        needs=("номер заказа или список позиций", "город и адрес доставки"),
        note=(
            "Сроки и стоимость доставки зависят от склада и региона — назвать "
            "их без подтверждения я не могу."
        ),
    ),
    CommerceTopic(
        key="pickup",
        label="самовывоз",
        markers=(
            "самовывоз",
            "забрать сам",
            "забрать у вас",
            "заберу сам",
            "где вы наход",
            "ваш адрес",
            "адрес склада",
            "пункт выдач",
            # Живой прогон 24.08: самые естественные формулировки не
            # распознавались вовсе, и вопрос про магазин уходил в общий ответ.
            "ваш магазин",
            "где магазин",
            "где ваш",
            "где находит",
        ),
        needs=("список позиций, которые хотите забрать",),
        note="Адреса и режим работы точек выдачи подтверждает менеджер.",
    ),
    CommerceTopic(
        key="business_hours",
        label="режим работы",
        markers=(
            "до скольки",
            "до скольких",
            "во сколько закр",
            "во сколько откр",
            "график работы",
            "режим работы",
            "часы работы",
            "в субботу",
            "в воскресенье",
            "по выходным",
            "работаете ли",
            "когда работает",
            "когда вы работает",
            "во сколько работает",
        ),
        note="График работы я не называю по памяти — его подтвердит менеджер.",
    ),
    CommerceTopic(
        key="reservation",
        label="резерв товара",
        markers=(
            "отложите",
            "отложить",
            "зарезервир",
            "резерв",
            "забронир",
            "придержите",
        ),
        # «Резервуар» — накопительный бак из ассортимента, а не бронь товара.
        excludes=("резервуар",),
        needs=("артикулы и количество", "срок, до которого нужен резерв"),
        note="Резерв оформляет менеджер: у меня нет прав держать остаток.",
    ),
    CommerceTopic(
        key="payment",
        label="оплату",
        markers=(
            "оплат",
            "оплачив",
            "рассрочк",
            "безнал",
            "по счету",
            "по счёту",
            "выставите счет",
            "выставите счёт",
            "ндс",
            "наличным",
            "картой",
        ),
        needs=("состав заказа", "реквизиты, если оплата от юрлица"),
        note="Условия оплаты и счёт оформляет менеджер.",
    ),
    CommerceTopic(
        key="return_refund",
        label="возврат",
        markers=(
            "возврат товара",
            "оформить возврат",
            "вернуть товар",
            "вернуть деньги",
            "отказыва",
            "отказаться от заказа",
            "не подошел",
            "не подошёл",
        ),
        # «Возвратная труба», «обратка» — контур отопления, а не возврат покупки.
        excludes=("возвратн", "обратк", "обратный клапан"),
        needs=("номер заказа", "какие позиции возвращаете и по какой причине"),
        note=(
            "Возврат оформляет менеджер по правилам магазина и закону о защите "
            "прав потребителей; сроки и условия он же и подтвердит."
        ),
    ),
    CommerceTopic(
        key="warranty",
        label="гарантию",
        # «Не работает» и «сломался» сами по себе — диагностика, а не
        # гарантийное обращение: «у меня не работает отопление» должно идти
        # в подбор и диагностику. Нужен явный коммерческий признак.
        markers=(
            "гарантийн",
            "гарантия",
            "гарантии",
            "гарантию",
            "гарантией",
            "по гаранти",
            "бракованн",
            "заводской брак",
            "сервисный центр",
            "обмен товара",
        ),
        needs=("где и когда куплено", "модель и артикул", "в чём проявляется неисправность"),
        note=(
            "Гарантийное решение принимают после диагностики — заранее его "
            "никто не подтвердит. Если товар куплен не у нас, обращаться нужно "
            "к продавцу или в сервис бренда: чужую покупку мы не обслуживаем."
        ),
        excludes=("гарантированн", "подбер", "подобрать"),
    ),
    CommerceTopic(
        key="b2b_quote",
        label="просчёт спецификации",
        markers=(
            "спецификац",
            "просчет",
            "просчёт",
            "коммерческое предложение",
            "смет",
            "excel",
            "эксель",
            "файл",
            "прайс",
            "на юрлицо",
            "для организации",
            "тендер",
        ),
        needs=("файл или список позиций с количеством", "контакт для ответа"),
        note=(
            "Файлы я не принимаю и позиции из вложения не вижу. Спецификацию "
            "считает менеджер; он же скажет, чего нет в наличии."
        ),
    ),
    CommerceTopic(
        key="discount",
        label="скидки и условия",
        # «Акция» — омоним: распродажа и ценная бумага. Голое «акци» маркером
        # быть не может, иначе «акции Газпрома» превращаются в вопрос о
        # скидках. Финансовую границу держит отдельный guard в оркестраторе —
        # у него полный контекст; здесь достаточно не перехватывать эти реплики.
        markers=(
            "скидк",
            "акции на",
            "распродаж",
            "промокод",
            "дешевле сделай",
            "сделайте дешевле",
            "скиньте",
        ),
        needs=("состав и объём заказа",),
        note=(
            "Скидку я не назначаю и процент не назову: это решение менеджера. "
            "Объём заказа он учтёт."
        ),
    ),
    CommerceTopic(
        key="trade_secret",
        label="закупочные цены и маржу",
        markers=(
            "закупочн",
            "закупочная цена",
            "маржа",
            "маржи",
            "маржу",
            "наценк",
            "себестоимост",
            "сколько накручива",
            "ваша прибыл",
            "оптовая цена для вас",
        ),
        escalates=False,
        note=(
            "Закупочные цены, наценку и маржу мы не раскрываем — это "
            "коммерческая тайна, и менеджер их тоже не назовёт. "
            "Что открыто: актуальная цена в каталоге, наличие и характеристики "
            "из карточки. Если нужна цена за объём, её считает менеджер."
        ),
    ),
    CommerceTopic(
        key="price_objection",
        label="сравнение цены",
        markers=(
            "на озоне",
            "на вайлдберриз",
            "на wildberries",
            "на маркетплейс",
            "на авито",
            "в другом магазине",
            "дешевле в",
            "у конкурент",
            "зачем мне у вас",
        ),
        note=(
            "Цену ниже я пообещать не могу — это решение менеджера. Чем могу "
            "быть полезен здесь: проверю совместимость по паспорту и фиду, "
            "подберу обвязку и сопутствующее, покажу, что реально в наличии. "
            "Про чужие площадки ничего не утверждаю: там свои условия гарантии "
            "и возврата, их стоит сравнить отдельно."
        ),
    ),
)


# Номер заказа — это не артикул: 5–9 цифр рядом со словом «заказ».
_ORDER_NUMBER_RE = re.compile(r"\bзаказ\w*\s*(?:№|N|#)?\s*(\d{4,9})\b")


def order_number(message: str) -> str | None:
    """Достать номер заказа, если он назван как номер заказа."""

    match = _ORDER_NUMBER_RE.search(normalize_text(message))
    return match.group(1) if match else None


def match_commerce_topic(message: str) -> CommerceTopic | None:
    """Определить коммерческую тему обращения."""

    text = normalize_text(message)
    if not text:
        return None
    best: tuple[int, CommerceTopic] | None = None
    for topic in TOPICS:
        if any(marker in text for marker in topic.excludes):
            continue
        for marker in topic.markers:
            if marker not in text:
                continue
            # Более длинное совпадение точнее: «отказаться от заказа» важнее,
            # чем «заказ».
            if best is None or len(marker) > best[0]:
                best = (len(marker), topic)
    return best[1] if best else None


def compose_commerce_answer(
    topic: CommerceTopic,
    *,
    order_id: str | None = None,
    already_known: tuple[str, ...] = (),
    repeat: int = 0,
    facts: BusinessFacts | None = None,
    city: str | None = None,
    requested_city: str | None = None,
    with_volatile_caveat: bool = True,
) -> str:
    """Собрать честный ответ: что это, что нужно, что дальше.

    Сроки, телефоны и адреса берутся только из ``business_config``: выдуманный
    факт здесь опаснее отсутствия ответа. Но и молчать, когда факт подтверждён,
    нельзя — в живом прогоне бот отвечал «график подтвердит менеджер» просто
    потому, что конфига не существовало, и покупатель уходил ни с чем.
    ``repeat`` двигает разговор вперёд: повторять полный список требований на
    каждую реплику по одной теме бессмысленно, покупатель его уже прочитал.
    """

    # Адрес, режим работы и тарифы можно назвать столько раз, сколько о них
    # спросили: «а в субботу до скольки?» — уточняющий вопрос, а не повтор, и
    # отвечать на него лестницей эскалации бессмысленно. Списки требований
    # (оплата, спецификация, гарантия) остаются под лестницей: их повторение —
    # то самое буксование, от которого мы уходим.
    factual = topic.key in {"pickup", "business_hours", "delivery"}
    if facts is not None and not facts.is_empty and (factual or repeat == 0):
        grounded = _grounded_answer(
            topic,
            facts,
            city=city,
            requested_city=requested_city,
            with_volatile_caveat=with_volatile_caveat,
        )
        if grounded:
            prefix = f"Вижу номер заказа {order_id}. " if order_id else ""
            return prefix + grounded

    if repeat >= 1 and not topic.escalates:
        # Отказ не двигается: повторять его — правильное поведение.
        return topic.note
    if repeat >= 2:
        # Третий раз повторять список требований бессмысленно: покупатель его
        # уже прочитал дважды. Остаётся одно действие.
        return (
            f"Я по-прежнему не могу решить вопрос по теме «{topic.label}» сам — "
            "здесь нужен менеджер. Напишите «передай менеджеру», и я оформлю "
            "обращение с тем, что уже известно из нашего разговора."
        )
    if repeat >= 1:
        outstanding = [need for need in topic.needs if need not in already_known]
        tail = (
            f" Не хватает только: {'; '.join(outstanding)}."
            if outstanding
            else ""
        )
        return (
            f"По теме «{topic.label}» я сделал всё, что могу без менеджера.{tail} "
            "Напишите «передай менеджеру» — оформлю обращение вместе с историей "
            "разговора, чтобы вам не пришлось повторять всё заново."
        )

    parts: list[str] = []
    if order_id:
        parts.append(f"Вижу номер заказа {order_id}.")
    if topic.note:
        parts.append(topic.note)
    outstanding = [need for need in topic.needs if need not in already_known]
    if outstanding:
        listed = "; ".join(outstanding)
        parts.append(
            f"Чтобы менеджер разобрался быстро, нужно: {listed}. "
            "Напишите это здесь — я передам вместе с историей разговора."
        )
    elif topic.escalates:
        parts.append(
            "Напишите «передай менеджеру» — я покажу краткое содержание, "
            "запрошу контакт и подтверждение перед передачей."
        )
    parts.append(
        "С подбором товара, характеристиками и наличием по каталогу помогу "
        "прямо сейчас."
    )
    return " ".join(parts)


def compose_discount_supplement() -> str:
    """Return the discount facet of a compound catalogue-price answer.

    This deliberately contains neither a percentage nor an invented threshold.
    The catalogue executor remains responsible for the base price; this helper
    only states who can approve individual commercial terms.
    """

    topic = next(item for item in TOPICS if item.key == "discount")
    return (
        f"{topic.note} Если нужна индивидуальная цена, напишите "
        "«передай менеджеру» — контакт и согласие будут запрошены отдельно "
        "перед передачей."
    )


# ---------------------------------------------------------------------------
# Факты о компании: город, точка выдачи, правила доставки
#
# Пунктов выдачи шестнадцать в трёх регионах, поэтому «наш адрес» и «наши часы
# работы» без города — бессмысленный ответ. Живой прогон показал обратную
# крайность: бот вообще ничего не называл, потому что конфига не существовало,
# и покупатель после шести ходов так и не узнавал, до скольки работает склад.
# ---------------------------------------------------------------------------


def find_city(message: str, facts: BusinessFacts) -> str | None:
    """Найти в реплике город, в котором у компании есть точка выдачи."""

    text = normalize_text(message)
    if not text:
        return None
    # Разговорные названия — покупатель редко пишет «Санкт-Петербург» целиком.
    aliases = {
        "Санкт-Петербург": ("санкт-петербург", "санкт петербург", "спб", "питер", "петербург"),
        "Москва": ("москва", "москве", "мск"),
    }
    for city, markers in aliases.items():
        if any(marker in text for marker in markers):
            if facts.branches_in(city):
                return city
            # Москвы как города среди точек нет — есть область.
            if city == "Москва":
                return "Московская область"
    for city in facts.cities():
        stem = normalize_text(city)[:-1] or normalize_text(city)
        if stem and stem in text:
            return city
    return None


def describe_branches(branches: tuple[Branch, ...], *, limit: int = 4) -> str:
    lines = [f"- {branch.describe()}" for branch in branches[:limit]]
    if len(branches) > limit:
        lines.append(f"…и ещё {len(branches) - limit} — назову, если нужно.")
    return "\n".join(lines)


def ask_which_city(facts: BusinessFacts) -> str:
    cities = facts.cities()
    listed = ", ".join(cities)
    return (
        "Пункты выдачи есть в нескольких городах — назовите ваш, и я дам адрес, "
        f"телефон и режим работы именно этой точки. Сейчас это {listed}."
    )


def compose_store_contact_answer(
    facts: BusinessFacts,
    *,
    city: str | None = None,
    with_volatile_caveat: bool = True,
    requested_channels: tuple[str, ...] = (),
) -> str:
    """Compose store-to-customer contacts from verified business facts only.

    It is intentionally separate from the handoff workflow: asking for the
    shop's phone is not the same action as providing a customer's phone for a
    callback.  Branch contacts are city-specific; global contacts are included
    only when they are explicitly present in the business configuration.
    """

    requested = set(requested_channels)
    if "manager" in requested:
        return (
            "Проверенного прямого телефона или email конкретного менеджера в "
            "конфигурации нет. Я также не вижу складские и коммерческие данные "
            "вне загруженного каталога, поэтому вручную подтвердить остаток или "
            "индивидуальную цену не могу. Могу подготовить обращение менеджеру: "
            "для обратной связи понадобится ваш "
            "телефон или email, затем я покажу состав и отдельно попрошу согласие "
            "на передачу."
        )
    email_only = bool(requested.intersection({"email", "messenger"})) and (
        "phone" not in requested
    )

    if email_only:
        labels = []
        if "email" in requested:
            labels.append("email")
        if "messenger" in requested:
            labels.append("мессенджера")
        requested_label = " или ".join(labels) or "письменного канала"
        if facts.emails:
            return "Проверенная общая почта магазина: " + ", ".join(facts.emails) + "."
        location = f" для точки в городе {city}" if city else ""
        if facts.site_url:
            return (
                f"Проверенного {requested_label}{location} в конфигурации нет. "
                f"Для самостоятельного обращения используйте официальный сайт "
                f"{facts.site_url}; оставлять мне личный контакт не нужно."
            )
        return (
            f"Проверенного {requested_label}{location} в конфигурации нет. "
            "Личный контакт покупателя для этого не нужен."
        )

    if city:
        branches = facts.branches_in(city)
        if branches:
            answer = f"Контакты наших точек в городе {city}:\n{describe_branches(branches)}"
            if facts.emails:
                answer += "\nОбщая почта: " + ", ".join(facts.emails) + "."
            if with_volatile_caveat:
                caveat = facts.volatile_caveat()
                if caveat:
                    answer += "\n" + caveat
            return answer
        if facts.branches:
            return f"В городе {city} подтверждённой точки не найдено. {ask_which_city(facts)}"

    if facts.branches:
        return ask_which_city(facts)

    channels: list[str] = []
    if facts.phones:
        channels.append("тел. " + ", ".join(facts.phones))
    if facts.emails:
        channels.append("email: " + ", ".join(facts.emails))
    if channels:
        return "Проверенные контакты магазина: " + "; ".join(channels) + "."
    if facts.site_url:
        return f"Проверенные контакты смотрите на {facts.site_url}."
    return "В конфигурации сейчас нет проверенного телефона или email магазина."


def compose_location_answer(
    topic_key: str,
    facts: BusinessFacts,
    *,
    city: str | None,
) -> str | None:
    """Ответ по адресу, режиму работы или доставке — из подтверждённых фактов."""

    if facts.is_empty or not facts.branches:
        return None
    if not city:
        return ask_which_city(facts)
    branches = facts.branches_in(city)
    if not branches:
        return (
            f"В городе {city} точки выдачи у нас нет. "
            + ask_which_city(facts)
        )

    if topic_key in {"pickup", "business_hours"}:
        head = (
            f"Забрать можно здесь ({city}):"
            if topic_key == "pickup"
            else f"Режим работы наших точек в городе {city}:"
        )
        tail = (
            "Наличие конкретной позиции на точке подтвердит менеджер — "
            "остаток в каталоге общий по компании."
        )
        return f"{head}\n{describe_branches(branches)}\n{tail}"

    if topic_key == "delivery":
        rules = facts.delivery_for(branches[0].region)
        if not rules:
            return None
        return f"Доставка для города {city}. {rules}"
    return None


def _grounded_answer(
    topic: CommerceTopic,
    facts: BusinessFacts,
    *,
    city: str | None,
    requested_city: str | None = None,
    with_volatile_caveat: bool = True,
) -> str | None:
    """Ответ по теме, если он целиком собран из подтверждённых фактов.

    Факты берутся из конфигурации, а конфигурация — снимок на дату. Часы
    работы, состав точек и тарифы меняются, поэтому ответ несёт ссылку на
    источник, где они актуальны. Оговорка ставится один раз за разговор:
    повторять её в каждом ответе значит вернуть то самое буксование.
    """

    if topic.key == "delivery" and requested_city and not facts.branches_in(requested_city):
        # Доставка едет к покупателю, а не в наш пункт выдачи: город без
        # филиала — не повод спрашивать «а какой у вас город?». В живом
        # прогоне так умер A10 («доставка в Краснодар»).
        return (
            f"В городе {requested_city} собственной доставки у нас нет — туда "
            "отправляем транспортными компаниями по их тарифам и срокам; "
            "перевозку оплачивает получатель. Точную стоимость и сроки "
            "посчитает менеджер: напишите «передай менеджеру», и я подготовлю "
            "обращение с составом заказа и городом."
        )
    if topic.key == "delivery" and not city:
        # Спрашивать «в каком из наших городов вы находитесь» в ответ на
        # «когда доставите?» бессмысленно: доставка едет к покупателю.
        return (
            "Скажите город доставки — назову условия и тарифы по нему. "
            "Стоимость и срок для конкретного заказа подтверждает менеджер. "
            "Если речь об уже оформленном заказе, добавьте его номер."
        )
    if topic.key in {"pickup", "business_hours", "delivery"}:
        answer = compose_location_answer(topic.key, facts, city=city)
        if answer and with_volatile_caveat and city:
            caveat = facts.volatile_caveat()
            if caveat:
                answer = f"{answer}\n{caveat}"
        return answer

    policies = {
        "payment": facts.payment,
        "return_refund": facts.returns,
        "warranty": facts.warranty,
    }
    policy = policies.get(topic.key)
    if policy:
        section = {"return_refund": "returns"}.get(topic.key, topic.key)
        draft = facts.draft_caveat(section)
        tail = (
            " Если нужно оформить — напишите «передай менеджеру», я подготовлю "
            "обращение с тем, что уже известно."
        )
        return policy + (f" {draft}" if draft else "") + tail

    if topic.key == "order_status" and facts.response_time:
        return (
            f"{topic.note} Это проверит менеджер — обычно ответ "
            f"{facts.response_time}. Чтобы он разобрался быстро, нужно: "
            f"{'; '.join(topic.needs)}. Напишите это здесь — передам вместе с "
            "историей разговора."
        )
    if topic.key == "b2b_quote":
        lead = facts.lead_times.get("просчёт спецификации")
        if lead:
            return (
                f"{topic.note} Просчёт занимает {lead}. Нужно: "
                f"{'; '.join(topic.needs)}."
            )
    return None


_CITY_NAME_PATTERN = r"[А-ЯЁ][а-яё-]{2,}(?:\s+[А-ЯЁ][а-яё-]{2,}){0,2}"
_DELIVERY_CITY_RE = re.compile(
    rf"\bдоставк\w*[^.!?]{{0,64}}?\b(?:в|до)\s+"
    rf"(?:г(?:ород|\.)?\s*)?({_CITY_NAME_PATTERN})"
)
_EXPLICIT_CITY_RE = re.compile(
    rf"\b(?:в|во|из|до|по)\s+(?:г(?:ород|\.)?\s*)?({_CITY_NAME_PATTERN})"
)
_FROM_CITY_RE = re.compile(rf"\bя\s+из\s+({_CITY_NAME_PATTERN})")
_LEADING_CITY_RE = re.compile(rf"^\s*({_CITY_NAME_PATTERN})\s*,")
_NON_CITY_LEADING_WORDS = {
    "добрый",
    "здравствуйте",
    "подскажите",
    "привет",
    "слушайте",
}


def extract_any_city(message: str) -> str | None:
    """Город, названный покупателем, даже если пункта выдачи там нет.

    Нужен отдельно от :func:`find_city`: на «доставка в Краснодар» бот обязан
    ответить про транспортные компании, а не спрашивать, в каком городе
    покупатель хочет забрать товар самовывозом.
    """
    source = str(message or "")
    # Delivery wording is the strongest relation in the sentence.  Prefer it
    # to a capitalised discourse opener: in "Привет, доставка в Краснодар"
    # the former is not a city, even though it has the same surface shape as
    # the useful short answer "Краснодар, 15-й этаж".
    for pattern in (_DELIVERY_CITY_RE, _FROM_CITY_RE, _EXPLICIT_CITY_RE):
        match = pattern.search(source)
        if match:
            return match.group(1).strip()
    leading = _LEADING_CITY_RE.search(source)
    if not leading:
        return None
    name = leading.group(1).strip()
    if normalize_text(name).split()[0] in _NON_CITY_LEADING_WORDS:
        return None
    return name
