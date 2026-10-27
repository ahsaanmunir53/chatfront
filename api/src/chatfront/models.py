"""Imports every model so Alembic and tests see the whole schema."""

from chatfront.ai.models import AiCall
from chatfront.audit.models import AuditEntry
from chatfront.catalog.models import Product, StockMovement, Variant
from chatfront.channels.models import (
    ChannelAccount,
    Conversation,
    CustomerIdentity,
    Handover,
    Message,
)
from chatfront.core.db import Base
from chatfront.core.models import Customer, Staff, Tenant, TenantModule
from chatfront.jobs.models import CronRun, Job
from chatfront.media.models import Media
from chatfront.orders.models import Order, OrderItem, PriceOffer
from chatfront.owner.models import PendingAction

__all__ = [
    "AiCall",
    "AuditEntry",
    "Base",
    "ChannelAccount",
    "Conversation",
    "CronRun",
    "Customer",
    "CustomerIdentity",
    "Handover",
    "Job",
    "Media",
    "Message",
    "Order",
    "OrderItem",
    "PendingAction",
    "PriceOffer",
    "Product",
    "Staff",
    "StockMovement",
    "Tenant",
    "TenantModule",
    "Variant",
]
