"""The shop's courier account, booking a parcel with it, and following the parcels it carries
(feature 31), with what it paid for them (feature 32).

The shop's courier token is sealed at rest. Booking takes the order's details as they are,
asks the courier for a tracking number and marks the order dispatched with it, so the customer
gets the tracking page as with a parcel booked by hand. Every hour code asks the courier where
each of its dispatched parcels is: delivered or returned moves the order on, so the customer
hears; anything else leaves it. A delivered parcel's money is asked for until the courier says
it has paid it over.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from chatfront.audit.service import Actor, ActorType
from chatfront.core.countries import get_pack
from chatfront.core.errors import AppError, NotFound
from chatfront.core.models import Tenant
from chatfront.core.secretbox import seal, unseal
from chatfront.delivery import shipping
from chatfront.delivery.couriers import POSTEX, Courier, PostEx, Stage, parcel_of
from chatfront.delivery.models import CodSettlement, CourierAccount, Shipment
from chatfront.jobs.queue import JobContext
from chatfront.orders import service as orders
from chatfront.orders.models import Order
from chatfront.orders.states import OrderStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from chatfront.services import Services

log = logging.getLogger(__name__)

TRACK_JOB = "delivery.track_couriers"
EVERY = timedelta(hours=1)
COURIERS = {POSTEX: "PostEx"}  # the name the shipment shows
BOOKABLE = (OrderStatus.PACKED,)  # a packed parcel is the one a rider can take


def courier_of(services: Services, account: CourierAccount) -> Courier:
    token = unseal(account.token_sealed, services.keys.secret_key)
    if account.courier == POSTEX:
        return PostEx(services.http, token, account.pickup_code or "")
    raise AppError("unknown_courier", f"No adapter for {account.courier}")


async def connect(
    session: AsyncSession,
    services: Services,
    tenant_id: uuid.UUID,
    courier: str,
    token: str,
    pickup: str,
) -> CourierAccount:
    if courier not in COURIERS:
        raise AppError("unknown_courier", f"No adapter for {courier}")
    account = await session.scalar(select(CourierAccount).where(CourierAccount.courier == courier))
    if account is None:
        account = CourierAccount(tenant_id=tenant_id, courier=courier, token_sealed="")
        session.add(account)
    account.token_sealed = seal(token.strip(), services.keys.secret_key)
    account.pickup_code = pickup.strip()[:40] or None
    await session.flush()
    return account


async def book(
    services: Services, tenant_id: uuid.UUID, code: str, courier: str, staff_id: uuid.UUID
) -> shipping.Moved:
    async with services.db.tenant(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        account = await session.scalar(
            select(CourierAccount).where(CourierAccount.courier == courier)
        )
        if tenant is None or account is None:
            raise NotFound("courier account")
        order = await shipping.order_by_code(session, code)
        if order.status not in BOOKABLE:
            raise AppError(
                "not_bookable", "Book a parcel once the order is packed", status_code=409
            )
        lines = await orders.lines(session, order)
        detail = ", ".join(f"{line.name} x{line.quantity}" for line in lines)
        parcel = parcel_of(
            order,
            shipping.to_collect(order),
            sum(line.quantity for line in lines),
            detail,
            get_pack(tenant.country).minor_digits,
        )
        adapter = courier_of(services, account)
    tracking = await adapter.book(parcel)
    try:
        return await shipping.move(
            services,
            tenant_id,
            code,
            OrderStatus.DISPATCHED,
            staff_id,
            courier=COURIERS[courier],
            tracking=tracking,
        )
    except AppError as exc:  # the order moved meanwhile: the booking must not be lost
        log.warning("booked %s as %s but could not move it: %s", code, tracking, exc.code)
        raise AppError(
            "booked_not_moved",
            f"{COURIERS[courier]} booked it as {tracking}; mark it dispatched with that number",
            status_code=409,
        ) from exc


async def track_job(ctx: JobContext) -> None:
    """Hourly: each courier's dispatched parcels, and its delivered parcels' money."""
    services: Services = ctx.services
    async with services.db.system() as session:
        accounts = list(await session.scalars(select(CourierAccount)))
    for account in accounts:
        try:
            await _follow(services, account)
        except AppError as exc:
            log.info("following %s parcels failed: %s", account.courier, exc.code)


async def _follow(services: Services, account: CourierAccount) -> None:
    adapter = courier_of(services, account)
    async with services.db.tenant(account.tenant_id) as session:
        tenant = await session.get(Tenant, account.tenant_id)
        rows = (
            await session.execute(
                select(Order.code, Order.status, Shipment.tracking_number, Shipment.id)
                .join(Shipment, Shipment.order_id == Order.id)
                .where(
                    Shipment.courier == COURIERS[account.courier],
                    Shipment.tracking_number.is_not(None),
                    Order.status.in_((OrderStatus.DISPATCHED, OrderStatus.DELIVERED)),
                )
            )
        ).all()
        settled = set(
            await session.scalars(
                select(CodSettlement.order_id).where(CodSettlement.source == account.courier)
            )
        )
    assert tenant is not None
    digits = get_pack(tenant.country).minor_digits
    for code, status, tracking, _ in rows:
        if tracking is None:
            continue
        try:
            if status == OrderStatus.DISPATCHED:
                await _moved(services, adapter, tenant, code, tracking, account.courier)
            else:
                await _paid(services, adapter, tenant, code, tracking, digits, settled)
        except AppError as exc:  # one parcel's trouble never stops the others
            log.info("following %s failed: %s", code, exc.code)


async def _moved(
    services: Services, adapter: Courier, tenant: Tenant, code: str, tracking: str, courier: str
) -> None:
    where = await adapter.track(tracking)
    if where.stage not in (Stage.DELIVERED, Stage.RETURNED):
        return
    wanted = OrderStatus.DELIVERED if where.stage is Stage.DELIVERED else OrderStatus.RETURNED
    await shipping.move_as(services, tenant.id, code, wanted, Actor(ActorType.SYSTEM, courier))


async def _paid(
    services: Services,
    adapter: Courier,
    tenant: Tenant,
    code: str,
    tracking: str,
    digits: int,
    settled: set[uuid.UUID],
) -> None:
    paid = await adapter.payment(tracking, digits)
    if not paid.settled:
        return
    async with services.db.tenant(tenant.id) as session:
        order = await shipping.order_by_code(session, code)
        if order.id in settled:
            return
        shipment = await shipping.shipment_for(session, order)
        short = (
            max(shipment.cod_amount - (shipment.courier_charge or 0) - (paid.amount or 0), 0)
            if paid.amount is not None
            else 0
        )
        session.add(
            CodSettlement(
                tenant_id=tenant.id,
                order_id=order.id,
                row_key=f"{adapter.name}:{tracking}",
                source=adapter.name,
                collected=shipment.cod_amount or None,
                charges=shipment.courier_charge,
                paid=paid.amount,
                short=short,
            )
        )
