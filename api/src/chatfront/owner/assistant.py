"""The owner's assistant: add a product from a photo and a voice note (features 1 and 4), and
change prices and shop policies by voice or text (feature 86). Spark Open reads; the owner
confirms; code applies.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from chatfront.ai.client import AiClient
from chatfront.ai.types import Part
from chatfront.audit.service import Actor, ActorType
from chatfront.catalog.drafts import (
    EMPTY_DRAFT,
    Change,
    OwnerMessage,
    ProductDraft,
    complete_draft,
    fields_text,
    name_from_words,
    read_owner_message,
    read_product,
    to_input,
    to_minor,
)
from chatfront.catalog.models import ProductStatus
from chatfront.catalog.service import (
    CatalogItem,
    apply_changes,
    create_product,
    find_products,
    load_items,
    single_match,
)
from chatfront.channels.conversation import Turn
from chatfront.channels.handover import parse_handover_button
from chatfront.channels.types import Kind, Outbound, OutText
from chatfront.core.countries import get_pack, shop_money
from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.core.numbers import find_numbers
from chatfront.core.settings import Settings
from chatfront.jobs.queue import enqueue
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.media.storage import Storage
from chatfront.orders.delivery import city_name, rates_for
from chatfront.owner import actions
from chatfront.owner.cards import change_list, confirm_card, product_summary
from chatfront.owner.models import ActionStatus, PendingAction
from chatfront.templates import get_template

# Gives a handed-over chat back to the agent: (tenant, handover, staff) -> resumed?
Resume = Callable[[uuid.UUID, uuid.UUID, uuid.UUID], Awaitable[bool]]

KIND_PRODUCT = "product"
KIND_UPDATE = "update"
TRANSLATE_JOB = "catalog.translate"
_POLICY_FIELDS = ("delivery", "hours", "payment", "returns", "policies")


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


Problem = tuple[str, dict[str, Any]]


def _delivery_op(change: Change, tenant: Tenant, digits: int) -> dict[str, Any] | Problem:
    """A change to the delivery charge (feature 94), with what it was before."""
    rates = rates_for(tenant)
    amount = to_minor(change.value_number, digits) if change.value_number is not None else None
    if change.kind == "free_delivery_over":
        new = amount if change.value_number else None
        return {"op": "free_delivery_over", "old": rates.free_over, "new": new}
    if amount is None:
        return ("owner.need_amount", {"name": t("owner.policy.delivery", "en")})
    if change.city:
        city = city_name(change.city, get_pack(tenant.country))
        return {"op": "delivery_fee", "city": city, "old": rates.cities.get(city), "new": amount}
    return {"op": "delivery_fee", "city": None, "old": rates.default, "new": amount}


def resolve_changes(
    changes: Sequence[Change],
    items: Sequence[CatalogItem],
    digits: int,
    tenant: Tenant | None = None,
) -> tuple[list[dict[str, Any]], list[Problem]]:
    """Turn what the owner asked for into exact operations on exact products, or problems to
    tell the owner (not found, which one, no amount)."""
    ops: list[dict[str, Any]] = []
    problems: list[Problem] = []
    for change in changes:
        if change.kind in ("delivery_fee", "free_delivery_over"):
            if tenant is not None:
                resolved = _delivery_op(change, tenant, digits)
                if isinstance(resolved, dict):
                    ops.append(resolved)
                else:
                    problems.append(resolved)
            continue
        if change.kind == "policy":
            if change.policy_key and change.value_text and change.value_text.strip():
                ops.append(
                    {
                        "op": "policy",
                        "key": change.policy_key,
                        "new": change.value_text.strip()[:300],
                    }
                )
            continue
        if not change.product:
            continue
        matches = find_products(items, change.product)
        item = single_match(matches)
        if item is None:
            if matches:
                options = ", ".join(
                    f"{m.item.product.code} ({m.item.product.name})" for m in matches[:3]
                )
                problems.append(("owner.which", {"options": options}))
            else:
                problems.append(("owner.not_found", {"name": change.product}))
            continue
        product = item.product
        if change.kind in ("price", "floor_price"):
            new = to_minor(change.value_number, digits) if change.value_number is not None else None
            if new is None:
                problems.append(("owner.need_amount", {"name": product.name}))
                continue
            if change.kind == "floor_price" and new > product.price:
                problems.append(("owner.floor_above_price", {"name": product.name}))
                continue
            old = product.price if change.kind == "price" else product.floor_price
            ops.append(
                {
                    "op": change.kind,
                    "product_id": str(product.id),
                    "name": product.name,
                    "old": old,
                    "new": new,
                }
            )
        else:
            ops.append({"op": change.kind, "product_id": str(product.id), "name": product.name})
    return ops, problems


def typed_price_change(
    words: str, items: Sequence[CatalogItem], lang: str, digits: int
) -> list[dict[str, Any]]:
    """Code's own reading of "lawn 3-piece 2500" as a price change, when the model gave none."""
    numbers = [n for n in find_numbers(words, lang) if n >= 10]
    if len(numbers) != 1:
        return []
    item = single_match(find_products(items, name_from_words(words, numbers[0], lang)))
    new = to_minor(numbers[0], digits)
    if item is None or new is None or new == item.product.price:
        return []
    product = item.product
    return [
        {
            "op": "price",
            "product_id": str(product.id),
            "name": product.name,
            "old": product.price,
            "new": new,
        }
    ]


class OwnerAssistant:
    def __init__(
        self,
        db: Database,
        ai: AiClient,
        storage: Storage,
        settings: Settings,
        resume: Resume | None = None,
    ) -> None:
        self.db = db
        self.ai = ai
        self.storage = storage
        self.settings = settings
        self.resume = resume

    async def respond(self, turn: Turn) -> list[Outbound]:
        handover_id = parse_handover_button((turn.message.payload or {}).get("button_id"))
        if handover_id is not None and self.resume is not None and turn.staff_id is not None:
            await self.resume(turn.tenant.id, handover_id, turn.staff_id)
            return [OutText(t("handover.resumed", turn.lang))]
        choice = actions.parse_button((turn.message.payload or {}).get("button_id"))
        if choice is not None:
            return await self._answer(turn, *choice)
        async with self.db.tenant(turn.tenant.id) as session:
            action = await actions.current_action(session, turn.conversation.id)
        if turn.message.kind == Kind.IMAGE:
            return await self._product(turn, None, None)
        if action is not None and action.kind == KIND_PRODUCT:
            return await self._product(turn, action, None)
        return await self._owner_message(turn)

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

    async def _owner_message(self, turn: Turn) -> list[Outbound]:
        tenant, lang, message = turn.tenant, turn.lang, turn.message
        words = message.body or ""
        async with self.db.tenant(tenant.id) as session:
            items = await load_items(
                session, statuses=(ProductStatus.ACTIVE, ProductStatus.ARCHIVED)
            )
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
            read = await read_owner_message(
                self.ai, tenant_id=tenant.id, context=context, media=media, words=words, lang=lang
            )
        except AppError:
            read = OwnerMessage(intent="other", product=None, changes=[], note="")
        if read.intent == "new_product" and read.product is not None:
            return await self._product(turn, None, read.product)
        digits = get_pack(tenant.country).minor_digits
        ops, problems = resolve_changes(read.changes, items, digits, tenant)
        if not ops and not problems:
            ops = typed_price_change(words, items, lang, digits)
        problem_lines = [t(key, lang, **values) for key, values in problems]
        if ops:
            async with self.db.tenant(tenant.id) as session:
                action = await actions.open_action(
                    session,
                    tenant_id=tenant.id,
                    conversation_id=turn.conversation.id,
                    staff_id=turn.staff_id,
                    kind=KIND_UPDATE,
                    payload={"ops": ops},
                    status=ActionStatus.PENDING,
                )
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

    # -- Confirm and Cancel ---------------------------------------------------------------

    async def _answer(self, turn: Turn, action_id: uuid.UUID, answer: str) -> list[Outbound]:
        tenant, lang = turn.tenant, turn.lang
        actor = Actor(ActorType.STAFF, str(turn.staff_id) if turn.staff_id else None)
        async with self.db.tenant(tenant.id) as session:
            action = await actions.find_for_button(session, turn.conversation.id, action_id)
            if action is None or not actions.is_live(action):
                return [OutText(t("owner.expired", lang))]
            if answer != "yes":
                actions.close(action, ActionStatus.CANCELLED)
                return [OutText(t("owner.cancelled", lang))]
            if action.kind == KIND_PRODUCT:
                product = to_input(
                    ProductDraft.model_validate(action.payload["draft"]),
                    get_template(tenant.template),
                    get_pack(tenant.country).minor_digits,
                    source="photo_voice" if action.payload.get("media_ids") else "chat",
                    image_media_id=(
                        uuid.UUID(action.payload["media_ids"][0])
                        if action.payload.get("media_ids")
                        else None
                    ),
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
                reply = t(
                    "owner.product_added",
                    lang,
                    name=item.product.name,
                    code=item.product.code,
                    link=f"{self.settings.web_url}/s/{tenant.slug}",
                )
            else:
                stored = await session.get(Tenant, tenant.id)
                assert stored is not None
                count = await apply_changes(session, stored, action.payload["ops"], actor)
                reply = t("owner.changes_applied", lang, count=count)
            actions.close(action, ActionStatus.CONFIRMED)
        return [OutText(reply)]
