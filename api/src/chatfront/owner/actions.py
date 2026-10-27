"""Changes waiting for the owner. Nothing in the catalog or the shop changes until Confirm.

An action is collected (a photo waiting for its price), shown with Confirm and Cancel
(pending), then confirmed, cancelled or expired. Button ids carry the action id, so a tap on
an old card can never confirm a newer change.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.core.clock import utcnow
from chatfront.owner.models import ActionStatus, PendingAction

ACTION_TTL = timedelta(hours=24)
_OPEN = (ActionStatus.COLLECTING, ActionStatus.PENDING)


async def open_action(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    staff_id: uuid.UUID | None,
    kind: str,
    payload: dict[str, Any],
    status: ActionStatus = ActionStatus.COLLECTING,
) -> PendingAction:
    """Start a new action; any other open one in this conversation is cancelled first."""
    for stale in await session.scalars(
        select(PendingAction).where(
            PendingAction.conversation_id == conversation_id, PendingAction.status.in_(_OPEN)
        )
    ):
        stale.status, stale.decided_at = ActionStatus.CANCELLED, utcnow()
    action = PendingAction(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        staff_id=staff_id,
        kind=kind,
        payload=payload,
        status=status,
        expires_at=utcnow() + ACTION_TTL,
    )
    session.add(action)
    await session.flush()
    return action


async def current_action(
    session: AsyncSession, conversation_id: uuid.UUID, kind: str | None = None
) -> PendingAction | None:
    """The conversation's open action, if it has not expired."""
    query = select(PendingAction).where(
        PendingAction.conversation_id == conversation_id,
        PendingAction.status.in_(_OPEN),
        PendingAction.expires_at > utcnow(),
    )
    if kind is not None:
        query = query.where(PendingAction.kind == kind)
    return await session.scalar(query.order_by(PendingAction.created_at.desc()).limit(1))


async def last_confirmed(
    session: AsyncSession, conversation_id: uuid.UUID, kind: str
) -> PendingAction | None:
    return await session.scalar(
        select(PendingAction)
        .where(
            PendingAction.conversation_id == conversation_id,
            PendingAction.kind == kind,
            PendingAction.status == ActionStatus.CONFIRMED,
        )
        .order_by(PendingAction.created_at.desc())
        .limit(1)
    )


def update(action: PendingAction, payload: dict[str, Any], status: ActionStatus) -> None:
    action.payload = payload  # a new dict, so the JSON column is saved
    action.status = status


def close(action: PendingAction, status: ActionStatus) -> None:
    action.status, action.decided_at = status, utcnow()


def button_id(action: PendingAction, answer: str) -> str:
    return f"pa:{action.id}:{answer}"


def parse_button(value: str | None) -> tuple[uuid.UUID, str] | None:
    """(action id, answer) from a button id such as 'pa:<uuid>:yes'."""
    if not value or not value.startswith("pa:"):
        return None
    try:
        _, raw_id, answer = value.split(":", 2)
        return uuid.UUID(raw_id), answer
    except ValueError:
        return None


async def find_for_button(
    session: AsyncSession, conversation_id: uuid.UUID, action_id: uuid.UUID
) -> PendingAction | None:
    action = await session.get(PendingAction, action_id)
    if action is None or action.conversation_id != conversation_id:
        return None
    return action


def is_live(action: PendingAction) -> bool:
    return action.status == ActionStatus.PENDING and action.expires_at > utcnow()
