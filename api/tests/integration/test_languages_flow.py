"""Languages end to end: same-language replies, switching mid-chat, handover translation."""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
from sqlalchemy import select

from chatfront.ai.client import AiClient, CallRecord
from chatfront.ai.config import RoleConfig
from chatfront.ai.mock import Fixture, MockProvider
from chatfront.ai.translate import translate
from chatfront.ai.types import Effort, Role
from chatfront.audit.models import AuditEntry
from chatfront.channels.conversation import ShopAssistant, Turn
from chatfront.channels.handover import reply_as_staff, show_to_staff
from chatfront.channels.models import Conversation, Direction, Message
from chatfront.channels.types import OutText
from chatfront.core.languages import load_registry
from chatfront.core.models import Customer, Staff, Tenant
from chatfront.services import Services
from tests.integration.conftest import SHOP


async def _ignore(record: CallRecord) -> None:
    return None


def mock_client(fixtures: list[Fixture]) -> tuple[AiClient, MockProvider]:
    mock = MockProvider(fixtures)
    roles = {role: RoleConfig((), Effort.LOW, 10) for role in Role}
    client = AiClient(roles, mock, mock, _ignore, use_mock=True, usd_to_pkr=Decimal(280))
    return client, mock


def _turn(lang: str, text: str) -> Turn:
    tenant = Tenant(id=uuid.uuid4(), slug="s", name="Gulmohar", template="clothing", profile={})
    message = Message(channel="webchat", kind="text", body=text)
    return Turn(tenant, Conversation(id=uuid.uuid4()), message, [], None, lang)


def _fixture(prompt: str, lang: str, content: str, match: str = "*") -> Fixture:
    return Fixture(prompt=prompt, role="*", match=match, lang=lang, content=content)


async def _say(client: httpx.AsyncClient, services: Services, token: str, text: str) -> str:
    post = {"token": token, "client_id": str(uuid.uuid4()), "text": text}
    assert (await client.post(f"/api/shops/{SHOP}/chat/messages", json=post)).json()["accepted"]
    assert services.worker is not None
    await services.worker.run_once()
    listed = (
        await client.get(f"/api/shops/{SHOP}/chat/messages", headers={"X-Chat-Token": token})
    ).json()
    assert listed[-1]["direction"] == "out"
    return str(listed[-1]["body"])


async def _conversation(services: Services) -> Conversation:
    async with services.db.system() as session:
        found = await session.scalar(select(Conversation))
    assert found is not None
    return found


async def test_replies_follow_the_customer_and_their_requests(
    app: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = app
    token = (await client.post(f"/api/shops/{SHOP}/chat/session")).json()["token"]

    assert "ځواب" in await _say(client, services, token, "دا سوټ په څو دی؟")
    assert (await _conversation(services)).lang == "ps"

    english = await _say(client, services, token, "English mein baat karo")
    assert "recorded reply" in english
    conversation = await _conversation(services)
    assert (conversation.lang, conversation.lang_choice) == ("ur-Latn", "en")

    # The choice holds even when the customer goes back to writing Urdu.
    assert "recorded reply" in await _say(client, services, token, "یہ سوٹ کتنے کا ہے؟")
    assert (await _conversation(services)).lang == "ur"

    roman = await _say(client, services, token, "Urdu mein likho please")
    assert "jawab" in roman
    async with services.db.system() as session:
        inbound = list(
            await session.scalars(
                select(Message)
                .where(Message.direction == Direction.IN)
                .order_by(Message.created_at)
            )
        )
        replies = list(
            await session.scalars(
                select(Message)
                .where(Message.direction == Direction.OUT)
                .order_by(Message.created_at)
            )
        )
        customer = await session.scalar(select(Customer))
    assert [m.payload["lang"] for m in inbound] == ["ps", "ur-Latn", "ur", "ur-Latn"]
    assert [m.payload["lang"] for m in replies] == ["ps", "en", "en", "ur-Latn"]
    listed = (
        await client.get(f"/api/shops/{SHOP}/chat/messages", headers={"X-Chat-Token": token})
    ).json()
    assert [m["lang"] for m in listed[:2]] == ["ps", "ps"]
    assert customer is not None
    assert customer.lang == "ur-Latn"


async def test_an_answer_in_the_wrong_language_is_asked_again() -> None:
    client, mock = mock_client(
        [
            _fixture("shop_assistant", "en", "Yeh suit 4,850 ka hai bhai, kal tak mil jayega."),
            _fixture(
                "shop_assistant", "en", "This suit is Rs 4,850.", match="Write that answer again"
            ),
        ]
    )
    replies = await ShopAssistant(client).respond(_turn("en", "How much is this suit?"))
    assert replies == [OutText("This suit is Rs 4,850.")]
    assert len(mock.requests) == 2
    assert mock.requests[0].lang == "en"


async def test_then_translated_when_asking_again_does_not_help() -> None:
    urdu = "یہ سوٹ 4,850 روپے کا ہے۔ کل تک ڈیلیوری ہو جائے گی۔"
    client, mock = mock_client(
        [
            _fixture("shop_assistant", "ps", urdu),
            _fixture("translate", "ps", "دا سوټ 4,850 روپۍ دی. سبا به درته ورسېږي."),
        ]
    )
    replies = await ShopAssistant(client).respond(_turn("ps", "دا سوټ په څو دی؟"))
    assert replies == [OutText("دا سوټ 4,850 روپۍ دی. سبا به درته ورسېږي.")]
    assert [r.prompt for r in mock.requests] == [
        "shop_assistant@v2",
        "shop_assistant@v2",
        "translate@v1",
    ]


async def test_close_languages_are_not_asked_again() -> None:
    client, mock = mock_client(
        [_fixture("shop_assistant", "ur-Latn", "Namaste bhaiya, ye suit 4850 ka hai.")]
    )
    replies = await ShopAssistant(client).respond(_turn("ur-Latn", "kitne ka hai?"))
    assert replies == [OutText("Namaste bhaiya, ye suit 4850 ka hai.")]
    assert len(mock.requests) == 1


async def test_translation_falls_back_to_the_original_words() -> None:
    client, _ = mock_client([])
    registry = load_registry()
    failed = await translate(
        client, "salam", source="ur-Latn", target=registry.get("ps"), tenant_id=uuid.uuid4()
    )
    assert (failed.text, failed.translated) == ("salam", False)
    same = await translate(
        client, "hi", source="afb", target=registry.get("ar"), tenant_id=uuid.uuid4()
    )
    assert (same.text, same.translated) == ("hi", False)


async def test_handover_owner_reads_and_writes_in_their_own_language(
    app: tuple[httpx.AsyncClient, Services],
) -> None:
    client, services = app
    assert isinstance(services.ai.mock, MockProvider)
    services.ai.mock.fixtures.extend(
        [
            _fixture("translate", "ur", "یہ سوٹ کتنے کا ہے؟", match="سوټ"),
            _fixture("translate", "ps", "دا سوټ 4,850 روپۍ دی.", match="4,850"),
        ]
    )
    token = (await client.post(f"/api/shops/{SHOP}/chat/session")).json()["token"]
    await _say(client, services, token, "دا سوټ په څو دی؟")
    conversation = await _conversation(services)
    async with services.db.system() as session:
        inbound = await session.scalar(select(Message).where(Message.direction == Direction.IN))
        owner = await session.scalar(select(Staff).where(Staff.tenant_id == conversation.tenant_id))
        assert inbound is not None
        assert owner is not None
        owner.lang = "ur"
    tenant_id = conversation.tenant_id

    shown = await show_to_staff(services, tenant_id, inbound.id, "ur")
    assert (shown.text, shown.translated) == ("یہ سوٹ کتنے کا ہے؟", True)
    asked = len(services.ai.mock.requests)
    again = await show_to_staff(services, tenant_id, inbound.id, "ur")
    assert again.text == shown.text
    assert len(services.ai.mock.requests) == asked  # remembered, not translated twice

    sent = await reply_as_staff(
        services, tenant_id, conversation.id, owner.id, "یہ سوٹ 4,850 روپے کا ہے۔"
    )
    assert sent.body == "دا سوټ 4,850 روپۍ دی."
    assert sent.payload["original"] == "یہ سوٹ 4,850 روپے کا ہے۔"
    assert (sent.payload["by"], sent.payload["original_lang"]) == ("staff", "ur")
    async with services.db.system() as session:
        stored = await session.get(Message, inbound.id)
        actions = list(await session.scalars(select(AuditEntry.action)))
    assert stored is not None
    assert stored.payload["translations"] == {"ur": "یہ سوٹ کتنے کا ہے؟"}
    assert "message.staff_reply" in actions
