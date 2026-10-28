"""Offers on a cart, kept in step with it (features 13 and 19).

Every time a cart changes, `price` works out its bundles, its coupon and its points again from
the database and the rules in offers.pricing, so the total on a card is always code's. When the
order is placed its coupon use and spent points are written down; a cancelled order gives them
back; a delivered order earns points and a returned one gives them up.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.books.ledger import today
from chatfront.catalog.models import Product, ProductStatus
from chatfront.core.models import Tenant
from chatfront.offers.pricing import (
    BundleRule,
    CouponProblem,
    CouponRule,
    Line,
    Loyalty,
    Offer,
    OfferKind,
    bundles_off,
    catalog_part,
    coupon_off,
    earned,
    points_off,
)
from chatfront.orders.models import (
    Bundle,
    Coupon,
    CouponUse,
    LoyaltyEntry,
    Order,
    OrderItem,
    PointsReason,
)
from chatfront.orders.states import OrderStatus

LOYALTY = "loyalty"  # the shop's profile key: {"on", "earn_per", "point_value", "max_share"}
DEFAULT_MAX_SHARE = 20
EXCHANGE = "exchange"  # a swap's order (feature 92) is already priced at what was paid
CO_BOUGHT_DAYS = 120
SUGGESTIONS = 2


def loyalty_of(tenant: Tenant) -> Loyalty | None:
    """The shop's points, or None when it gives none."""
    raw: dict[str, Any] = (tenant.profile or {}).get(LOYALTY) or {}
    if not raw.get("on"):
        return None
    loyalty = Loyalty(
        earn_per=int(raw.get("earn_per") or 0),
        point_value=int(raw.get("point_value") or 0),
        max_share=int(raw.get("max_share") or DEFAULT_MAX_SHARE),
    )
    return loyalty if loyalty.earn_per > 0 and loyalty.point_value > 0 else None


def as_line(item: OrderItem) -> Line:
    return Line(item.product_id, item.quantity, item.line_total, item.list_price is not None)


async def bundle_rules(session: AsyncSession) -> list[BundleRule]:
    bundles = await session.scalars(select(Bundle).where(Bundle.active.is_(True)))
    return [
        BundleRule(b.name, tuple(uuid.UUID(str(p)) for p in b.product_ids), b.off) for b in bundles
    ]


def coupon_rule(coupon: Coupon) -> CouponRule:
    return CouponRule(
        code=coupon.code,
        percent=coupon.percent,
        amount=coupon.amount,
        min_subtotal=coupon.min_subtotal,
        starts_on=coupon.starts_on,
        ends_on=coupon.ends_on,
        max_uses=coupon.max_uses,
        once_per_customer=coupon.once_per_customer,
        active=coupon.active,
    )


def clean_code(code: str) -> str:
    return "".join(code.split()).upper()


async def find_coupon(session: AsyncSession, code: str) -> Coupon | None:
    found: Coupon | None = await session.scalar(
        select(Coupon).where(Coupon.code == clean_code(code))
    )
    return found


async def _coupon(
    session: AsyncSession,
    tenant: Tenant,
    code: str,
    eligible: int,
    customer_id: uuid.UUID | None,
    order_id: uuid.UUID | None,
) -> Offer | CouponProblem:
    coupon = await find_coupon(session, code)
    if coupon is None:
        return CouponProblem.UNKNOWN
    others = CouponUse.order_id != order_id if order_id else true()
    uses = await session.scalar(
        select(func.count(CouponUse.id)).where(CouponUse.coupon_id == coupon.id, others)
    )
    used_before = customer_id is not None and (
        await session.scalar(
            select(CouponUse.id)
            .where(CouponUse.coupon_id == coupon.id, CouponUse.customer_id == customer_id, others)
            .limit(1)
        )
        is not None
    )
    return coupon_off(
        coupon_rule(coupon), eligible, today(tenant), uses=int(uses or 0), used_before=used_before
    )


async def balance(session: AsyncSession, customer_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(LoyaltyEntry.points), 0)).where(
            LoyaltyEntry.customer_id == customer_id
        )
    )
    return int(total or 0)


@dataclass
class Priced:
    offers: list[Offer] = field(default_factory=list)
    coupon_problem: CouponProblem | None = None

    @property
    def off(self) -> int:
        return sum(offer.amount for offer in self.offers)


async def price(
    session: AsyncSession,
    tenant: Tenant,
    lines: Sequence[Line],
    *,
    coupon: str | None = None,
    points: int = 0,
    customer_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
) -> Priced:
    """The offers on a set of lines: bundles, then the coupon, then points."""
    priced = Priced(bundles_off(lines, await bundle_rules(session)))
    if coupon:
        eligible = catalog_part(lines) - priced.off
        found = await _coupon(session, tenant, coupon, eligible, customer_id, order_id)
        if isinstance(found, Offer):
            priced.offers.append(found)
        else:
            priced.coupon_problem = found
    loyalty = loyalty_of(tenant)
    if points and loyalty is not None and customer_id is not None:
        base = sum(line.line_total for line in lines) - priced.off
        spent = points_off(points, await balance(session, customer_id), loyalty, base)
        if spent is not None:
            priced.offers.append(spent)
    return priced


async def apply(
    session: AsyncSession, tenant: Tenant, order: Order, items: Sequence[OrderItem]
) -> CouponProblem | None:
    """Price an order's offers onto it; returns why its coupon no longer applies, if so."""
    if order.source == EXCHANGE:
        order.offers, order.offer_off = [], 0
        return None
    priced = await price(
        session,
        tenant,
        [as_line(item) for item in items],
        coupon=order.coupon_code,
        points=order.points_used,
        customer_id=order.customer_id,
        order_id=order.id,
    )
    order.offers = [offer.as_dict() for offer in priced.offers]
    order.offer_off = priced.off
    return priced.coupon_problem


def _spent(order: Order, kind: OfferKind) -> list[dict[str, Any]]:
    return [offer for offer in order.offers or [] if offer.get("kind") == kind]


async def _points(session: AsyncSession, order: Order, points: int, reason: PointsReason) -> None:
    if points:
        session.add(
            LoyaltyEntry(
                tenant_id=order.tenant_id,
                customer_id=order.customer_id,
                order_id=order.id,
                points=points,
                reason=reason,
            )
        )


async def _entries(session: AsyncSession, order: Order, reason: PointsReason) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(LoyaltyEntry.points), 0)).where(
            LoyaltyEntry.order_id == order.id, LoyaltyEntry.reason == reason
        )
    )
    return int(total or 0)


async def on_move(session: AsyncSession, tenant: Tenant, order: Order, wanted: OrderStatus) -> None:
    """Write down what an order's offers did, as it moves."""
    if wanted == OrderStatus.PLACED:
        for used in _spent(order, OfferKind.COUPON):
            coupon = await find_coupon(session, str(used["label"]))
            if coupon is not None:
                session.add(
                    CouponUse(
                        tenant_id=order.tenant_id,
                        coupon_id=coupon.id,
                        order_id=order.id,
                        customer_id=order.customer_id,
                        amount=int(used["amount"]),
                    )
                )
        spent = sum(int(offer["points"]) for offer in _spent(order, OfferKind.POINTS))
        await _points(session, order, -spent, PointsReason.REDEEMED)
    elif wanted in (OrderStatus.CANCELLED, OrderStatus.RETURNED):
        use = await session.scalar(select(CouponUse).where(CouponUse.order_id == order.id))
        if use is not None and wanted == OrderStatus.CANCELLED:
            await session.delete(use)
        redeemed = -await _entries(session, order, PointsReason.REDEEMED)
        await _points(session, order, redeemed, PointsReason.CANCELLED)
        if wanted == OrderStatus.RETURNED:
            earned_here = await _entries(session, order, PointsReason.EARNED)
            await _points(session, order, -earned_here, PointsReason.RETURNED)
    elif wanted == OrderStatus.DELIVERED and (loyalty := loyalty_of(tenant)) is not None:
        goods = order.subtotal - order.offer_off
        await _points(session, order, earned(goods, loyalty), PointsReason.EARNED)
    await session.flush()


# -- what goes with a product (feature 13) ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoesWith:
    product: Product
    bundle: Bundle | None  # the set that makes it cheaper, if any


async def goes_with(
    session: AsyncSession, product_id: uuid.UUID, in_cart: set[uuid.UUID]
) -> list[GoesWith]:
    """Up to two products to suggest with one just added: the other pieces of its bundles
    first, then what customers most often bought with it. Only products on sale."""
    found: list[GoesWith] = []
    seen = {product_id, *in_cart}
    for bundle in await session.scalars(select(Bundle).where(Bundle.active.is_(True))):
        ids = [uuid.UUID(str(p)) for p in bundle.product_ids]
        if product_id not in ids:
            continue
        for other in ids:
            product = await session.get(Product, other) if other not in seen else None
            if product is not None and product.status == ProductStatus.ACTIVE:
                found.append(GoesWith(product, bundle))
                seen.add(other)
    together = select(OrderItem.order_id).where(OrderItem.product_id == product_id)
    rows = await session.execute(
        select(OrderItem.product_id, func.count(func.distinct(OrderItem.order_id)).label("n"))
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            OrderItem.order_id.in_(together),
            OrderItem.product_id.not_in(seen),
            Order.status.not_in((OrderStatus.CART, OrderStatus.CANCELLED)),
            Order.created_at > func.now() - func.make_interval(0, 0, 0, CO_BOUGHT_DAYS),
        )
        .group_by(OrderItem.product_id)
        .order_by(func.count(func.distinct(OrderItem.order_id)).desc())
        .limit(SUGGESTIONS * 2)
    )
    for other, _ in rows.all():
        product = await session.get(Product, other)
        if product is not None and product.status == ProductStatus.ACTIVE:
            found.append(GoesWith(product, None))
    return found[:SUGGESTIONS]
