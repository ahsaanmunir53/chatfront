"""The owner's assistant: add a product from a photo and a voice note (features 1 and 4),
change prices and shop policies by voice or text (feature 86), tell the books about bills,
expenses and udhaar (48, 49) and ask how the shop is doing (75). Spark Open reads; the owner
confirms; code applies. Each staff member gets only what their role allows (42).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.client import AiClient
from chatfront.ai.types import Part
from chatfront.audit.service import Actor, ActorType
from chatfront.books import reports
from chatfront.books.chat import (
    KIND_MONEY,
    BillReading,
    apply_money,
    bill_op,
    is_bill,
    money_card_text,
    problem_text,
    read_bill,
    resolve_money,
)
from chatfront.catalog.drafts import (
    EMPTY_DRAFT,
    MoneyLine,
    OwnerMessage,
    ProductDraft,
    complete_draft,
    fields_text,
    price_from_words,
    read_owner_message,
    read_product,
    to_input,
)
from chatfront.catalog.models import ProductStatus
from chatfront.catalog.service import (
    CatalogItem,
    apply_changes,
    create_product,
    load_items,
)
from chatfront.channels.conversation import Turn
from chatfront.channels.types import Kind, Outbound, OutText
from chatfront.core.countries import get_pack, shop_money
from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.core.settings import Settings
from chatfront.jobs.queue import enqueue
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.media.storage import Storage
from chatfront.owner import actions
from chatfront.owner.cards import change_list, confirm_card, product_summary
from chatfront.owner.changes import resolve_changes, typed_price_change
from chatfront.owner.models import ActionStatus, PendingAction
from chatfront.owner.rights import Can, staff_rights
from chatfront.templates import get_template

# A tap on a button another module put in the owner's chat (giving a chat back to the agent,
# approving a payment): the replies, or None when the button is not that module's.
ButtonHandler = Callable[[Turn], Awaitable[list[Outbound] | None]]

KIND_PRODUCT = "product"
KIND_UPDATE = "update"
_RIGHT = {KIND_PRODUCT: Can.CATALOG, KIND_UPDATE: Can.CATALOG, KIND_MONEY: Can.BOOKS_WRITE}
TRANSLATE_JOB = "catalog.translate"
_POLICY_FIELDS = ("delivery", "hours", "payment", "returns", "policies")


def refuse(turn: Turn) -> list[Outbound]:
    return [OutText(t("owner.not_allowed", turn.lang))]


def prompt_context(tenant: Tenant) -> dict[str, str]:
    template = get_template(tenant.template)
    pack = get_pack(tenant.country)
    return {
        "shop_name": tenant.name,
        "business_type": template.name.en.lower(),
        "country": pack.name,
        "currency": pack.currency,
        "fields": fields_text(template),
    }


class OwnerAssistant:
    def __init__(
        self,
        db: Database,
        ai: AiClient,
        storage: Storage,
        settings: Settings,
        buttons: Sequence[ButtonHandler] = (),
    ) -> None:
        self.db = db
        self.ai = ai
        self.storage = storage
        self.settings = settings
        self.buttons = tuple(buttons)

    async def respond(self, turn: Turn) -> list[Outbound]:
        button = (turn.message.payload or {}).get("button_id")
        if turn.staff_id is not None and button:
            for handler in self.buttons:
                replies = await handler(turn)
                if replies is not None:
                    return replies
        allowed = await staff_rights(self.db, turn.tenant.id, turn.staff_id)
        choice = actions.parse_button(button)
        if choice is not None:
            return await self._answer(turn, allowed, *choice)
        async with self.db.tenant(turn.tenant.id) as session:
            action = await actions.current_action(session, turn.conversation.id)
        words, kind = turn.message.body or "", turn.message.kind
        if kind == Kind.IMAGE and is_bill(words):
            return await self._bill(turn, None) if Can.BOOKS_WRITE in allowed else refuse(turn)
        if kind == Kind.IMAGE:
            return await self._product(turn, None, None) if Can.CATALOG in allowed else refuse(turn)
        if action is not None and action.kind == KIND_PRODUCT:
            return await self._product(turn, action, None)
        waiting_bill = (
            action is not None
            and action.kind == KIND_MONEY
            and action.status == ActionStatus.COLLECTING
        )
        if waiting_bill and price_from_words(words, turn.lang) is not None:
            assert action is not None
            return await self._bill(turn, action)
        return await self._owner_message(turn, allowed)

    # -- a new product --------------------------------------------------------------------

    async def _photos(self, tenant_id: uuid.UUID, media_ids: Sequence[str]) -> list[Part]:
        parts: list[Part] = []
        async with self.db.tenant(tenant_id) as session:
            stored = [await session.get(Media, uuid.UUID(m)) for m in media_ids]
        for media in stored:
            if media is not None:
                parts.append(await media_service.model_part(self.storage, media))
        return parts

    async def _product(
        self, turn: Turn, action: PendingAction | None, read: ProductDraft | None
    ) -> list[Outbound]:
        """Read (or re-read with the owner's correction) a product draft, then preview it or ask
        for what is missing."""
        message, tenant, lang = turn.message, turn.tenant, turn.lang
        payload = dict(action.payload) if action else {}
        media_ids: list[str] = list(payload.get("media_ids", []))
        if message.kind == Kind.IMAGE and message.media_id is not None:
            media_ids = [str(message.media_id)]
        words = message.body or ""
        draft = read
        if draft is None:
            previous = (
                ProductDraft.model_validate(payload["draft"]) if payload.get("draft") else None
            )
            media = await self._photos(tenant.id, media_ids)
            if message.kind == Kind.AUDIO and turn.media is not None:
                media.append(turn.media)
            try:
                draft = await read_product(
                    self.ai,
                    tenant_id=tenant.id,
                    context=prompt_context(tenant),
                    media=media,
                    words=words,
                    previous=previous,
                    lang=lang,
                )
            except AppError:  # no model, or no usable answer: code reads the typed words
                draft = previous or EMPTY_DRAFT
        draft = complete_draft(draft, words, lang)
        image_id = uuid.UUID(media_ids[0]) if media_ids else None
        product = to_input(
            draft,
            get_template(tenant.template),
            get_pack(tenant.country).minor_digits,
            source="photo_voice" if image_id else "chat",
            image_media_id=image_id,
        )
        new_payload = {"media_ids": media_ids, "draft": draft.model_dump()}
        status = ActionStatus.PENDING if product else ActionStatus.COLLECTING
        async with self.db.tenant(tenant.id) as session:
            if action is None or message.kind == Kind.IMAGE:
                saved = await actions.open_action(
                    session,
                    tenant_id=tenant.id,
                    conversation_id=turn.conversation.id,
                    staff_id=turn.staff_id,
                    kind=KIND_PRODUCT,
                    payload=new_payload,
                    status=status,
                )
            else:
                found = await session.get(PendingAction, action.id)
                assert found is not None
                saved = found
                actions.update(saved, new_payload, status)
        if product is not None:
            text = t("owner.preview_new", lang, summary=product_summary(tenant, product, lang))
            return [confirm_card(saved, text, "owner.confirm", lang)]
        return [OutText(t(self._ask_for(draft, message.kind, words), lang))]

    @staticmethod
    def _ask_for(draft: ProductDraft, kind: str, words: str) -> str:
        if kind == Kind.IMAGE and not words:
            return "owner.photo_received"
        if kind == Kind.AUDIO and not words:
            return "owner.voice_unclear"
        return "owner.need_name" if "name" in draft.missing else "owner.need_price"

    # -- prices and policies --------------------------------------------------------------

    async def _read(self, turn: Turn, items: Sequence[CatalogItem]) -> OwnerMessage:
        tenant, message = turn.tenant, turn.message
        context = prompt_context(tenant) | {
            "products": "; ".join(
                f"{i.product.code} {i.product.name} {shop_money(tenant, i.product.price, 'en')}"
                for i in items[:60]
            )
            or "none yet",
            "policies": "; ".join(
                f"{key}: {tenant.profile[key]}" for key in _POLICY_FIELDS if tenant.profile.get(key)
            )
            or "none yet",
        }
        media = [turn.media] if message.kind == Kind.AUDIO and turn.media is not None else []
        try:
            return await read_owner_message(
                self.ai,
                tenant_id=tenant.id,
                context=context,
                media=media,
                words=message.body or "",
                lang=turn.lang,
            )
        except AppError:
            return OwnerMessage(intent="other", product=None, changes=[], note="")

    async def _owner_message(self, turn: Turn, allowed: frozenset[Can]) -> list[Outbound]:
        tenant, lang, message = turn.tenant, turn.lang, turn.message
        words = message.body or ""
        async with self.db.tenant(tenant.id) as session:
            items = await load_items(
                session, statuses=(ProductStatus.ACTIVE, ProductStatus.ARCHIVED)
            )
        read = await self._read(turn, items)
        if read.intent == "report" and read.report is not None:
            return await self._report(turn, read.report, allowed)
        if read.intent == "money" and read.money:
            return await self._money(turn, read.money, allowed)
        if read.intent == "new_product" and read.product is not None:
            return (
                await self._product(turn, None, read.product)
                if Can.CATALOG in allowed
                else refuse(turn)
            )
        digits = get_pack(tenant.country).minor_digits
        ops, problems = resolve_changes(read.changes, items, digits, tenant)
        if not ops and not problems:
            ops = typed_price_change(words, items, lang, digits)
        if (ops or problems) and Can.CATALOG not in allowed:
            return refuse(turn)
        problem_lines = [t(key, lang, **values) for key, values in problems]
        if ops:
            action = await self._open(turn, KIND_UPDATE, {"ops": ops})
            text = "\n".join(
                [
                    t("owner.changes_preview", lang, lines=change_list(tenant, ops, lang)),
                    *problem_lines,
                ]
            )
            return [confirm_card(action, text, "owner.apply", lang)]
        if problem_lines:
            return [OutText("\n".join(problem_lines))]
        if message.kind == Kind.AUDIO and not words:
            return [OutText(t("owner.voice_unclear", lang))]
        return [OutText(t("owner.help", lang))]

    async def _open(self, turn: Turn, kind: str, payload: dict[str, Any]) -> PendingAction:
        async with self.db.tenant(turn.tenant.id) as session:
            return await actions.open_action(
                session,
                tenant_id=turn.tenant.id,
                conversation_id=turn.conversation.id,
                staff_id=turn.staff_id,
                kind=kind,
                payload=payload,
                status=ActionStatus.PENDING,
            )

    # -- the books: expenses, udhaar, bills and reports (features 48, 49, 75) ---------------

    async def _report(self, turn: Turn, report: str, allowed: frozenset[Can]) -> list[Outbound]:
        if reports.NEEDS[report] not in allowed:
            return refuse(turn)
        async with self.db.tenant(turn.tenant.id) as session:
            text = await reports.report_text(session, turn.tenant, report, turn.lang)
        return [OutText(text)]

    async def _money(
        self, turn: Turn, lines: Sequence[MoneyLine], allowed: frozenset[Can]
    ) -> list[Outbound]:
        if Can.BOOKS_WRITE not in allowed:
            return refuse(turn)
        tenant, lang = turn.tenant, turn.lang
        digits = get_pack(tenant.country).minor_digits
        async with self.db.tenant(tenant.id) as session:
            ops, problems = await resolve_money(session, lines, digits)
        problem_lines = [problem_text(tenant, problem, lang) for problem in problems]
        if not ops:
            return [OutText("\n".join(problem_lines))]
        source = "voice" if turn.message.kind == Kind.AUDIO else "chat"
        action = await self._open(turn, KIND_MONEY, {"ops": ops, "source": source})
        text = "\n".join([money_card_text(tenant, ops, lang), *problem_lines])
        return [confirm_card(action, text, "books.save", lang)]

    async def _bill(self, turn: Turn, action: PendingAction | None) -> list[Outbound]:
        """A bill photo, or the amount the owner typed for the bill before it."""
        tenant, lang, message = turn.tenant, turn.lang, turn.message
        words = message.body or ""
        payload = dict(action.payload) if action else {}
        reading = BillReading.model_validate(payload["reading"]) if payload.get("reading") else None
        media_id = payload.get("media_id")
        if action is None and message.media_id is not None:
            media_id = str(message.media_id)
            try:
                reading = await read_bill(
                    self.ai,
                    tenant_id=tenant.id,
                    context=prompt_context(tenant),
                    media=await self._photos(tenant.id, [media_id]),
                    words=words,
                    lang=lang,
                )
            except AppError:  # no model: the owner types the amount
                reading = None
        op = bill_op(reading, words, get_pack(tenant.country).minor_digits, lang)
        ready = isinstance(op, dict)
        new_payload = {
            "media_id": media_id,
            "reading": reading.model_dump() if reading else None,
            "source": "bill",
            "ops": [op] if ready else [],
        }
        status = ActionStatus.PENDING if ready else ActionStatus.COLLECTING
        async with self.db.tenant(tenant.id) as session:
            if action is None:
                saved = await actions.open_action(
                    session,
                    tenant_id=tenant.id,
                    conversation_id=turn.conversation.id,
                    staff_id=turn.staff_id,
                    kind=KIND_MONEY,
                    payload=new_payload,
                    status=status,
                )
            else:
                found = await session.get(PendingAction, action.id)
                assert found is not None
                saved = found
                actions.update(saved, new_payload, status)
        if isinstance(op, dict):
            return [confirm_card(saved, money_card_text(tenant, [op], lang), "books.save", lang)]
        return [OutText(problem_text(tenant, op, lang))]

    # -- Confirm and Cancel ---------------------------------------------------------------

    async def _answer(
        self, turn: Turn, allowed: frozenset[Can], action_id: uuid.UUID, answer: str
    ) -> list[Outbound]:
        tenant, lang = turn.tenant, turn.lang
        actor = Actor(ActorType.STAFF, str(turn.staff_id) if turn.staff_id else None)
        try:
            async with self.db.tenant(tenant.id) as session:
                action = await actions.find_for_button(session, turn.conversation.id, action_id)
                if action is None or not actions.is_live(action):
                    return [OutText(t("owner.expired", lang))]
                if answer != "yes":
                    actions.close(action, ActionStatus.CANCELLED)
                    return [OutText(t("owner.cancelled", lang))]
                if _RIGHT[action.kind] not in allowed:
                    return refuse(turn)
                reply = await self._apply(session, tenant, action, actor, lang)
                actions.close(action, ActionStatus.CONFIRMED)
        except AppError:  # something changed since the card: nothing was saved
            return [OutText(t("owner.not_applied", lang))]
        return [OutText(reply)]

    async def _apply(
        self, session: AsyncSession, tenant: Tenant, action: PendingAction, actor: Actor, lang: str
    ) -> str:
        if action.kind == KIND_MONEY:
            media_id = action.payload.get("media_id")
            count = await apply_money(
                session,
                tenant,
                action.payload["ops"],
                actor,
                source=action.payload["source"],
                media_id=uuid.UUID(media_id) if media_id else None,
            )
            return t("books.saved", lang, count=count)
        if action.kind == KIND_UPDATE:
            stored = await session.get(Tenant, tenant.id)
            assert stored is not None
            count = await apply_changes(session, stored, action.payload["ops"], actor)
            return t("owner.changes_applied", lang, count=count)
        media_ids = action.payload.get("media_ids")
        product = to_input(
            ProductDraft.model_validate(action.payload["draft"]),
            get_template(tenant.template),
            get_pack(tenant.country).minor_digits,
            source="photo_voice" if media_ids else "chat",
            image_media_id=uuid.UUID(media_ids[0]) if media_ids else None,
        )
        assert product is not None  # only complete drafts are ever pending
        item = await create_product(session, tenant.id, product, actor)
        await enqueue(
            session,
            TRANSLATE_JOB,
            {"product_id": str(item.product.id)},
            tenant_id=tenant.id,
            dedupe_key=f"translate:{item.product.id}",
        )
        return t(
            "owner.product_added",
            lang,
            name=item.product.name,
            code=item.product.code,
            link=f"{self.settings.web_url}/s/{tenant.slug}",
        )
