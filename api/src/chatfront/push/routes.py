"""Turning alerts on and off on a device, from the owner console (feature 115)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import delete

from chatfront.core.errors import AppError
from chatfront.owner.routes import ServicesDep, StaffDep
from chatfront.push.alerts import queue_alert, vapid_of
from chatfront.push.models import PushSubscription

router = APIRouter(prefix="/api/owner/push")


class KeyOut(BaseModel):
    public_key: str | None  # None: alerts are not set up on this server


class AlertsOut(BaseModel):
    on: bool  # alerts on this device


class Keys(BaseModel):
    p256dh: str = Field(min_length=80, max_length=200)
    auth: str = Field(min_length=16, max_length=60)


class SubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=1000, pattern=r"^https://")
    keys: Keys


class EndpointIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=1000)


class TestIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    lang: str = Field(default="en", max_length=16)


@router.get("/key")
async def push_key(services: ServicesDep, session: StaffDep) -> KeyOut:
    del session  # signed-in staff only
    vapid = vapid_of(services.settings)
    return KeyOut(public_key=vapid.public_key if vapid else None)


@router.post("")
async def subscribe(body: SubscriptionIn, services: ServicesDep, session: StaffDep) -> AlertsOut:
    if vapid_of(services.settings) is None:
        raise AppError("push_off", "Alerts are not set up on this server", status_code=409)
    async with services.db.system() as db:
        # A phone that was someone else's (or another shop's) is now this person's.
        await db.execute(delete(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
        db.add(
            PushSubscription(
                tenant_id=session.tenant_id,
                staff_id=session.staff_id,
                endpoint=body.endpoint,
                p256dh=body.keys.p256dh,
                auth=body.keys.auth,
            )
        )
    return AlertsOut(on=True)


@router.post("/off")
async def unsubscribe(body: EndpointIn, services: ServicesDep, session: StaffDep) -> AlertsOut:
    async with services.db.tenant(session.tenant_id) as db:
        await db.execute(
            delete(PushSubscription).where(
                PushSubscription.endpoint == body.endpoint,
                PushSubscription.staff_id == session.staff_id,
            )
        )
    return AlertsOut(on=False)


@router.post("/test")
async def test_alert(body: TestIn, services: ServicesDep, session: StaffDep) -> dict[str, bool]:
    """An alert to the person's own devices, so they can see that it works."""
    queued = await queue_alert(services, session.tenant_id, session.staff_id, body.text, body.lang)
    return {"queued": queued}
