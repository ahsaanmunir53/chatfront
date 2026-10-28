"""Imports every model so Alembic and tests see the whole schema."""

from chatfront.ai.models import AiCall
from chatfront.audit.models import AuditEntry
from chatfront.books.models import LedgerEntry, UdhaarAccount
from chatfront.catalog.models import (
    CatalogImport,
    CommerceCatalog,
    ImportDraft,
    Product,
    StockMovement,
    Variant,
)
from chatfront.channels.models import (
    ChannelAccount,
    Conversation,
    CustomerIdentity,
    Handover,
    IdentityLink,
    Message,
    PostComment,
    SavedAnswer,
    SocialPost,
)
from chatfront.core.db import Base
from chatfront.core.models import Customer, Staff, Tenant, TenantModule
from chatfront.delivery.models import PackingCheck, Shipment
from chatfront.jobs.models import CronRun, Job
from chatfront.media.models import Media
from chatfront.orders.models import (
    Bundle,
    Coupon,
    CouponUse,
    LoyaltyEntry,
    Order,
    OrderItem,
    PriceOffer,
    ReturnRequest,
    WaitlistEntry,
)
from chatfront.owner.models import PendingAction
from chatfront.payments.models import (
    Payment,
    PaymentClaim,
    Receipt,
    Refund,
    WalletCredit,
)
from chatfront.push.models import PushSubscription
from chatfront.shield.models import Approval, FraudListEntry, FraudReport, Review
from chatfront.sim.models import SimChat, SimRun
from chatfront.stock.models import BatchAllocation, OwnerNotice, StockBatch

__all__ = [
    "AiCall",
    "Approval",
    "AuditEntry",
    "Base",
    "BatchAllocation",
    "Bundle",
    "CatalogImport",
    "ChannelAccount",
    "CommerceCatalog",
    "Conversation",
    "Coupon",
    "CouponUse",
    "CronRun",
    "Customer",
    "CustomerIdentity",
    "FraudListEntry",
    "FraudReport",
    "Handover",
    "IdentityLink",
    "ImportDraft",
    "Job",
    "LedgerEntry",
    "LoyaltyEntry",
    "Media",
    "Message",
    "Order",
    "OrderItem",
    "OwnerNotice",
    "PackingCheck",
    "Payment",
    "PaymentClaim",
    "PendingAction",
    "PostComment",
    "PriceOffer",
    "Product",
    "PushSubscription",
    "Receipt",
    "Refund",
    "ReturnRequest",
    "Review",
    "SavedAnswer",
    "Shipment",
    "SimChat",
    "SimRun",
    "SocialPost",
    "Staff",
    "StockBatch",
    "StockMovement",
    "Tenant",
    "TenantModule",
    "UdhaarAccount",
    "Variant",
    "WaitlistEntry",
    "WalletCredit",
]
