"""The shield in the console (features 38 and 104): what Chatfront may do on its own, the
decisions waiting for the owner, and the shop's customers with their verified-buyer badge."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from chatfront.audit.service import Actor, ActorType, record
from chatfront.books.models import UdhaarAccount
from chatfront.catalog.stock import SHOP_LIMIT, shop_limit
from chatfront.channels import answers
from chatfront.channels.models import SavedAnswer
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Customer, Staff, Tenant
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus
from chatfront.owner.rights import Can, rights
from chatfront.owner.routes import ServicesDep, StaffDep, needs
from chatfront.owner.sessions import OwnerSession
from chatfront.shield import approvals, autonomy, buyers, why

router = APIRouter(prefix="/api/owner")
SettingsDep = Annotated[OwnerSession, needs(Can.SETTINGS)]
OrdersDep = Annotated[OwnerSession, needs(Can.ORDERS)]
TrustDep = Annotated[OwnerSession, needs(Can.TRUST)]

CUSTOMER_LIMIT = 100
MAX_VERIFY_AFTER = 50
MAX_STOCK_LIMIT = 100_000


class ShopSettings(BaseModel):
    autonomy: dict[str, str]
    choices: dict[str, list[str]]  # the levels each action can take
    verified_after: int
    low_stock_limit: int
    ask_discount_over: int


class SettingsChange(BaseModel):
    autonomy: dict[str, str] = Field(default_factory=dict, max_length=10)
    verified_after: int | None = Field(default=None, ge=1, le=MAX_VERIFY_AFTER)
    low_stock_limit: int | None = Field(default=None, ge=0, le=MAX_STOCK_LIMIT)


class Waiting(BaseModel):
    id: uuid.UUID
    kind: str
    text: str
    created_at: datetime


class Decision(BaseModel):
    approve: bool


class CustomerOut(BaseModel):
    id: uuid.UUID
    name: str | None
    verified: bool
    verified_by: str | None
    orders: int
    delivered: int
    returned: int
    owes: int


class Verified(BaseModel):
    verified: bool


class DecisionOut(BaseModel):
    topic: str
    outcome: str
    rule: str
    values: dict[str, Any]
    evidence: list[dict[str, Any]]


class Why(BaseModel):
    code: str
    status: str
    decisions: list[DecisionOut]
    history: list[dict[str, Any]]


def _settings(tenant: Tenant) -> ShopSettings:
    return ShopSettings(
        autonomy=autonomy.levels(tenant),
        choices={
            str(a): [str(level) for level in rule.levels] for a, rule in autonomy.RULES.items()
        },
        verified_after=buyers.verify_after(tenant),
        low_stock_limit=shop_limit(tenant),
        ask_discount_over=autonomy.ASK_DISCOUNT_OVER,
    )


@router.get("/settings")
async def shop_settings(session: SettingsDep, services: ServicesDep) -> ShopSettings:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        return _settings(tenant)


@router.put("/settings")
async def change_settings(
    body: SettingsChange, session: SettingsDep, services: ServicesDep
) -> ShopSettings:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        assert tenant is not None
        before = _settings(tenant)
        for action, wanted in body.autonomy.items():
            tenant.profile = autonomy.set_level(tenant, action, wanted)
        profile = dict(tenant.profile or {})
        if body.verified_after is not None:
            profile["verified_after"] = body.verified_after
        if body.low_stock_limit is not None:
            profile[SHOP_LIMIT] = body.low_stock_limit
        tenant.profile = profile
        after = _settings(tenant)
        await record(
            db,
            tenant.id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "settings.changed",
            entity=("tenant", str(tenant.id)),
            data={
                key: {"from": getattr(before, key), "to": getattr(after, key)}
                for key in ("autonomy", "verified_after", "low_stock_limit")
                if getattr(before, key) != getattr(after, key)
            },
        )
        return after


# -- decisions waiting for the owner -----------------------------------------------------


@router.get("/approvals")
async def waiting(session: StaffDep, services: ServicesDep) -> list[Waiting]:
    """The questions this person may answer, newest first."""
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        staff = await db.get(Staff, session.staff_id)
        assert tenant is not None
        assert staff is not None
        allowed = rights(staff.role)
        kinds = services.registries.approvals
        mine = [a for a in await approvals.pending(db) if kinds[a.kind].right in allowed]
        described = await approvals.describe_for(services, tenant, mine, staff)
    return [Waiting.model_validate(item) for item in described]


@router.post("/approvals/{approval_id}")
async def decide(
    approval_id: uuid.UUID, body: Decision, session: StaffDep, services: ServicesDep
) -> dict[str, str]:
    outcome = await approvals.answer(
        services, session.tenant_id, approval_id, session.staff_id, approve=body.approve
    )
    if outcome == approvals.Answered.NOT_ALLOWED:
        raise AppError("forbidden", "Your role in this shop does not allow this", status_code=403)
    return {"outcome": str(outcome)}


# -- answers the owner approved for reuse (feature 91) -----------------------------------


class AnswerOut(BaseModel):
    id: uuid.UUID
    question: str
    answer: str
    created_at: datetime


@router.get("/answers")
async def saved_answers(session: SettingsDep, services: ServicesDep) -> list[AnswerOut]:
    async with services.db.tenant(session.tenant_id) as db:
        return [
            AnswerOut(id=a.id, question=a.question, answer=a.answer, created_at=a.created_at)
            for a in await answers.approved(db)
        ]


@router.delete("/answers/{answer_id}")
async def remove_answer(
    answer_id: uuid.UUID, session: SettingsDep, services: ServicesDep
) -> dict[str, bool]:
    """The assistant stops giving this answer at once."""
    async with services.db.tenant(session.tenant_id) as db:
        found = await db.get(SavedAnswer, answer_id)
        if found is None:
            raise NotFound("answer")
        await db.delete(found)
        await record(
            db,
            session.tenant_id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "answer.removed",
            entity=("saved_answer", str(answer_id)),
            data={"question": found.question},
        )
    return {"removed": True}


# -- customers and the verified-buyer badge (feature 38) ---------------------------------


@router.get("/customers")
async def customers(session: OrdersDep, services: ServicesDep) -> list[CustomerOut]:
    async with services.db.tenant(session.tenant_id) as db:
        rows = (
            await db.execute(
                select(
                    Customer,
                    func.count(Order.id),
                    func.count(case((Order.status == OrderStatus.DELIVERED, 1))),
                    func.count(case((Order.status == OrderStatus.RETURNED, 1))),
                    func.coalesce(func.max(UdhaarAccount.balance), 0),
                )
                .join(Order, Order.customer_id == Customer.id)
                .where(Order.placed_at.is_not(None))
                .outerjoin(UdhaarAccount, UdhaarAccount.customer_id == Customer.id)
                .where(Customer.staff_id.is_(None))
                .group_by(Customer.id)
                .order_by(func.max(Order.created_at).desc())
                .limit(CUSTOMER_LIMIT)
            )
        ).all()
    return [
        CustomerOut(
            id=customer.id,
            name=customer.display_name,
            verified=customer.verified_buyer,
            verified_by=customer.verified_by,
            orders=orders,
            delivered=delivered,
            returned=returned,
            owes=owes,
        )
        for customer, orders, delivered, returned, owes in rows
    ]


@router.put("/customers/{customer_id}/verified")
async def set_verified(
    customer_id: uuid.UUID, body: Verified, session: TrustDep, services: ServicesDep
) -> dict[str, bool]:
    """The owner's own choice; the automatic rule never overrides it."""
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        customer = await db.get(Customer, customer_id)
        if tenant is None or customer is None or customer.staff_id is not None:
            raise NotFound("customer")
        await buyers.set_by_owner(
            db, tenant, customer, body.verified, Actor(ActorType.STAFF, str(session.staff_id))
        )
    return {"verified": body.verified}


# -- the why panel (feature 107) -----------------------------------------------------------


@router.get("/orders/{code}/why")
async def why_order(code: str, session: OrdersDep, services: ServicesDep) -> Why:
    async with services.db.tenant(session.tenant_id) as db:
        tenant = await db.get(Tenant, session.tenant_id)
        order = await db.scalar(select(Order).where(Order.code == code.upper()[:12]))
        if tenant is None or order is None:
            raise NotFound("order")
        decisions = await why.explain(db, tenant, order)
        return Why(
            code=order.code,
            status=order.status,
            decisions=[DecisionOut(**asdict(decision)) for decision in decisions],
            history=await why.history(db, order),
        )
