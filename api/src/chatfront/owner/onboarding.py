"""Shop in five minutes (feature 83): someone messages Chatfront's own number or the web start
page, answers a few questions, sends a few product photos, and the shop, its catalog and its
page are made.

Spark Open reads the answers; code keeps the steps, checks every answer and makes the shop.
Without a model, simple word lists read the answers, so the flow still works on the mock.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Role, TextPart
from chatfront.audit.service import Actor, ActorType
from chatfront.catalog.drafts import (
    EMPTY_DRAFT,
    ProductDraft,
    complete_draft,
    read_product,
    to_input,
)
from chatfront.catalog.service import create_product
from chatfront.channels.conversation import Turn
from chatfront.channels.models import Channel
from chatfront.channels.types import Kind, ListRow, Outbound, OutList, OutText
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack, shop_money
from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Customer, Tenant, Tier
from chatfront.core.provisioning import NewShop, create_shop, free_slug, owner_of
from chatfront.core.settings import Settings
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.media.storage import Storage
from chatfront.modules.registry import ModuleRegistry
from chatfront.owner import actions
from chatfront.owner.assistant import prompt_context
from chatfront.owner.cards import confirm_card
from chatfront.owner.models import ActionStatus, PendingAction
from chatfront.owner.sessions import console_link, issue
from chatfront.templates import all_templates, get_template

KIND = "onboarding"
WEB_SHOP_LIFETIME = timedelta(days=7)  # shops made anonymously on the website are trials
MAX_PRODUCTS = 10
STEPS = ("shop_name", "business_type", "city", "delivery", "payment_methods", "products")
_QUESTION = {
    "shop_name": "start.ask_name",
    "business_type": "start.ask_type",
    "city": "start.ask_city",
    "delivery": "start.ask_delivery",
    "payment_methods": "start.ask_payment",
    "products": "start.ask_products",
}
PAYMENTS = ("jazzcash", "easypaisa", "bank", "raast", "cod")
_PAYMENT_WORDS = {
    "jazzcash": ("jazzcash", "jazz cash", "jazz", "جاز"),
    "easypaisa": ("easypaisa", "easy paisa", "ایزی"),
    "bank": ("bank", "ibft", "transfer", "account", "بینک"),
    "raast": ("raast", "راست"),
    "cod": ("cod", "cash on delivery", "cash", "نقد", "کیش"),
}
_TYPE_WORDS = {
    "clothing": ("kapre", "kapra", "clothes", "suit", "lawn", "dress", "fashion", "کپڑے", "سوٹ"),
    "food": ("khana", "food", "biryani", "cake", "kitchen", "bakery", "کھانا"),
    "grocery": ("kiryana", "karyana", "grocery", "sauda", "کریانہ"),
    "services": ("salon", "parlour", "parlor", "tutor", "repair", "makeup", "سیلون"),
    "electronics": ("mobile", "phone", "electronics", "laptop", "charger", "موبائل"),
    "wholesale": ("wholesale", "thok", "bulk", "تھوک"),
}
_DONE_WORDS = (
    *("done", "bas", "ho gaya", "hogaya", "khatam", "finish", "skip", "shesh"),
    *("بس", "ہو گیا", "बस", "हो गया", "শেষ", "خلاص", "انتهيت"),
)


class ShopAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_name: str | None
    business_type: str | None
    city: str | None
    owner_name: str | None
    delivery: str | None
    payment_methods: list[str]
    done: bool
    cancel: bool


def _has(text: str, words: tuple[str, ...]) -> bool:
    """Latin words must start a word ("cash", not "ca-cash"); other scripts match anywhere."""
    lowered = f" {text.casefold()} "
    return any((f" {word}" in lowered) if word.isascii() else (word in lowered) for word in words)


def read_by_words(step: str, text: str) -> dict[str, Any]:
    """Code's own reading of an answer, used when no model is available."""
    words = text.strip()
    found: dict[str, Any] = {}
    payments = [key for key, keys in _PAYMENT_WORDS.items() if _has(words, keys)]
    if payments:
        found["payment_methods"] = payments
    kinds = [key for key, keys in _TYPE_WORDS.items() if _has(words, keys)]
    if len(kinds) == 1:
        found["business_type"] = kinds[0]
    if _has(words, _DONE_WORDS):
        found["done"] = True
    if step in ("shop_name", "city", "delivery") and words and not found.get("done"):
        found.setdefault(step, words[:120])
    return found


def merge(answers: dict[str, Any], read: dict[str, Any]) -> dict[str, Any]:
    """New answers win; only valid values are kept."""
    merged = dict(answers)
    for key in ("shop_name", "city", "owner_name", "delivery"):
        value = read.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()[:120]
    if read.get("business_type") in all_templates():
        merged["business_type"] = read["business_type"]
    methods = [m for m in read.get("payment_methods") or [] if m in PAYMENTS]
    if methods:
        merged["payment_methods"] = sorted(set(methods), key=PAYMENTS.index)
    return merged


def next_step(state: dict[str, Any]) -> str | None:
    answers = state.get("answers", {})
    for step in STEPS:
        if step == "products":
            return None if state.get("products_done") else step
        if not answers.get(step):
            return step
    return None


class OnboardingAssistant:
    def __init__(
        self,
        db: Database,
        ai: AiClient,
        storage: Storage,
        settings: Settings,
        modules: ModuleRegistry,
    ) -> None:
        self.db = db
        self.ai = ai
        self.storage = storage
        self.settings = settings
        self.modules = modules

    async def respond(self, turn: Turn) -> list[Outbound]:
        lang, conversation = turn.lang, turn.conversation
        async with self.db.tenant(turn.tenant.id) as session:
            action = await actions.current_action(session, conversation.id, KIND)
            made = await actions.last_confirmed(session, conversation.id, KIND)
            if action is None and made is not None:
                return [OutText(self._made_text(made.payload, lang))]
            if action is None:
                action = await actions.open_action(
                    session,
                    tenant_id=turn.tenant.id,
                    conversation_id=conversation.id,
                    staff_id=None,
                    kind=KIND,
                    payload={"answers": {}, "products": []},
                )
                return [OutText(t("start.welcome", lang))]
        state = dict(action.payload)
        choice = actions.parse_button((turn.message.payload or {}).get("button_id"))
        if choice is not None:
            return await self._answer(turn, action, state, choice[1])
        if turn.message.kind == Kind.IMAGE or state.get("photo"):
            return await self._product(turn, action, state)
        return await self._answers(turn, action, state)

    # -- questions ------------------------------------------------------------------------

    async def _read(self, turn: Turn, state: dict[str, Any], step: str) -> dict[str, Any]:
        text = turn.message.body or ""
        prompt = load_prompt("onboarding")
        parts = [turn.media] if turn.message.kind == Kind.AUDIO and turn.media else []
        request = ChatRequest(
            role=Role.AUDIO if parts else Role.CHAT,
            messages=[
                ChatMessage(
                    "system",
                    prompt.render(
                        types=", ".join(all_templates()), answers=json.dumps(state["answers"])
                    ),
                ),
                ChatMessage("user", [*parts, TextPart(text or "(voice note)")]),
            ],
            tenant_id=turn.tenant.id,
            prompt=prompt.ref,
            lang=turn.lang,
        )
        try:
            answers, _ = await self.ai.complete_json(request, ShopAnswers)
        except AppError:
            return read_by_words(step, text)
        read = answers.model_dump()
        return read if any(read.values()) else read_by_words(step, text)

    async def _answers(
        self, turn: Turn, action: PendingAction, state: dict[str, Any]
    ) -> list[Outbound]:
        step = next_step(state) or "products"
        read = await self._read(turn, state, step)
        if read.get("cancel"):
            await self._save(action, state, ActionStatus.CANCELLED)
            return [OutText(t("start.cancelled", turn.lang))]
        state["answers"] = merge(state["answers"], read)
        if step == "products" and read.get("done"):
            state["products_done"] = True
        return await self._ask_next(turn, action, state)

    async def _ask_next(
        self, turn: Turn, action: PendingAction, state: dict[str, Any]
    ) -> list[Outbound]:
        lang = turn.lang
        step = next_step(state)
        if step is None:
            saved = await self._save(action, state, ActionStatus.PENDING)
            return [
                confirm_card(
                    saved,
                    t("start.summary", lang, summary=self._summary(state, lang)),
                    "start.make",
                    lang,
                )
            ]
        saved = await self._save(action, state, ActionStatus.COLLECTING)
        if step == "business_type":
            rows = tuple(
                ListRow(actions.button_id(saved, f"type:{key}"), self._type_name(key, lang)[:24])
                for key in all_templates()
            )
            return [OutList(t(_QUESTION[step], lang), t("start.choose", lang)[:20], rows)]
        return [OutText(t(_QUESTION[step], lang))]

    @staticmethod
    def _type_name(key: str, lang: str) -> str:
        names = get_template(key).name.model_dump(by_alias=True)
        return str(names.get(lang) or names["en"])

    def _summary(self, state: dict[str, Any], lang: str) -> str:
        answers = state["answers"]
        lines = [
            answers["shop_name"],
            self._type_name(answers["business_type"], lang),
            answers["city"],
            answers["delivery"],
            ", ".join(t(f"start.pay.{m}", lang) for m in answers["payment_methods"]),
        ]
        lines += [f"- {p['draft']['name']}" for p in state.get("products", [])]
        return "\n".join(lines)

    async def _save(
        self, action: PendingAction, state: dict[str, Any], status: ActionStatus
    ) -> PendingAction:
        async with self.db.tenant(action.tenant_id) as session:
            stored = await session.get(PendingAction, action.id)
            assert stored is not None
            actions.update(stored, dict(state), status)
            if status in (ActionStatus.CANCELLED, ActionStatus.CONFIRMED):
                actions.close(stored, status)
        return stored

    # -- products sent while making the shop -----------------------------------------------

    async def _product(
        self, turn: Turn, action: PendingAction, state: dict[str, Any]
    ) -> list[Outbound]:
        lang, message = turn.lang, turn.message
        pending = state.get("photo") or {}
        if message.kind == Kind.IMAGE and message.media_id is not None:
            pending = {"media_id": str(message.media_id), "draft": None}
        words = message.body or ""
        template_id = state["answers"].get("business_type", "clothing")
        context = prompt_context(turn.tenant) | {
            "shop_name": state["answers"].get("shop_name", "the new shop"),
            "business_type": get_template(template_id).name.en.lower(),
        }
        async with self.db.tenant(turn.tenant.id) as session:
            photo = await session.get(Media, uuid.UUID(pending["media_id"]))
        media = [await media_service.model_part(self.storage, photo)] if photo else []
        if message.kind == Kind.AUDIO and turn.media is not None:
            media.append(turn.media)
        previous = ProductDraft.model_validate(pending["draft"]) if pending.get("draft") else None
        try:
            draft = await read_product(
                self.ai,
                tenant_id=turn.tenant.id,
                context=context,
                media=media,
                words=words,
                previous=previous,
                lang=lang,
            )
        except AppError:
            draft = previous or EMPTY_DRAFT
        draft = complete_draft(draft, words, lang)
        product = to_input(
            draft, get_template(template_id), 2, source="onboarding", image_media_id=None
        )
        if product is None:
            state["photo"] = {"media_id": pending["media_id"], "draft": draft.model_dump()}
            await self._save(action, state, ActionStatus.COLLECTING)
            key = "owner.photo_received" if not words else "owner.need_price"
            return [OutText(t(key, lang))]
        state.pop("photo", None)
        state["products"] = [
            *state.get("products", []),
            {"media_id": pending["media_id"], "draft": draft.model_dump()},
        ][:MAX_PRODUCTS]
        await self._save(action, state, ActionStatus.COLLECTING)
        price = shop_money(turn.tenant, product.price, lang)
        return [OutText(t("start.product_noted", lang, name=product.name, price=price))]

    # -- making the shop ------------------------------------------------------------------

    async def _answer(
        self, turn: Turn, action: PendingAction, state: dict[str, Any], answer: str
    ) -> list[Outbound]:
        lang = turn.lang
        if answer.startswith("type:"):
            state["answers"] = merge(
                state["answers"], {"business_type": answer.removeprefix("type:")}
            )
            return await self._ask_next(turn, action, state)
        if answer != "yes" or next_step(state) is not None:
            await self._save(action, state, ActionStatus.CANCELLED)
            return [OutText(t("start.cancelled", lang))]
        tenant, token = await self._make_shop(turn, state)
        state["shop_slug"] = tenant.slug
        state["owner_login"] = token
        await self._save(action, state, ActionStatus.CONFIRMED)
        return [OutText(self._made_text(state, lang))]

    def _made_text(self, state: dict[str, Any], lang: str) -> str:
        web = self.settings.web_url
        text = t(
            "start.made",
            lang,
            link=f"{web}/s/{state['shop_slug']}",
            owner_link=console_link(web, state["owner_login"]),
        )
        return text if not state.get("trial") else f"{text}\n{t('start.trial_note', lang)}"

    async def _make_shop(self, turn: Turn, state: dict[str, Any]) -> tuple[Tenant, str]:
        answers = state["answers"]
        on_web = turn.message.channel == Channel.WEBCHAT
        state["trial"] = on_web
        async with self.db.tenant(turn.tenant.id) as session:
            customer = await session.get(Customer, turn.conversation.customer_id)
        slug = await free_slug(self.db, answers["shop_name"])
        tenant = await create_shop(
            self.db,
            self.modules,
            NewShop(
                slug=slug,
                name=answers["shop_name"],
                template=answers["business_type"],
                tier=Tier.SMALL,
                owner_name=answers.get("owner_name") or answers["shop_name"],
                owner_email=f"owner@{slug}.chatfront.invalid",
                profile={
                    "city": answers["city"],
                    "delivery": answers["delivery"],
                    "payment": ", ".join(
                        t(f"start.pay.{m}", "en") for m in answers["payment_methods"]
                    ),
                    "payment_methods": answers["payment_methods"],
                },
                expires_at=utcnow() + WEB_SHOP_LIFETIME if on_web else None,
                owner_phone_hash=customer.phone_hash if customer and not on_web else None,
            ),
        )
        owner = await owner_of(self.db, tenant.id)
        await self._copy_products(turn.tenant.id, tenant, state.get("products", []), owner.id)
        return tenant, issue(owner.id, tenant.id, self.settings.secret_key.get_secret_value())

    async def _copy_products(
        self,
        source_tenant: uuid.UUID,
        tenant: Tenant,
        products: list[dict[str, Any]],
        owner_id: uuid.UUID,
    ) -> None:
        """The products sent while making the shop, with their photos copied into the new shop."""
        template = get_template(tenant.template)
        digits = get_pack(tenant.country).minor_digits
        actor = Actor(ActorType.STAFF, str(owner_id))
        for entry in products:
            async with self.db.tenant(source_tenant) as session:
                photo = await session.get(Media, uuid.UUID(entry["media_id"]))
            data = await self.storage.get(photo.storage_key) if photo else None
            async with self.db.tenant(tenant.id) as session:
                image_id = None
                if data is not None:
                    stored = await media_service.store(
                        session,
                        self.storage,
                        tenant.id,
                        data,
                        source="onboarding",
                        max_bytes=self.settings.max_upload_bytes,
                    )
                    image_id = stored.id
                product = to_input(
                    ProductDraft.model_validate(entry["draft"]),
                    template,
                    digits,
                    source="onboarding",
                    image_media_id=image_id,
                )
                if product is not None:
                    await create_product(session, tenant.id, product, actor)
