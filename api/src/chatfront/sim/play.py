"""A simulated customer's next move (feature 110): Glimmer reads the chat and writes what the
customer says next, or a recorded conversation gives the next line when no model is set."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Role
from chatfront.channels.models import Direction, Message
from chatfront.core.languages import load_registry
from chatfront.core.models import Tenant
from chatfront.eval.cases import Case
from chatfront.sim.personas import Persona

TRANSCRIPT_MESSAGES = 16  # what the customer remembers of the chat
NOT_STARTED = "(The chat has not started yet. Write your first message.)"


@dataclass(frozen=True, slots=True)
class Move:
    text: str = ""
    tap: int | None = None  # a button of the shop's last card
    done: bool = False


class SimMove(BaseModel):
    """What the model playing the customer answers."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(default="", max_length=600)
    tap: int | None = None
    done: bool = False


def buttons_of(message: Message) -> list[dict[str, Any]]:
    found = (message.payload or {}).get("buttons") if message.direction == Direction.OUT else None
    return list(found) if isinstance(found, list) else []


def last_buttons(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """The buttons on the shop's latest card since the customer last wrote: the card may come
    before the words that go with it."""
    for message in reversed(messages):
        if message.direction == Direction.IN:
            return []
        if buttons := buttons_of(message):
            return buttons
    return []


def transcript(messages: Sequence[Message]) -> str:
    lines: list[str] = []
    for message in messages[-TRANSCRIPT_MESSAGES:]:
        who = "Customer" if message.direction == Direction.IN else "Shop"
        lines.append(f"{who}: {message.body or ''}")
        if buttons := buttons_of(message):
            shown = "  ".join(f"[{i}] {b.get('title', '')}" for i, b in enumerate(buttons))
            lines.append(f"  Buttons: {shown}")
    return "\n".join(lines) or NOT_STARTED


def phone(persona: Persona) -> str:
    """A made-up mobile number in the Pakistani pattern, one per customer."""
    return f"0300 555{persona.id[1:]:0>4}"


async def live_move(
    ai: AiClient,
    tenant: Tenant,
    persona: Persona,
    product: str,
    messages: Sequence[Message],
    chat_id: uuid.UUID,
) -> Move:
    prompt = load_prompt("customer_sim")
    language = load_registry().get(persona.lang)
    system = prompt.render(
        persona=persona.brief(product),
        language=language.prompt,
        name=persona.name,
        phone=phone(persona),
    )
    request = ChatRequest(
        role=Role.SIMULATOR,
        messages=[ChatMessage("system", system), ChatMessage("user", transcript(messages))],
        tenant_id=tenant.id,
        prompt=prompt.ref,
        ref=("simulation", str(chat_id)),
        lang=persona.lang,
    )
    move, _ = await ai.complete_json(request, SimMove)
    text = " ".join(move.message.split())
    if move.done or (not text and move.tap is None):
        return Move(done=True)
    return Move(text=text if move.tap is None else "", tap=move.tap)


def replay_move(case: Case, step: int, order_code: str | None) -> Move:
    """The recorded conversation's next line; done when it has no more."""
    if step >= len(case.steps):
        return Move(done=True)
    line = case.steps[step]
    if line.tap is not None:
        return Move(tap=line.tap)
    return Move(text=(line.say or "").replace("{order}", order_code or ""))
