"""Comment-to-DM (feature 77): "price?" under an Instagram post or reel, or a Facebook Page
post, gets the product in a private reply.

Code decides everything: a comment is answered only when it asks about price, stock or
ordering (a word list per language), only once per person per post, within the 7 days Meta
allows, and at most 750 an hour per account. The post's product comes from its caption (a
product code or name) or from the owner, who is asked once per post in the console; until then
the comments wait. The reply is written by code from the catalog, in the commenter's language,
and starts a DM thread: when they answer, the order agent takes over with the reply in view.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.catalog import sales
from chatfront.catalog.service import CatalogItem, load_items
from chatfront.channels import service as channels
from chatfront.channels.messaging import MetaMessagingAdapter
from chatfront.channels.models import (
    Channel,
    ChannelAccount,
    CommentStatus,
    Direction,
    Message,
    PostComment,
    SocialPost,
)
from chatfront.channels.types import to_record
from chatfront.core.clock import utcnow
from chatfront.core.countries import get_pack, shop_money
from chatfront.core.db import Database
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.langdetect import load_detector
from chatfront.core.models import Tenant
from chatfront.core.phone import hash_phone
from chatfront.core.settings import get_settings
from chatfront.jobs.queue import JobContext, enqueue
from chatfront.orders import live_claims

if TYPE_CHECKING:
    from chatfront.services import Services

log = logging.getLogger(__name__)

COMMENT_JOB = "comments.reply"
REPLY_WINDOW = timedelta(days=7)
HOURLY_LIMIT = 750
_WEBHOOK_CHANNELS = {"instagram": Channel.INSTAGRAM, "page": Channel.MESSENGER}
_WORD = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class Comment:
    channel: Channel  # Instagram, or Messenger for a Facebook Page
    account_external_id: str
    comment_id: str
    author_id: str
    text: str
    media_id: str | None
    commented_at: datetime


# -- reading webhooks -----------------------------------------------------------------------


def _time(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), UTC)
    except (TypeError, ValueError, OverflowError):
        return utcnow()


def _comment(channel: Channel, account: str, field: Any, value: dict[str, Any]) -> Comment | None:
    author = str((value.get("from") or {}).get("id") or "")
    if channel is Channel.INSTAGRAM and field in ("comments", "live_comments"):
        comment_id, text = value.get("id") or value.get("comment_id"), value.get("text")
        media = (value.get("media") or {}).get("id")
    elif (
        channel is Channel.MESSENGER
        and field == "feed"
        and value.get("item") == "comment"
        and value.get("verb") == "add"
    ):
        comment_id, text, media = (
            value.get("comment_id"),
            value.get("message"),
            value.get("post_id"),
        )
    else:
        return None
    if not comment_id or not author or not text or author == account:
        return None  # not a comment, or the shop's own
    return Comment(
        channel=channel,
        account_external_id=account,
        comment_id=str(comment_id),
        author_id=author,
        text=str(text)[:1000],
        media_id=str(media) if media else None,
        commented_at=_time(value.get("created_time")),
    )


def parse(payload: dict[str, Any]) -> list[Comment]:
    """New comments in a webhook. Instagram Login puts the field on the entry itself;
    Facebook Login and Pages use a list of changes."""
    channel = _WEBHOOK_CHANNELS.get(str(payload.get("object", "")))
    if channel is None:
        return []
    found = []
    for entry in payload.get("entry", []):
        account = str(entry.get("id", ""))
        changes = entry.get("changes") or (
            [{"field": entry["field"], "value": entry.get("value")}] if "field" in entry else []
        )
        for change in changes:
            try:
                comment = _comment(channel, account, change.get("field"), change.get("value") or {})
            except (AttributeError, TypeError):
                log.warning("skipping a %s comment that could not be read", channel)
                continue
            if comment is not None:
                found.append(comment)
    return found


# -- which comments to answer ---------------------------------------------------------------


@lru_cache
def _words() -> tuple[frozenset[str], tuple[str, ...]]:
    raw = json.loads((get_settings().i18n_dir / "comment-words.json").read_text("utf-8"))
    words = [unicodedata.normalize("NFKC", w).casefold() for w in raw["words"]]
    return frozenset(w for w in words if " " not in w), tuple(w for w in words if " " in w)


def asks(text: str) -> bool:
    """Whether a comment asks about price, stock or ordering."""
    plain = unicodedata.normalize("NFKC", text).casefold()
    single, phrases = _words()
    return bool(single.intersection(_WORD.findall(plain))) or any(p in plain for p in phrases)


def product_named(items: list[CatalogItem], text: str | None) -> CatalogItem | None:
    """The product whose code is written in the text, else the one whose name is."""
    if not text:
        return None
    words = {word.upper() for word in _WORD.findall(text)}
    plain = text.casefold()
    by_code = [item for item in items if item.product.code.upper() in words]
    by_name = [item for item in items if item.product.name.casefold() in plain]
    found = by_code or sorted(by_name, key=lambda item: -len(item.product.name))
    return found[0] if found else None


# -- storing and answering -------------------------------------------------------------------


async def live_claim(services: Services, account: ChannelAccount, comment: Comment) -> str | None:
    """A comment that is a number during a live sale takes a piece (feature 8), first come
    first served; the answer to send it. The comment's author is the customer: with Instagram
    Login their id in a comment is the one their messages come from. None when it is not a
    claim, or the comment was seen before."""
    number = sales.claim_number(comment.text)
    if number is None:
        return None
    async with services.db.tenant(account.tenant_id) as session:
        seen = await session.scalar(
            select(PostComment.id).where(
                PostComment.channel == comment.channel, PostComment.comment_id == comment.comment_id
            )
        )
        tenant = await session.get(Tenant, account.tenant_id)
        if seen is not None or tenant is None or await sales.live_item(session, number) is None:
            return None
        lang = _language(tenant, comment.text)
        customer = await channels.identify_sender(
            session, tenant.id, Channel(account.channel), comment.author_id, services.keys
        )
        chat = await channels.open_conversation(session, tenant.id, customer.id, account.id)
        chat.lang = chat.lang or lang
        replies = await live_claims.claim(
            session, tenant, customer.id, chat.id, number, lang, first_choice=True
        )
    return to_record(replies[0])[0] if replies else None


async def accept(
    db: Database, account: ChannelAccount, comment: Comment, key: str, claim: str | None = None
) -> bool:
    """Keep the comment and queue its answer. False for a comment seen before."""
    async with db.tenant(account.tenant_id) as session:
        stored = await session.scalar(
            insert(PostComment)
            .values(
                id=uuid.uuid4(),
                tenant_id=account.tenant_id,
                channel_account_id=account.id,
                channel=comment.channel,
                comment_id=comment.comment_id,
                author_hash=hash_phone(f"{comment.channel}:comment:{comment.author_id}", key),
                text=comment.text,
                status=CommentStatus.NEW,
                claim_reply=claim,
                commented_at=comment.commented_at,
                post_id=await _post(session, account, comment.media_id),
            )
            .on_conflict_do_nothing(index_elements=["channel", "comment_id"])
            .returning(PostComment.id)
        )
        if stored is None:
            return False
        await enqueue(
            session,
            COMMENT_JOB,
            {"comment": str(stored)},
            tenant_id=account.tenant_id,
            dedupe_key=f"comment:{stored}",
        )
    return True


async def _post(
    session: AsyncSession, account: ChannelAccount, media_id: str | None
) -> uuid.UUID | None:
    if media_id is None:
        return None
    await session.execute(
        insert(SocialPost)
        .values(
            id=uuid.uuid4(),
            tenant_id=account.tenant_id,
            channel=account.channel,
            media_id=media_id,
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "channel", "media_id"])
    )
    return await session.scalar(
        select(SocialPost.id).where(
            SocialPost.channel == account.channel, SocialPost.media_id == media_id
        )
    )


def _name(item: CatalogItem, lang: str) -> str:
    texts = item.product.translations or {}
    translated = (texts.get(lang) or {}).get("name") if isinstance(texts, dict) else None
    return str(translated or item.product.name)


def product_message(tenant: Tenant, item: CatalogItem, lang: str, link: str) -> str:
    """The private reply: the product, its price and options from the catalog, and how to
    order. Written by code, never by a model."""
    active = [v for v in item.variants if v.active]
    prices: list[int] = sorted({item.price_of(v) for v in active}) or [item.product.price]
    price = shop_money(tenant, prices[0], lang)
    shown = t("comments.from", lang, price=price) if len(prices) > 1 else price
    lines = [t("comments.hello", lang), f"{_name(item, lang)}: {shown}"]
    options = list(
        dict.fromkeys(
            str(value)
            for variant in active
            if variant.stock is None or variant.stock > 0
            for value in variant.options.values()
        )
    )
    if options:
        lines.append(t("comments.options", lang, options=", ".join(options)))
    if not item.in_stock:
        lines.append(t("comments.sold_out", lang))
    lines.append(t("comments.order_here", lang, link=link))
    return "\n".join(lines)


def _language(tenant: Tenant, text: str) -> str:
    found = load_detector().detect(text, likely=get_pack(tenant.country).languages)
    return found.tag if found is not None else tenant.default_lang


async def _skip(services: Services, row: PostComment, status: CommentStatus, reason: str) -> None:
    async with services.db.tenant(row.tenant_id) as session:
        stored = await session.get(PostComment, row.id)
        assert stored is not None
        stored.status, stored.reason = status, reason


async def _why_not(session: AsyncSession, row: PostComment, now: datetime) -> str | None:
    """The reason a comment gets no reply, or None when it should get one."""
    if now - row.commented_at > REPLY_WINDOW:
        return "too_old"
    if not asks(row.text):
        return "not_a_question"
    answered = await session.scalar(
        select(PostComment.id).where(
            PostComment.post_id == row.post_id,
            PostComment.author_hash == row.author_hash,
            PostComment.status == CommentStatus.SENT,
            PostComment.id != row.id,
        )
    )
    if answered is not None:
        return "already_answered"
    sent_this_hour = await session.scalar(
        select(func.count(PostComment.id)).where(
            PostComment.channel_account_id == row.channel_account_id,
            PostComment.status == CommentStatus.SENT,
            PostComment.replied_at > now - timedelta(hours=1),
        )
    )
    return "hourly_limit" if (sent_this_hour or 0) >= HOURLY_LIMIT else None


async def _learn_post(services: Services, account: ChannelAccount, post: SocialPost) -> None:
    """Fetch the post's caption and link once, and take the product from the caption."""
    adapter = services.adapters[Channel(account.channel)]
    if post.permalink is not None or not isinstance(adapter, MetaMessagingAdapter):
        return
    ref = channels.account_ref(account, services.keys, services.fallback_token(account.channel))
    try:
        caption, link = await adapter.post_info(ref, post.media_id)
    except AppError as exc:
        log.info("could not read a post: %s", exc.code)
        return
    async with services.db.tenant(account.tenant_id) as session:
        stored = await session.get(SocialPost, post.id)
        assert stored is not None
        stored.caption, stored.permalink = (caption or "")[:2000] or None, link
        if stored.product_id is None:
            named = product_named(await load_items(session), caption)
            if named is not None:
                stored.product_id, stored.linked_by = named.product.id, "caption"
        post.caption, post.permalink, post.product_id = (
            stored.caption,
            stored.permalink,
            stored.product_id,
        )


async def reply_to_comment(ctx: JobContext) -> None:
    services: Services = ctx.services
    assert ctx.tenant_id is not None
    now = utcnow()
    async with services.db.tenant(ctx.tenant_id) as session:
        row = await session.get(PostComment, uuid.UUID(ctx.payload["comment"]))
        if row is None or row.status not in (CommentStatus.NEW, CommentStatus.WAITING):
            return
        account = await session.get(ChannelAccount, row.channel_account_id)
        tenant = await session.get(Tenant, ctx.tenant_id)
        post = await session.get(SocialPost, row.post_id) if row.post_id else None
        reason = await _why_not(session, row, now)
    assert account is not None
    assert tenant is not None
    if row.claim_reply:  # a live sale's claim: code decided it when the comment came
        await _send_text(services, tenant, account, row, row.claim_reply, None)
        return
    if reason is not None:
        await _skip(services, row, CommentStatus.SKIPPED, reason)
        return
    if post is not None:
        await _learn_post(services, account, post)
    async with services.db.tenant(tenant.id) as session:
        items = await load_items(session)
    by_id = {item.product.id: item for item in items}
    item = (by_id.get(post.product_id) if post and post.product_id else None) or product_named(
        items, row.text
    )
    if item is None:
        await _skip(services, row, CommentStatus.WAITING, "which_product")
        return
    await _send(services, tenant, account, row, item)


async def _send(
    services: Services,
    tenant: Tenant,
    account: ChannelAccount,
    row: PostComment,
    item: CatalogItem,
) -> None:
    lang = _language(tenant, row.text)
    link = f"{services.settings.web_url}/s/{tenant.slug}#p-{item.product.code}"
    text = product_message(tenant, item, lang, link)
    await _send_text(services, tenant, account, row, text, item.product.id)


async def _send_text(
    services: Services,
    tenant: Tenant,
    account: ChannelAccount,
    row: PostComment,
    text: str,
    product_id: uuid.UUID | None,
) -> None:
    """The private reply, and the DM thread it starts."""
    adapter = services.adapters[Channel(account.channel)]
    assert isinstance(adapter, MetaMessagingAdapter)
    lang = _language(tenant, row.text)
    ref = channels.account_ref(account, services.keys, services.fallback_token(account.channel))
    try:
        recipient, message_id = await adapter.private_reply(ref, row.comment_id, text)
    except AppError as exc:
        await _skip(services, row, CommentStatus.FAILED, exc.code[:30])
        return
    async with services.db.tenant(tenant.id) as session:
        customer = await channels.identify_sender(
            session, tenant.id, Channel(account.channel), recipient, services.keys
        )
        chat = await channels.open_conversation(session, tenant.id, customer.id, account.id)
        chat.lang = chat.lang or lang
        session.add(
            Message(
                tenant_id=tenant.id,
                conversation_id=chat.id,
                direction=Direction.OUT,
                channel=account.channel,
                external_id=message_id,
                kind="text",
                body=text,
                payload={"by": "agent", "comment_id": row.comment_id, "lang": lang},
                status="sent",
            )
        )
        stored = await session.get(PostComment, row.id)
        assert stored is not None
        stored.status, stored.reason = CommentStatus.SENT, None
        stored.lang, stored.product_id = lang, product_id or stored.product_id
        stored.conversation_id, stored.message_id, stored.replied_at = chat.id, message_id, utcnow()


async def answer_waiting(services: Services, tenant_id: uuid.UUID, post_id: uuid.UUID) -> int:
    """After the owner says which product a post shows: answer the comments that waited."""
    now = utcnow()
    async with services.db.tenant(tenant_id) as session:
        waiting = list(
            await session.scalars(
                select(PostComment.id).where(
                    PostComment.post_id == post_id,
                    PostComment.status == CommentStatus.WAITING,
                    PostComment.commented_at > now - REPLY_WINDOW,
                )
            )
        )
        for comment_id in waiting:
            await enqueue(
                session,
                COMMENT_JOB,
                {"comment": str(comment_id)},
                tenant_id=tenant_id,
                dedupe_key=f"comment:{comment_id}:{now.timestamp()}",
            )
    return len(waiting)
