"""Business-type templates: a new kind of business is a JSON file, not new code."""

from __future__ import annotations

import json
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

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
    cost: int | None = None  # synthetic cost of one piece, so the demo shows batch profit
    floor: int | None = None  # the lowest price the owner takes when a customer bargains
    category: str
    description: str
    attributes: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)  # other names customers use: chawal, چاول
    weight_g: int | None = None  # shipping weight of one piece; the template default otherwise
    variants: list[DemoVariant] = Field(default_factory=lambda: [DemoVariant()])


class DemoBundle(BaseModel):
    """Products sold together for less (feature 13); money in major units."""

    model_config = ConfigDict(extra="forbid")

    name: str
    products: list[str] = Field(min_length=2, max_length=4)  # demo product names
    off: int = Field(gt=0)


class DemoCoupon(BaseModel):
    """A discount code (feature 19); money in major units, `days` from when the shop is made."""

    model_config = ConfigDict(extra="forbid")

    code: str
    percent: int | None = Field(default=None, ge=1, le=90)
    amount: int | None = Field(default=None, gt=0)
    min_subtotal: int = Field(default=0, ge=0)
    days: int | None = Field(default=None, ge=1)
    show_on_page: bool = False


class DemoLoyalty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    earn_per: int = Field(gt=0)  # spent for one point, major units
    point_value: int = Field(gt=0)  # a point's worth, major units
    max_share: int = Field(default=20, ge=1, le=100)


class DemoOffers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundles: list[DemoBundle] = Field(default_factory=list)
    coupons: list[DemoCoupon] = Field(default_factory=list)
    loyalty: DemoLoyalty | None = None


class DemoShop(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    tier: Tier
    city: str
    tagline: Text3
    products: list[DemoProduct] = Field(default_factory=list)
    offers: DemoOffers = Field(default_factory=DemoOffers)


class BusinessTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: Text3
    product_fields: list[ProductField]
    order_flow: list[OrderStep]
    payment_methods: list[PaymentMethod]
    agent_questions: list[AgentQuestion]
    modules: list[str] = Field(default_factory=list)
    # Shipping weight of one piece when a product does not give its own (delivery charge, 94).
    default_weight_g: int = Field(default=500, ge=0)
    # Days after delivery a customer may ask to swap or return (feature 92); 0 for none.
    return_days: int = Field(default=7, ge=0, le=60)
    # The usual size charts for this kind of shop (feature 5): a starting point the owner edits
    # in the console, read as catalog.sizes.SizeChart.
    size_charts: list[dict[str, Any]] = Field(default_factory=list)
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
