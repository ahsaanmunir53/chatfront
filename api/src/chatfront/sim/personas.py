"""The fifty simulated customers (feature 110): invented people in Pakistani cities, writing in
Urdu or Roman Urdu, each with a mood and something they came for.

They are synthetic, like all demo data. None of them ever sends a payment screenshot: a
customer who pays by transfer says they will, and the order waits for payment.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PERSONAS_FILE = Path(__file__).parent / "data" / "personas.json"


class Goal(StrEnum):
    BUY = "buy"
    BARGAIN = "bargain"
    ASK = "ask"
    CHANGE = "change"
    HUMAN = "human"
    GIFT = "gift"


# What each goal asks of the customer, for the model playing them.
GOALS: dict[Goal, str] = {
    Goal.BUY: "Find the product, check the price and colour or size, and order it.",
    Goal.BARGAIN: (
        "Like the product but ask for a better price once or twice before deciding; order "
        "only if the price comes down or the shop explains why it cannot."
    ),
    Goal.ASK: (
        "Ask about the product, delivery time and charges, and cash on delivery, then say "
        "you will think about it. Do not order."
    ),
    Goal.CHANGE: "Add the product, then change the quantity or colour once, then order.",
    Goal.HUMAN: "Ask one question, then ask to talk to the owner or a person.",
    Goal.GIFT: "Order the product as a gift, delivered to a relative in another city.",
}

MOODS: dict[str, str] = {
    "polite": "polite and patient, with greetings",
    "hurried": "in a hurry: short messages, no greetings",
    "chatty": "chatty, with small talk and several questions at once",
    "unsure": "unsure, asks for advice and takes time to decide",
    "direct": "direct and to the point",
}


class Persona(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^p\d{2}$")
    name: str
    lang: Literal["ur", "ur-Latn"]
    city: str
    area: str
    mood: Literal["polite", "hurried", "chatty", "unsure", "direct"]
    goal: Goal
    pay: Literal["cod", "transfer"]
    quantity: int = Field(ge=1, le=5)

    def brief(self, product: str) -> str:
        """Who the customer is and what they came for, for the model playing them."""
        paying = (
            "cash on delivery"
            if self.pay == "cod"
            else "a bank or wallet transfer (you say you will send it; you never send a picture)"
        )
        return (
            f"You are {self.name}, a customer in {self.area}, {self.city}. You are "
            f"{MOODS[self.mood]}. You want {self.quantity} x {product}. {GOALS[self.goal]} "
            f"You pay by {paying}."
        )


class PersonaFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    personas: tuple[Persona, ...] = Field(min_length=1)


@lru_cache
def load_personas(path: Path = PERSONAS_FILE) -> tuple[Persona, ...]:
    return PersonaFile.model_validate_json(path.read_text(encoding="utf-8")).personas


def persona(persona_id: str) -> Persona:
    return next(p for p in load_personas() if p.id == persona_id)
