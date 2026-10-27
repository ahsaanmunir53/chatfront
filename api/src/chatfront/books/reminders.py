"""Udhaar reminders that grow firmer (feature 49): a polite one a week after the balance
opened, a firmer one at two weeks, a firm one at three, then no more; the owner sees the rest.

Each reminder follows the shop's autonomy level (feature 104): sent on its own, put to the
owner first, or never sent. A reminder goes only to a customer whose chat is open to the shop;
the 24-hour rule means anyone else needs an approved template, so the console lists them.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.audit.service import SYSTEM, record
from chatfront.books.models import UdhaarAccount
from chatfront.channels.language import reply_language
from chatfront.channels.models import Conversation
from chatfront.channels.notify import send_to
from chatfront.channels.types import OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.jobs.queue import JobContext
from chatfront.owner.rights import Can
from chatfront.shield import approvals
from chatfront.shield.approvals import ApprovalKind
from chatfront.shield.autonomy import Action, Level, level
from chatfront.shield.models import Approval

if TYPE_CHECKING:
    from chatfront.services import Services

REMINDERS_JOB = "books.udhaar_reminders"
REMINDER = "udhaar_reminder"
EVERY = timedelta(hours=6)
STAGES = (timedelta(days=7), timedelta(days=14), timedelta(days=21))  # after the balance opened
GAP = timedelta(days=6)  # never two reminders closer than this


def due_stage(account: UdhaarAccount) -> int | None:
    """The reminder this account is due for now (0, 1 or 2), if any."""
    if account.balance <= 0 or account.owed_since is None or account.stage >= len(STAGES):
        return None
    now = utcnow()
    if now - account.owed_since < STAGES[account.stage]:
        return None
    if account.reminded_at is not None and now - account.reminded_at < GAP:
        return None
    return account.stage


async def _chat(session: AsyncSession, account: UdhaarAccount) -> Conversation | None:
    if account.customer_id is None:
        return None
    return await session.scalar(
        select(Conversation)
        .where(
            Conversation.customer_id == account.customer_id,
            Conversation.last_inbound_at.is_not(None),
        )
        .order_by(Conversation.last_inbound_at.desc())
        .limit(1)
    )


def reminder_text(tenant: Tenant, account: UdhaarAccount, stage: int, lang: str) -> str:
    return t(
        f"udhaar.reminder_{stage + 1}",
        lang,
        name=account.name,
        amount=shop_money(tenant, account.balance, lang),
        shop=tenant.name,
    )


async def remind(services: Services, tenant_id: uuid.UUID, account_id: uuid.UUID) -> bool:
    """Send the due reminder now; True when it went out and the stage moved on."""
    async with services.db.tenant(tenant_id) as session:
        tenant = await session.get(Tenant, tenant_id)
        account = await session.get(UdhaarAccount, account_id)
        if tenant is None or account is None:
            return False
        stage = due_stage(account)
        chat = await _chat(session, account) if stage is not None else None
        if stage is None or chat is None:
            return False
        lang = reply_language(chat, tenant)
        text = reminder_text(tenant, account, stage, lang)
    if not await send_to(services, tenant_id, chat.id, OutText(text), lang):
        return False
    async with services.db.tenant(tenant_id) as session:
        stored = await session.get(UdhaarAccount, account_id)
        assert stored is not None
        stored.stage, stored.reminded_at = stage + 1, utcnow()
        await record(
            session,
            tenant_id,
            SYSTEM,
            "udhaar.reminded",
            entity=("udhaar_account", str(account_id)),
            data={"stage": stage + 1, "balance": stored.balance},
        )
    return True


async def _ask_owner(
    services: Services, tenant: Tenant, account: UdhaarAccount, stage: int
) -> None:
    question = {
        "account_id": str(account.id),
        "stage": stage,
        "name": account.name,
        "balance": account.balance,
    }
    async with services.db.tenant(tenant.id) as session:
        await approvals.ask_once(session, tenant.id, REMINDER, question, ("account_id",))


async def reminders_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    async with services.db.system() as session:
        rows = (
            await session.execute(
                select(UdhaarAccount, Tenant)
                .join(Tenant, Tenant.id == UdhaarAccount.tenant_id)
                .where(UdhaarAccount.balance > 0, UdhaarAccount.customer_id.is_not(None))
                .limit(500)
            )
        ).all()
    for account, tenant in rows:
        stage = due_stage(account)
        mode = level(tenant, Action.UDHAAR_REMINDERS)
        if stage is None or mode == Level.NEVER:
            continue
        if mode == Level.AUTO:
            await remind(services, tenant.id, account.id)
        else:
            await _ask_owner(services, tenant, account, stage)


def describe(tenant: Tenant, approval: Approval, lang: str) -> str:
    payload = approval.payload
    return t(
        "approval.udhaar_reminder",
        lang,
        name=payload.get("name", ""),
        amount=shop_money(tenant, int(payload.get("balance", 0)), lang),
        stage=int(payload["stage"]) + 1,
    )


async def carry_out(services: Services, tenant: Tenant, approval: Approval, approved: bool) -> None:
    account_id = uuid.UUID(approval.payload["account_id"])
    if approved:
        await remind(services, tenant.id, account_id)
        return
    async with services.db.tenant(tenant.id) as session:  # skipped: on to the next stage
        account = await session.get(UdhaarAccount, account_id)
        if account is not None and account.stage == approval.payload["stage"]:
            account.stage, account.reminded_at = account.stage + 1, utcnow()


KIND = ApprovalKind(Can.BOOKS_WRITE, describe, carry_out)
