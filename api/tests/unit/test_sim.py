"""The customer simulator without a database (feature 110): the fifty customers, how a chat is
shown to the model playing one, and which recorded chats a replay uses."""

from __future__ import annotations

import uuid
from decimal import Decimal

from chatfront.ai.client import AiClient, CallRecord
from chatfront.ai.config import RoleConfig
from chatfront.ai.mock import Fixture, MockProvider
from chatfront.ai.types import Effort, Role
from chatfront.channels.models import Direction, Message
from chatfront.core.models import Tenant
from chatfront.eval.cases import Case, load_cases
from chatfront.sim.personas import GOALS, Goal, load_personas, persona
from chatfront.sim.play import (
    NOT_STARTED,
    Move,
    last_buttons,
    live_move,
    phone,
    replay_move,
    transcript,
)
from chatfront.sim.runner import LANGS, recorded

CARD = [{"id": "place:1", "title": "Place order"}, {"id": "edit:1", "title": "Change"}]


def said(direction: Direction, body: str, **payload: object) -> Message:
    return Message(direction=direction, body=body, payload=dict(payload))


class TestPersonas:
    def test_fifty_urdu_and_roman_urdu_customers_with_something_to_do(self) -> None:
        people = load_personas()
        assert len(people) == 50
        assert len({p.id for p in people}) == 50
        assert {p.lang for p in people} == set(LANGS)
        assert {p.goal for p in people} == set(Goal)
        assert set(GOALS) == set(Goal)

    def test_a_brief_says_who_they_are_and_never_asks_for_a_picture_of_money(self) -> None:
        sana = persona("p01")
        brief = sana.brief("Printed lawn 3-piece")
        assert "Sana Iqbal" in brief
        assert "1 x Printed lawn 3-piece" in brief
        payer = next(p for p in load_personas() if p.pay == "transfer")
        assert "you never send a picture" in payer.brief("Sugar")

    def test_each_customer_has_their_own_made_up_number(self) -> None:
        numbers = {phone(p) for p in load_personas()}
        assert len(numbers) == 50
        assert phone(persona("p07")) == "0300 5550007"


class TestPlay:
    def test_the_model_reads_the_chat_with_the_cards_buttons(self) -> None:
        chat = [
            said(Direction.IN, "lawn suit hai?"),
            said(Direction.OUT, "Aap ka order", buttons=CARD),
            said(Direction.OUT, "Place order dabayein."),
        ]
        shown = transcript(chat)
        assert shown.splitlines() == [
            "Customer: lawn suit hai?",
            "Shop: Aap ka order",
            "  Buttons: [0] Place order  [1] Change",
            "Shop: Place order dabayein.",
        ]
        assert transcript([]) == NOT_STARTED

    def test_the_card_to_tap_is_the_latest_since_the_customer_wrote(self) -> None:
        card_then_words = [
            said(Direction.OUT, "Aap ka order", buttons=CARD),
            said(Direction.OUT, "Place order dabayein."),
        ]
        assert last_buttons(card_then_words) == CARD
        old_card = [*card_then_words, said(Direction.IN, "ruk jayein")]
        assert last_buttons(old_card) == []
        assert last_buttons([]) == []

    def test_a_replay_says_or_taps_the_recorded_line(self) -> None:
        case = next(c for c in load_cases() if c.id == "checkout-02")
        first = replay_move(case, 0, None)
        assert first.text.startswith("Linen kurta L size ek")
        assert replay_move(case, 2, None) == Move(tap=0)
        assert replay_move(case, 3, None) == Move(done=True)
        status = next(c for c in load_cases() if "{order}" in (c.steps[0].say or ""))
        assert "AB12CD" in replay_move(status, 0, "AB12CD").text

    async def test_the_model_playing_a_customer_answers_in_json(self) -> None:
        mock = MockProvider(
            [
                Fixture("customer_sim", "simulator", "has not started", json={"message": " hi  "}),
                Fixture("customer_sim", "simulator", "Buttons", json={"tap": 0}),
                Fixture("customer_sim", "simulator", "Shop:", json={"message": ""}),
            ]
        )
        roles = {Role.SIMULATOR: RoleConfig((), Effort.LOW, 1000)}
        ai = AiClient(roles, mock, mock, _no_meter, use_mock=True, usd_to_pkr=Decimal(280))
        shop = Tenant(id=uuid.uuid4(), name="Gulmohar Lawn House", slug="g", template="clothing")
        who, chat = persona("p01"), uuid.uuid4()
        assert await live_move(ai, shop, who, "Lawn", [], chat) == Move(text="hi")
        card = [said(Direction.OUT, "Aap ka order", buttons=CARD)]
        assert await live_move(ai, shop, who, "Lawn", card, chat) == Move(tap=0)
        words = [said(Direction.OUT, "Shukriya")]
        assert await live_move(ai, shop, who, "Lawn", words, chat) == Move(done=True)
        system = mock.requests[0].messages[0].text()
        assert "Roman Urdu" in system or "Latin" in system
        assert mock.requests[0].ref == ("simulation", str(chat))


async def _no_meter(record: CallRecord) -> None:
    del record


class TestReplayPlan:
    def test_a_busy_day_has_a_bit_of_everything_and_an_order_first(self) -> None:
        cases: list[Case] = recorded("clothing")
        assert cases[0].kind == "checkout"
        assert {c.lang for c in cases} <= set(LANGS)
        kinds = [c.kind for c in cases[:5]]
        assert len(set(kinds)) == 5  # no kind twice before every kind has come once
        assert recorded("services") == []  # no recorded chats with a salon
