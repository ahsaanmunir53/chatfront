"""Imports every model so Alembic and tests see the whole schema."""

from chatfront.ai.models import AiCall
from chatfront.audit.models import AuditEntry
from chatfront.books.models import LedgerEntry, UdhaarAccount
from chatfront.catalog.models import Product, StockMovement, Variant
from chatfront.channels.models import (
    ChannelAccount,
    Conversation,
    CustomerIdentity,
    Handover,
    IdentityLink,
    Message,
)
from chatfront.core.db import Base
from chatfront.core.models import Customer, Staff, Tenant, TenantModule
from chatfront.delivery.models import Shipment
from chatfront.jobs.models import CronRun, Job
from chatfront.media.models import Media
from chatfront.orders.models import Order, OrderItem, PriceOffer
from chatfront.owner.models import PendingAction
from chatfront.payments.models import Payment, PaymentClaim, Receipt, WalletCredit
from chatfront.shield.models import Approval
from chatfront.stock.models import BatchAllocation, OwnerNotice, StockBatch

__all__ = [
    "AiCall",
    "Approval",
    "AuditEntry",
    "Base",
    "BatchAllocation",
    "ChannelAccount",
    "Conversation",
    "CronRun",
    "Customer",
    "CustomerIdentity",
    "Handover",
    "IdentityLink",
    "Job",
    "LedgerEntry",
    "Media",
    "Message",
    "Order",
    "OrderItem",
    "OwnerNotice",
    "Payment",
    "PaymentClaim",
    "PendingAction",
    "PriceOffer",
    "Product",
    "Receipt",
    "Shipment",
    "Staff",
    "StockBatch",
    "StockMovement",
    "Tenant",
    "TenantModule",
    "UdhaarAccount",
    "Variant",
    "WalletCredit",
]
