"""The shop's posts that customers comment on, in the console (feature 77): which product each
shows, the comments waiting for the owner to say so, and what was answered."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import ColumnElement, case, func, select

from chatfront.audit.service import Actor, ActorType, record
from chatfront.catalog.models import Product
from chatfront.channels.comments import answer_waiting
from chatfront.channels.models import CommentStatus, PostComment, SocialPost
from chatfront.core.errors import AppError, NotFound
from chatfront.owner.routes import ChatsDep, ServicesDep

router = APIRouter(prefix="/api/owner/posts")

SHOWN = 30


class PostOut(BaseModel):
    id: uuid.UUID
    channel: str
    permalink: str | None
    caption: str | None
    product_id: uuid.UUID | None
    product_name: str | None
    linked_by: str | None  # caption or owner
    waiting: int  # comments waiting for the product
    answered: int


class CommentOut(BaseModel):
    id: uuid.UUID
    channel: str
    text: str
    status: str
    reason: str | None
    product_name: str | None
    post_id: uuid.UUID | None
    commented_at: datetime


class Posts(BaseModel):
    posts: list[PostOut]
    comments: list[CommentOut]


class PostProduct(BaseModel):
    product_id: uuid.UUID | None


def _count(status: CommentStatus) -> ColumnElement[int]:
    return func.count(case((PostComment.status == status, PostComment.id)))


@router.get("")
async def posts(session: ChatsDep, services: ServicesDep) -> Posts:
    async with services.db.tenant(session.tenant_id) as db:
        waiting, answered = _count(CommentStatus.WAITING), _count(CommentStatus.SENT)
        rows = await db.execute(
            select(SocialPost, Product.name, waiting, answered)
            .outerjoin(Product, Product.id == SocialPost.product_id)
            .outerjoin(PostComment, PostComment.post_id == SocialPost.id)
            .group_by(SocialPost.id, Product.name)
            .order_by(waiting.desc(), SocialPost.created_at.desc())
            .limit(SHOWN)
        )
        found = [
            PostOut(
                id=post.id,
                channel=post.channel,
                permalink=post.permalink,
                caption=(post.caption or "")[:200] or None,
                product_id=post.product_id,
                product_name=name,
                linked_by=post.linked_by,
                waiting=waits,
                answered=sent,
            )
            for post, name, waits, sent in rows.all()
        ]
        recent = await db.execute(
            select(PostComment, Product.name)
            .outerjoin(Product, Product.id == PostComment.product_id)
            .order_by(PostComment.commented_at.desc())
            .limit(SHOWN)
        )
        comments = [
            CommentOut(
                id=comment.id,
                channel=comment.channel,
                text=comment.text[:300],
                status=comment.status,
                reason=comment.reason,
                product_name=name,
                post_id=comment.post_id,
                commented_at=comment.commented_at,
            )
            for comment, name in recent.all()
        ]
    return Posts(posts=found, comments=comments)


@router.put("/{post_id}/product")
async def set_product(
    post_id: uuid.UUID, body: PostProduct, session: ChatsDep, services: ServicesDep
) -> dict[str, int]:
    """Say which product a post shows; the comments that waited for it are answered now."""
    async with services.db.tenant(session.tenant_id) as db:
        post = await db.get(SocialPost, post_id)
        if post is None:
            raise NotFound("post")
        if body.product_id is not None and await db.get(Product, body.product_id) is None:
            raise AppError("unknown_product", "No such product in this shop")
        post.product_id = body.product_id
        post.linked_by = "owner" if body.product_id else None
        await record(
            db,
            session.tenant_id,
            Actor(ActorType.STAFF, str(session.staff_id)),
            "post.product",
            entity=("post", str(post_id)),
            data={"product": str(body.product_id) if body.product_id else None},
        )
    queued = await answer_waiting(services, session.tenant_id, post_id) if body.product_id else 0
    return {"answering": queued}
