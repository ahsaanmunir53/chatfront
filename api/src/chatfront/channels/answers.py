"""Approved answers (feature 91): what the owner told a customer in a handed-over chat may
answer the next customer who asks the same, but only once the owner says so.

When the owner gives a chat back to the assistant after replying in it, code pairs the
customer's question (their last messages before the first reply) with the owner's reply, and
asks the owner, with Approve and Reject, whether the assistant may give that answer again. An
approved answer goes into the shop details the order agent reads; nothing unapproved does. The
owner can remove an answer in the console at any time.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from chatfront.audit.service import Actor, ActorType, record
from chatfront.channels.models import Direction, Handover, Message, SavedAnswer
from chatfront.channels.types import Kind
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.owner.rights import Can
from chatfront.shield import approvals
from chatfront.shield.approvals import ApprovalKind
from chatfront.shield.models import Approval

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from chatfront.services import Services

ANSWER = "answer"  # the approval kind
SHOWN = 20  # the newest approved answers the agent reads
QUESTION_CHARS, ANSWER_CHARS = 300, 600
QUESTION_MESSAGES = 2


def _text(messages: list[Message], limit: int) -> str:
    return " ".join(" ".join((m.body or "").split()) for m in messages if m.body)[:limit]


async def propose(services: Services, tenant_id: uuid.UUID, handover_id: uuid.UUID) -> bool:
    """Ask the owner whether a handed-over chat's answer may be given again; False when no
    one replied, or there was no question in words."""
    async with services.db.tenant(tenant_id) as session:
        handover = await session.get(Handover, handover_id)
        if handover is None:
            return False
        messages = list(
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == handover.conversation_id)
                .order_by(Message.created_at)
            )
        )
        replies = [
            m
            for m in messages
            if m.direction == Direction.OUT
            and (m.payload or {}).get("by") == "staff"
            and m.created_at >= handover.created_at
        ]
        if not replies:
            return False
        asked = [
            m
            for m in messages
            if m.direction == Direction.IN
            and m.kind == Kind.TEXT
            and m.created_at < replies[0].created_at
        ][-QUESTION_MESSAGES:]
        question, answer = _text(asked, QUESTION_CHARS), _text(replies, ANSWER_CHARS)
        if not question or not answer:
            return False
        await approvals.ask(
            session,
            tenant_id,
            ANSWER,
            {"handover_id": str(handover_id), "question": question, "answer": answer},
        )
    return True


def describe(tenant: Tenant, approval: Approval, lang: str) -> str:
    del tenant
    return t(
        "approval.answer",
        lang,
        question=approval.payload["question"],
        answer=approval.payload["answer"],
    )


async def carry_out(services: Services, tenant: Tenant, approval: Approval, approved: bool) -> None:
    if not approved:
        return
    asked = approval.payload
    async with services.db.tenant(tenant.id) as session:
        saved = SavedAnswer(
            tenant_id=tenant.id,
            question=asked["question"],
            answer=asked["answer"],
            handover_id=uuid.UUID(asked["handover_id"]),
            approved_by=approval.decided_by,
        )
        session.add(saved)
        await session.flush()
        await record(
            session,
            tenant.id,
            Actor(ActorType.STAFF, str(approval.decided_by)),
            "answer.saved",
            entity=("saved_answer", str(saved.id)),
        )


KIND = ApprovalKind(Can.SETTINGS, describe, carry_out)


async def approved(session: AsyncSession) -> list[SavedAnswer]:
    return list(
        await session.scalars(
            select(SavedAnswer).order_by(SavedAnswer.created_at.desc()).limit(SHOWN)
        )
    )


def detail_lines(answers: list[SavedAnswer]) -> list[str]:
    """For the shop details: each approved question and answer, as the owner approved it."""
    if not answers:
        return []
    lines = ["Answers the owner approved for questions like these:"]
    lines += [f"- Q: {a.question}\n  A: {a.answer}" for a in answers]
    return lines
