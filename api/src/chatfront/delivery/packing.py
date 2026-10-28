"""Packing check (feature 102): the packer photographs the packed order before sealing it.

Spark Open is shown the photo, the order's lines and, where the shop has them, the products'
own photos, and says how many pieces of each line it can see and what else is there. Code
decides: every piece there and nothing else is a match, and the packer gets Mark packed; a
piece clearly missing, one too many or something not on the order is a mismatch, and the
packer fixes it and sends a new photo. When the model cannot tell for some line, the owner
decides: a packer's unclear check goes to the owner with Approve (mark it packed) and Reject
(pack it again); the owner's own gets Mark packed. Nothing is packed until someone taps it.
Without a model nothing can be read, and the packer ticks the slip by hand.

The packer sends the photo in their chat with the order's code and a word like "pack", or
from the order in the console.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Part, Role, TextPart
from chatfront.audit.service import Actor, ActorType, record
from chatfront.catalog.drafts import Strict, prompt_context
from chatfront.catalog.models import Product
from chatfront.channels.language import staff_language
from chatfront.channels.notify import tell_owner
from chatfront.channels.types import Button, Outbound, OutButtons, OutText
from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Staff, Tenant
from chatfront.delivery import shipping
from chatfront.delivery.labels import named
from chatfront.delivery.models import PackingCheck, Verdict
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.media.storage import Storage
from chatfront.orders import service as orders
from chatfront.orders.models import Order, OrderItem
from chatfront.orders.states import OrderStatus
from chatfront.owner.rights import Can, staff_rights
from chatfront.shield import approvals
from chatfront.shield.approvals import ApprovalKind
from chatfront.shield.models import Approval

if TYPE_CHECKING:
    from chatfront.channels.conversation import Turn
    from chatfront.services import Services

KIND = "packing"  # the approval kind
BUTTON = "pk:"  # Mark packed, with the order's code
CHECKABLE = (OrderStatus.PAID, OrderStatus.CONFIRMED, OrderStatus.PACKED)
READY = (OrderStatus.PAID, OrderStatus.CONFIRMED)
REFERENCES = 4  # product photos shown with the packed one
MAX_EXTRA = 5
_CODE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{6}(?![A-Za-z0-9])")
# A photo whose words say it is a packed order, in the languages shops write in.
PACK_WORDS = frozenset(
    (
        *("pack", "packed", "packing", "parcel", "packet"),
        *("پیک", "پیکنگ", "پیکڈ", "پارسل", "پيڪ", "پيڪنگ"),
        *("पैक", "पैकिंग", "পার্সেল", "প্যাক", "প্যাকিং", "تغليف", "طرد"),
    )
)


class SeenLine(Strict):
    line: str = Field(description="The order line's name, such as L1")
    seen: int | None
    sure: bool


class PackingReading(Strict):
    lines: list[SeenLine]
    extra: list[str]
    clear: bool
    note: str


def is_check(words: str) -> bool:
    return any(token.strip(".,:;!?-").casefold() in PACK_WORDS for token in words.split())


def codes_in(words: str) -> list[str]:
    return [found.upper() for found in _CODE.findall(words)]


@dataclass(frozen=True, slots=True)
class Outcome:
    verdict: Verdict
    lines: list[dict[str, Any]]
    extra: list[str]


def compare(items: Sequence[OrderItem], reading: PackingReading) -> Outcome:
    """Code's verdict on what the model saw."""
    seen: dict[str, SeenLine] = {}
    for line in reading.lines:
        seen.setdefault(line.line.strip().upper(), line)
    lines = []
    for index, item in enumerate(items, start=1):
        found = seen.get(f"L{index}")
        lines.append(
            {
                "name": named(item),
                "quantity": item.quantity,
                "seen": found.seen if found else None,
                "sure": bool(found and found.sure and found.seen is not None),
            }
        )
    extra = [" ".join(e.split())[:80] for e in reading.extra if e.strip()][:MAX_EXTRA]
    wrong = any(line["sure"] and line["seen"] != line["quantity"] for line in lines)
    if wrong or extra:
        return Outcome(Verdict.MISMATCH, lines, extra)
    if not reading.clear or not all(line["sure"] for line in lines):
        return Outcome(Verdict.UNCLEAR, lines, extra)
    return Outcome(Verdict.MATCH, lines, extra)


async def read_packing(
    ai: AiClient,
    tenant: Tenant,
    items: Sequence[OrderItem],
    photo: Part,
    references: Sequence[tuple[str, Part]],
) -> PackingReading:
    prompt = load_prompt("packing_check")
    listed = "\n".join(
        f"L{index}: {item.quantity} x {named(item)}" for index, item in enumerate(items, start=1)
    )
    parts: list[Part] = [TextPart("The packed order:"), photo]
    for name, part in references:
        parts += [TextPart(f"What {name} looks like:"), part]
    request = ChatRequest(
        role=Role.VISION,
        messages=[
            ChatMessage("system", prompt.render(**prompt_context(tenant), lines=listed)),
            ChatMessage("user", parts),
        ],
        tenant_id=tenant.id,
        prompt=prompt.ref,
    )
    reading, _ = await ai.complete_json(request, PackingReading)
    return reading


async def _references(
    session: AsyncSession, storage: Storage, items: Sequence[OrderItem]
) -> list[tuple[str, Part]]:
    """The products' own photos, named with their order lines."""
    found: list[tuple[str, Part]] = []
    shown: set[uuid.UUID] = set()
    for index, item in enumerate(items, start=1):
        product = await session.get(Product, item.product_id)
        media_id = product.image_media_id if product else None
        if media_id is None or media_id in shown or len(found) >= REFERENCES:
            continue
        media = await session.get(Media, media_id)
        if media is not None:
            shown.add(media_id)
            found.append((f"L{index}", await media_service.model_part(storage, media)))
    return found


async def find_order(session: AsyncSession, words: str) -> Order | str:
    """The order a photo is for: the code in the words, else the only order waiting to be
    packed. A string key says why none could be picked."""
    codes = codes_in(words)
    if codes:
        found = await session.scalar(
            select(Order).where(Order.code.in_(codes), Order.status.in_(CHECKABLE)).limit(1)
        )
        return found if found is not None else "packing.not_ready"
    waiting = list(await session.scalars(select(Order).where(Order.status.in_(READY)).limit(2)))
    return waiting[0] if len(waiting) == 1 else "packing.which_order"


@dataclass(frozen=True, slots=True)
class Checked:
    check: PackingCheck
    code: str
    asked_owner: bool
    ready: bool  # still waiting to be packed


async def check(
    db: Database,
    ai: AiClient,
    storage: Storage,
    tenant: Tenant,
    order_id: uuid.UUID,
    media_id: uuid.UUID,
    staff_id: uuid.UUID,
) -> Checked | None:
    """Check one photo against the order; None when no model could read it."""
    async with db.tenant(tenant.id) as session:
        order = await session.get(Order, order_id)
        media = await session.get(Media, media_id)
        if order is None or media is None:
            raise AppError("not_found", "Order or photo not found", status_code=404)
        items = await orders.lines(session, order)
        photo = await media_service.model_part(storage, media)
        references = await _references(session, storage, items)
    try:
        reading = await read_packing(ai, tenant, items, photo, references)
    except AppError:
        return None
    outcome = compare(items, reading)
    decides = Can.TRUST in await staff_rights(db, tenant.id, staff_id)
    async with db.tenant(tenant.id) as session:
        stored = PackingCheck(
            tenant_id=tenant.id,
            order_id=order_id,
            media_id=media_id,
            verdict=outcome.verdict,
            lines=outcome.lines,
            extra=outcome.extra,
            staff_id=staff_id,
        )
        session.add(stored)
        await session.flush()
        await record(
            session,
            tenant.id,
            Actor(ActorType.STAFF, str(staff_id)),
            "packing.checked",
            entity=("order", str(order_id)),
            data={"verdict": outcome.verdict, "check": str(stored.id)},
        )
        ask = outcome.verdict == Verdict.UNCLEAR and not decides
        if ask:
            payload = {"check_id": str(stored.id), "code": order.code, "staff_id": str(staff_id)}
            await approvals.ask_once(session, tenant.id, KIND, payload, ("code",))
    return Checked(stored, order.code, ask, order.status in READY)


# -- what people read --------------------------------------------------------------------------


def findings(check: PackingCheck, lang: str) -> list[str]:
    """What is wrong or could not be seen, one line each."""
    out = []
    for line in check.lines:
        name, wanted, seen = line["name"], line["quantity"], line["seen"]
        if not line["sure"]:
            out.append(t("packing.not_seen", lang, name=name))
        elif seen < wanted:
            out.append(t("packing.missing", lang, name=name, count=wanted - seen))
        elif seen > wanted:
            out.append(t("packing.too_many", lang, name=name, count=seen - wanted))
    out += [t("packing.extra", lang, item=item) for item in check.extra]
    return out


def result_text(checked: Checked, lang: str) -> str:
    check, code = checked.check, checked.code
    if check.verdict == Verdict.MATCH:
        pieces = sum(int(line["quantity"]) for line in check.lines)
        return t("packing.match", lang, code=code, count=pieces)
    head = "packing.mismatch" if check.verdict == Verdict.MISMATCH else "packing.unclear"
    tail = "packing.fix" if check.verdict == Verdict.MISMATCH else "packing.retake"
    lines = [t(head, lang, code=code), *(f"- {f}" for f in findings(check, lang))]
    lines.append(t(tail, lang))
    if checked.asked_owner:
        lines.append(t("packing.asked_owner", lang))
    return "\n".join(lines)


def reply(checked: Checked, lang: str) -> Outbound:
    """The result in the chat, with Mark packed when the one who checked may pack it now."""
    text = result_text(checked, lang)
    verdict = checked.check.verdict
    packable = checked.ready and (
        verdict == Verdict.MATCH or (verdict == Verdict.UNCLEAR and not checked.asked_owner)
    )
    if not packable:
        return OutText(text)
    return OutButtons(text, (Button(f"{BUTTON}{checked.code}", t("console.move.packed", lang)),))


# -- from the chat -----------------------------------------------------------------------------


async def from_chat(db: Database, ai: AiClient, storage: Storage, turn: Turn) -> list[Outbound]:
    tenant, message, lang = turn.tenant, turn.message, turn.lang
    assert message.media_id is not None
    assert turn.staff_id is not None
    async with db.tenant(tenant.id) as session:
        found = await find_order(session, message.body or "")
    if isinstance(found, str):
        return [OutText(t(found, lang))]
    checked = await check(db, ai, storage, tenant, found.id, message.media_id, turn.staff_id)
    if checked is None:
        return [OutText(t("packing.no_model", lang))]
    return [reply(checked, lang)]


async def packing_button(services: Services, turn: Turn) -> list[Outbound] | None:
    """Mark packed, tapped under a check's result."""
    button = (turn.message.payload or {}).get("button_id") or ""
    if turn.staff_id is None or not button.startswith(BUTTON):
        return None
    if Can.SHIP not in await staff_rights(services.db, turn.tenant.id, turn.staff_id):
        return [OutText(t("owner.not_allowed", turn.lang))]
    code = button.removeprefix(BUTTON)
    try:
        await shipping.move(services, turn.tenant.id, code, OrderStatus.PACKED, turn.staff_id)
    except AppError:
        return [OutText(t("packing.cannot_mark", turn.lang, code=code))]
    return [OutText(t("packing.marked", turn.lang, code=code))]


# -- the owner's decision on an unclear check --------------------------------------------------


def describe(tenant: Tenant, approval: Approval, lang: str) -> str:
    del tenant
    return t("packing.approval", lang, code=approval.payload["code"])


async def carry_out(services: Services, tenant: Tenant, approval: Approval, approved: bool) -> None:
    """Approve marks it packed; either way, the packer hears the owner's answer."""
    code = str(approval.payload["code"])
    if approved and approval.decided_by is not None:
        with suppress(AppError):  # packed already, from the console or another phone
            await shipping.move(services, tenant.id, code, OrderStatus.PACKED, approval.decided_by)
    async with services.db.tenant(tenant.id) as session:
        packer = await session.get(Staff, uuid.UUID(str(approval.payload["staff_id"])))
    if packer is None:
        return
    lang = staff_language(packer, tenant)
    key = "packing.owner_approved" if approved else "packing.owner_rejected"
    await tell_owner(services, tenant.id, packer, OutText(t(key, lang, code=code)), lang)


APPROVAL = ApprovalKind(Can.TRUST, describe, carry_out)
