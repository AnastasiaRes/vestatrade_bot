"""Deterministic, request-scoped planning for compound dialogue turns.

The existing intent router deliberately returns one catalogue intent.  That is
not enough for turns such as "show three models with prices and explain the
discount": catalogue lookup and commercial policy are two independent acts.
This module recognises only the small, high-confidence set needed by the live
dialogue regressions.  It performs no I/O, stores no raw message or contact,
and leaves all unrecognised turns to the existing orchestration pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .commerce_topics import match_commerce_topic
from .utils import normalize_text


class TurnAct(str, Enum):
    """A user-visible purpose expressed in one message."""

    BROWSE_OPTIONS = "browse_options"
    PRICE_LOOKUP = "price_lookup"
    COMMERCE_POLICY = "commerce_policy"
    DISCOUNT_POLICY = "discount_policy"
    REQUEST_STORE_CONTACT = "request_store_contact"
    REQUEST_THIRD_PARTY_CONTACT = "request_third_party_contact"
    PROVIDE_CUSTOMER_CONTACT = "provide_customer_contact"
    REQUEST_HANDOFF = "request_handoff"


class SelectionMode(str, Enum):
    """Whether the customer wants examples or a compatibility recommendation."""

    UNSPECIFIED = "unspecified"
    BROWSE = "browse"
    RECOMMEND = "recommend"


class ContactDirection(str, Enum):
    """Direction in which contact data is expected to travel."""

    STORE_TO_CUSTOMER = "store_to_customer"
    CUSTOMER_TO_STORE = "customer_to_store"
    THIRD_PARTY = "third_party"


class TurnAction(str, Enum):
    """Controller actions in the order in which they should be executed."""

    ANSWER_STORE_CONTACT = "answer_store_contact"
    ANSWER_THIRD_PARTY_CONTACT = "answer_third_party_contact"
    CATALOG_BROWSE = "catalog_browse"
    CATALOG_PRICE = "catalog_price"
    ANSWER_COMMERCE_POLICY = "answer_commerce_policy"
    ANSWER_DISCOUNT_POLICY = "answer_discount_policy"
    CONTINUE_HANDOFF = "continue_handoff"


@dataclass(frozen=True)
class TurnFrame:
    """Typed semantic facts for the current turn only.

    The raw message and extracted contact are intentionally absent.  The
    orchestrator already owns both, while duplicating them here would create a
    second PII-bearing state object.
    """

    acts: tuple[TurnAct, ...] = ()
    selection_mode: SelectionMode = SelectionMode.UNSPECIFIED
    requested_count: int | None = None
    contact_direction: ContactDirection | None = None
    customer_contact_present: bool = False
    product_context_present: bool = False

    def has(self, act: TurnAct) -> bool:
        return act in self.acts


@dataclass(frozen=True)
class TurnPlan:
    """A bounded plan that can wrap the legacy orchestrator."""

    actions: tuple[TurnAction, ...] = ()
    bypass_engineering_preflight: bool = False
    skip_commerce_short_circuit: bool = False
    ignore_pending_handoff_for_turn: bool = False

    def has(self, action: TurnAction) -> bool:
        return action in self.actions


_PRICE_RE = re.compile(
    r"\b(?:цен(?:а|ы|е|у|ой|ою|ам|ами|ах|ник\w*|ов\w*)|прайс\w*|"
    r"сто(?:ит|ят|имость\w*)|сколько\s+(?:стоит|стоят|выйдет|будет)|"
    r"сколько\s+(?:будет\s+)?за\b|почем|по\s+чем)\b"
)
_PRESENTATION_RE = re.compile(r"\b(?:покаж\w*|дай\w*|назов\w*|привед\w*)\b")
_OPTION_NOUN_RE = re.compile(r"\b(?:вариант\w*|модел\w*|позици\w*|товар\w*)\b")
_PRODUCT_NOUN_RE = re.compile(
    r"\b(?:радиатор|насос|кот[её]л|труб|кран|клапан|фитинг|бойлер|"
    r"водонагревател|гидроаккумулятор|канализац|термостат|коллектор)\w*\b"
)
_DISCOUNT_RE = re.compile(
    r"\b(?:скидк\w*|распродаж\w*|промокод\w*|скиньте)\b|"
    r"\b(?:дешевле\s+сделай|сделайте\s+дешевле)\b"
)
_SKU_HINT_RE = re.compile(
    r"\b(?=[a-zа-я0-9./-]*[a-zа-я])(?=[a-zа-я0-9./-]*\d)"
    r"[a-zа-я0-9]+(?:[-./][a-zа-я0-9]+)+\b"
)
_COUNT_RANGE_RE = re.compile(
    r"\b(\d{1,2})\s*(?:-|до)\s*(\d{1,2})\s+"
    r"(?:вариант\w*|модел\w*|позици\w*|товар\w*)\b"
)
_COUNT_SINGLE_RE = re.compile(
    r"\b(\d{1,2})\s+(?:вариант\w*|модел\w*|позици\w*|товар\w*)\b"
)

_BROWSE_SIGNALS = (
    "чаще берут",
    "обычно берут",
    "популярн",
    "ходовые модел",
    "типовые модел",
    "для примера",
    "просто покаж",
    "просто дай",
    "без технических деталей",
    "не нужны технические детали",
    "не надо уточнять",
    "без уточнений",
    "не хочу уточнять",
    "не буду уточнять",
)
_RECOMMEND_SIGNALS = (
    "подбери",
    "подберите",
    "помоги подобрать",
    "помогите подобрать",
    "что подойдет",
    "какой подойдет",
    "какой лучше взять",
    "что лучше взять",
    "что брать",
    "для моей системы",
)

_CONTACT_NOUN_RE = re.compile(
    r"\b(?:телефон\w*|номер\w*|email|e-mail|имейл\w*|почт\w*|"
    r"контакт\w*|кантакт\w*)\b"
)
_STORE_CONTACT_IMPERATIVE_RE = re.compile(
    r"\b(?:дай\w*|пришл\w*|отправ\w*|напиш\w*|подскаж\w*)\b"
    r"[^.!?]{0,36}\bмне\b[^.!?]{0,36}"
    r"\b(?:телефон\w*|номер\w*|email|e-mail|имейл\w*|почт\w*|"
    r"контакт\w*|кантакт\w*)\b"
)
_STORE_CONTACT_POSSESSIVE_RE = re.compile(
    r"\b(?:ваш\w*|магазин(?:а|ный)\w*|менеджер(?:а|ский)\w*|"
    r"филиал(?:а|ьный)\w*)\b[^.!?]{0,16}"
    r"\b(?:телефон\w*|номер\w*|email|e-mail|имейл\w*|почт\w*|"
    r"контакт\w*|кантакт\w*)\b"
    r"|\b(?:телефон\w*|номер\w*|email|e-mail|имейл\w*|почт\w*|"
    r"контакт\w*|кантакт\w*)\b"
    r"[^.!?]{0,12}\b(?:магазина|менеджера|филиала)\b"
)
_STORE_CONTACT_CONNECT_RE = re.compile(
    r"\bкак\s+(?:связаться|свезаться|связатсья)\b"
)
_STORE_CONTACT_GENERIC_QUESTION_RE = re.compile(
    r"\b(?:куда\s+позвонить|куда\s+написать|"
    r"какие\s+(?:контакт|кантакт)\w*)\b"
)
_STORE_CONTACT_TARGET_RE = re.compile(
    r"\b(?:магазин|филиал|офис|менедж|минедж|консульт|кансульт|"
    r"сотруд|сатруд|продав|продов|прадав|администр|адмнистр|"
    r"оператор|опиратор|человек|челавек|вами)\w*\b"
)
_THIRD_PARTY_CONTACT_TARGET_RE = re.compile(
    r"\b(?:производител|поставщик|завод|бренд|дистрибьютор|"
    r"документац|инструкц)\w*\b|\bих\s+(?:email|e-mail|имейл|почт|"
    r"телефон|номер|контакт)\w*\b"
)
_CUSTOMER_CONTACT_OWNERSHIP_RE = re.compile(
    r"\b(?:мой|моя|мои)\s+(?:(?:рабоч|личн|контактн)\w*\s+)?"
    r"(?:email|e-mail|имейл|почт|телефон|номер|контакт)\w*\b|"
    r"\b(?:связаться|связь)\s+со\s+мной\b|\bдля\s+связи\b"
)
_HANDOFF_RE = re.compile(
    r"\b(?:передай|передайте|переключи|переключите|соедини|соедините|позови|позовите)\w*\b"
    r"[^.!?]{0,50}\b(?:менеджер|оператор|консультант|сотрудник|продавец|человек)\w*\b"
    r"|\b(?:хочу|нужен|нужна|можно)\b[^.!?]{0,36}"
    r"\b(?:менеджер|оператор|консультант|сотрудник|продавец|человек)\w*\b"
)


def _requested_count(text: str) -> int | None:
    match = _COUNT_RANGE_RE.search(text)
    if match:
        # The upper edge is what a presentation limit means.  Bound it so a
        # phrase such as "show 100 models" cannot expand a catalogue response.
        return min(5, max(int(match.group(1)), int(match.group(2))))
    match = _COUNT_SINGLE_RE.search(text)
    if match:
        return min(5, max(1, int(match.group(1))))
    return None


def _is_store_contact_request(text: str) -> bool:
    if _THIRD_PARTY_CONTACT_TARGET_RE.search(text):
        return False
    if _STORE_CONTACT_GENERIC_QUESTION_RE.search(text):
        return True
    if _STORE_CONTACT_CONNECT_RE.search(text):
        # "How do I contact the manufacturer?" is a product-support question,
        # not a request for Vesta's branch contacts.  Require an explicit store
        # or human-store target for this otherwise broad wording.
        return bool(_STORE_CONTACT_TARGET_RE.search(text))
    if not _CONTACT_NOUN_RE.search(text):
        return False
    return bool(
        _STORE_CONTACT_IMPERATIVE_RE.search(text)
        or _STORE_CONTACT_POSSESSIVE_RE.search(text)
    )


def _is_third_party_contact_request(
    text: str,
    *,
    customer_contact_present: bool = False,
) -> bool:
    if not _THIRD_PARTY_CONTACT_TARGET_RE.search(text):
        return False
    explicit_request = bool(
        _STORE_CONTACT_GENERIC_QUESTION_RE.search(text)
        or _STORE_CONTACT_CONNECT_RE.search(text)
        or _STORE_CONTACT_IMPERATIVE_RE.search(text)
    )
    if explicit_request:
        return True
    # A short ``телефон производителя?`` is still a request.  When the
    # message already contains an actual address/number, though, the same
    # words describe third-party data and must never be treated as a request
    # for, or as, the customer's callback contact.
    return bool(
        _CONTACT_NOUN_RE.search(text)
        and not customer_contact_present
        and len(text.split()) <= 10
    )


def _price_explicitly_targets_product(text: str) -> bool:
    """Distinguish product price from delivery/payment cost in one clause."""

    for price_match in _PRICE_RE.finditer(text):
        for product_match in _PRODUCT_NOUN_RE.finditer(text):
            if price_match.end() <= product_match.start():
                between = text[price_match.end() : product_match.start()]
            elif product_match.end() <= price_match.start():
                between = text[product_match.end() : price_match.start()]
            else:
                between = ""
            if len(between) <= 12 and "достав" not in between:
                return True
    return False


def _price_explicitly_targets_commerce(text: str) -> bool:
    """Return whether the price wording names delivery/service, not a product."""

    commerce_noun = (
        r"(?:доставк|монтаж|самовывоз|оплат|возврат|гаранти|отгрузк)\w*"
    )
    return bool(
        re.search(
            rf"(?:сколько\s+(?:будет\s+)?стоит|стоимость|цена)"
            rf"\s+(?:самой\s+|за\s+)?{commerce_noun}",
            text,
        )
        or re.search(
            rf"{commerce_noun}[^.!?]{{0,24}}"
            r"(?:сколько\s+(?:будет\s+)?стоит|стоимость|цена)",
            text,
        )
    )


def _is_browse_request(text: str, requested_count: int | None) -> bool:
    has_presentation = bool(_PRESENTATION_RE.search(text))
    has_option_noun = bool(_OPTION_NOUN_RE.search(text))
    has_product_noun = bool(_PRODUCT_NOUN_RE.search(text))
    if any(signal in text for signal in _BROWSE_SIGNALS) and (
        has_presentation or has_option_noun or has_product_noun
    ):
        return True
    if requested_count is not None and has_presentation and has_option_noun:
        return True
    # Preserve the legacy distinction: a short standalone "show options" is a
    # command, while a rich product refinement containing those two words is
    # not silently reduced to a generic category browse.
    return has_presentation and has_option_noun and len(text.split()) <= 6


class TurnPlanner:
    """Build a deterministic frame and an ordered plan for one message."""

    def frame(
        self,
        message: str,
        *,
        customer_contact_present: bool = False,
        customer_contact_owned: bool | None = None,
        product_context_present: bool = False,
        pending_selection_mode: SelectionMode | str | None = None,
    ) -> TurnFrame:
        # ``normalize_text`` removes en/em dashes; canonicalise them first so a
        # natural "2–3 models" range remains machine-readable.
        text = normalize_text(str(message or "").replace("–", "-").replace("—", "-"))
        if not text:
            return TurnFrame(customer_contact_present=customer_contact_present)

        third_party_contact_request = _is_third_party_contact_request(
            text,
            customer_contact_present=customer_contact_present,
        )
        if third_party_contact_request:
            return TurnFrame(
                acts=(TurnAct.REQUEST_THIRD_PARTY_CONTACT,),
                contact_direction=ContactDirection.THIRD_PARTY,
                customer_contact_present=False,
                product_context_present=product_context_present,
            )

        store_contact = _is_store_contact_request(text)
        if store_contact:
            # Direction wins over the nearby word "manager": in "send me the
            # phone and I will pass it to the manager" the user is not asking
            # the bot to transfer their own contact.
            return TurnFrame(
                acts=(TurnAct.REQUEST_STORE_CONTACT,),
                contact_direction=ContactDirection.STORE_TO_CUSTOMER,
                customer_contact_present=False,
                product_context_present=product_context_present,
            )

        commerce_topic = match_commerce_topic(text)
        requested_count = _requested_count(text)
        browse_candidate = _is_browse_request(text, requested_count)
        explicit_catalog_browse = bool(
            _PRESENTATION_RE.search(text)
            and (_OPTION_NOUN_RE.search(text) or requested_count is not None)
        )
        if commerce_topic is not None and not explicit_catalog_browse:
            # "Не надо уточнять город доставки радиатора" is a delivery
            # question, not permission to dump radiator cards.  A commercial
            # topic yields to browse only when the user explicitly asks to
            # show catalogue options in the same turn.
            browse_candidate = False
        recommends = any(signal in text for signal in _RECOMMEND_SIGNALS)
        has_product_noun = bool(_PRODUCT_NOUN_RE.search(text))
        asks_discount = bool(_DISCOUNT_RE.search(text))
        asks_price = bool(
            _PRICE_RE.search(text)
            and not (
                commerce_topic is not None
                and commerce_topic.key != "discount"
                and _price_explicitly_targets_commerce(text)
            )
            and (
                commerce_topic is None
                or commerce_topic.key == "discount"
                or (has_product_noun and _price_explicitly_targets_product(text))
                or bool(_SKU_HINT_RE.search(text))
                or product_context_present
            )
        )
        asks_handoff = bool(_HANDOFF_RE.search(text))
        contact_owned = (
            bool(
                customer_contact_present
                and _CUSTOMER_CONTACT_OWNERSHIP_RE.search(text)
            )
            if customer_contact_owned is None
            else bool(customer_contact_owned)
        )
        third_party_contact = bool(
            customer_contact_present
            and _THIRD_PARTY_CONTACT_TARGET_RE.search(text)
            and not contact_owned
        )
        owned_customer_contact = bool(
            customer_contact_present
            and (contact_owned or not _THIRD_PARTY_CONTACT_TARGET_RE.search(text))
        )

        explicit_browse_override = any(signal in text for signal in _BROWSE_SIGNALS)
        if recommends and not explicit_browse_override:
            selection_mode = SelectionMode.RECOMMEND
        elif browse_candidate:
            selection_mode = SelectionMode.BROWSE
        elif asks_price:
            pending_mode = str(
                getattr(pending_selection_mode, "value", pending_selection_mode)
                or ""
            )
            # A plain catalogue price lookup can bypass compatibility questions.
            # A budget supplied while compatibility selection is active is an
            # additive constraint and must keep that goal alive.
            selection_mode = (
                SelectionMode.RECOMMEND
                if pending_mode == SelectionMode.RECOMMEND.value
                else SelectionMode.BROWSE
            )
        else:
            selection_mode = SelectionMode.UNSPECIFIED

        acts: list[TurnAct] = []
        if selection_mode == SelectionMode.BROWSE and browse_candidate:
            acts.append(TurnAct.BROWSE_OPTIONS)
        if asks_price:
            acts.append(TurnAct.PRICE_LOOKUP)
        if commerce_topic is not None and commerce_topic.key != "discount":
            acts.append(TurnAct.COMMERCE_POLICY)
        if asks_discount:
            acts.append(TurnAct.DISCOUNT_POLICY)
        if owned_customer_contact:
            acts.append(TurnAct.PROVIDE_CUSTOMER_CONTACT)
        if asks_handoff:
            acts.append(TurnAct.REQUEST_HANDOFF)

        direction = None
        if customer_contact_present:
            direction = (
                ContactDirection.THIRD_PARTY
                if third_party_contact
                else ContactDirection.CUSTOMER_TO_STORE
            )
        return TurnFrame(
            acts=tuple(acts),
            selection_mode=selection_mode,
            requested_count=requested_count,
            contact_direction=direction,
            customer_contact_present=(
                owned_customer_contact
            ),
            product_context_present=product_context_present,
        )

    def plan(
        self,
        frame: TurnFrame,
        *,
        pending_handoff: bool = False,
    ) -> TurnPlan:
        if frame.has(TurnAct.REQUEST_STORE_CONTACT):
            return TurnPlan(
                actions=(TurnAction.ANSWER_STORE_CONTACT,),
                bypass_engineering_preflight=True,
                ignore_pending_handoff_for_turn=pending_handoff,
            )
        if frame.has(TurnAct.REQUEST_THIRD_PARTY_CONTACT):
            return TurnPlan(
                actions=(TurnAction.ANSWER_THIRD_PARTY_CONTACT,),
                bypass_engineering_preflight=True,
                ignore_pending_handoff_for_turn=pending_handoff,
            )

        actions: list[TurnAction] = []
        if frame.has(TurnAct.BROWSE_OPTIONS):
            actions.append(TurnAction.CATALOG_BROWSE)
        if frame.has(TurnAct.PRICE_LOOKUP):
            actions.append(TurnAction.CATALOG_PRICE)
        if frame.has(TurnAct.COMMERCE_POLICY):
            actions.append(TurnAction.ANSWER_COMMERCE_POLICY)
        if frame.has(TurnAct.DISCOUNT_POLICY):
            actions.append(TurnAction.ANSWER_DISCOUNT_POLICY)
        if frame.has(TurnAct.REQUEST_HANDOFF) or (
            pending_handoff and frame.has(TurnAct.PROVIDE_CUSTOMER_CONTACT)
        ):
            actions.append(TurnAction.CONTINUE_HANDOFF)

        catalog_and_policy = bool(
            (frame.has(TurnAct.BROWSE_OPTIONS) or frame.has(TurnAct.PRICE_LOOKUP))
            and (
                frame.has(TurnAct.COMMERCE_POLICY)
                or frame.has(TurnAct.DISCOUNT_POLICY)
            )
        )
        return TurnPlan(
            actions=tuple(actions),
            bypass_engineering_preflight=frame.selection_mode == SelectionMode.BROWSE,
            skip_commerce_short_circuit=catalog_and_policy,
        )
