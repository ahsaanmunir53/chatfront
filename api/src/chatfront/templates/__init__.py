"""Business-type templates: a new kind of business is a JSON file, not new code."""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from chatfront.core.models import Tier

TEMPLATES_DIR = Path(__file__).parent


class Text3(BaseModel):
    """The same text in English, Urdu and Roman Urdu."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    en: str
    ur: str
    roman: str = Field(alias="ur-Latn")


class ProductField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    type: Literal["text", "number", "choice", "bool"]
    label: Text3
    required: bool = False
    options: list[str] = Field(default_factory=list)


class OrderStep(StrEnum):
    SIZE_CHECK = "size_check"
    DELIVERY_SLOT = "delivery_slot"
    ADDRESS = "address"
    LOCATION_PIN = "location_pin"
    APPOINTMENT = "appointment"
    QUOTE_APPROVAL = "quote_approval"
    PAYMENT_CHOICE = "payment_choice"
    FULL_PAYMENT_BEFORE_DISPATCH = "full_payment_before_dispatch"


class PaymentMethod(StrEnum):
    COD = "cod"
    COD_WITH_ADVANCE = "cod_with_advance"
    TRANSFER = "transfer"
    DEPOSIT = "deposit"
    CREDIT = "credit"


class AgentQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    text: Text3


class DemoVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    options: dict[str, str] = Field(default_factory=dict)
    price: int | None = None  # major units; None means the product's price
    stock: int | None = None


class DemoProduct(BaseModel):
    """A synthetic product for the demo shop. Prices are in major units (rupees)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    price: int
    category: str
    description: str
    attributes: dict[str, str] = Field(default_factory=dict)
    variants: list[DemoVariant] = Field(default_factory=lambda: [DemoVariant()])


class DemoShop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    tier: Tier
    city: str
    tagline: Text3
    products: list[DemoProduct] = Field(default_factory=list)


class BusinessTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: Text3
    product_fields: list[ProductField]
    order_flow: list[OrderStep]
    payment_methods: list[PaymentMethod]
    agent_questions: list[AgentQuestion]
    modules: list[str] = Field(default_factory=list)
    demo: DemoShop


@lru_cache
def all_templates(directory: Path = TEMPLATES_DIR) -> dict[str, BusinessTemplate]:
    templates: dict[str, BusinessTemplate] = {}
    for path in sorted(directory.glob("*.json")):
        template = BusinessTemplate.model_validate(json.loads(path.read_text(encoding="utf-8")))
        if template.id != path.stem:
            raise ValueError(f"{path.name}: id must match the file name")
        templates[template.id] = template
    return templates


def get_template(template_id: str) -> BusinessTemplate:
    try:
        return all_templates()[template_id]
    except KeyError as exc:
        raise KeyError(f"unknown business template: {template_id}") from exc
