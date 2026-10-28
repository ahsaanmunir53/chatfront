"""Imports every model so Alembic and tests see the whole schema."""

from chatfront.ai.models import AiCall
from chatfront.audit.models import AuditEntry
from chatfront.books.models import LedgerEntry, UdhaarAccount
from chatfront.catalog.models import (
    CatalogImport,
    CommerceCatalog,
    FlashSale,
    ImportDraft,
    Product,
    SaleHold,
    SaleItem,
    StockMovement,
    Variant,
)
from chatfront.channels.models import (
    ChannelAccount,
    Conversation,
    CustomerIdentity,
    Handover,
    IdentityLink,
    Lead,
    Message,
    PostComment,
    SavedAnswer,
    SocialPost,
)
from chatfront.core.db import Base
from chatfront.core.models import Customer, Staff, Tenant, TenantModule
from chatfront.crm.models import Broadcast, BroadcastRecipient
from chatfront.delivery.models import PackingCheck, Shipment
from chatfront.jobs.models import CronRun, Job
from chatfront.media.models import Media
from chatfront.orders.models import (
    Bundle,
    Coupon,
    CouponUse,
    GroupOrder,
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
from chatfront.publish.models import Publication
from chatfront.push.models import PushSubscription
from chatfront.shield.models import Approval, Consent, FraudListEntry, FraudReport, Review
from chatfront.sim.models import SimChat, SimRun
from chatfront.stock.models import BatchAllocation, OwnerNotice, StockBatch

__all__ = [
    "AiCall",
    "Approval",
    "AuditEntry",
    "Base",
    "BatchAllocation",
    "Broadcast",
    "BroadcastRecipient",
    "Bundle",
    "CatalogImport",
    "ChannelAccount",
    "CommerceCatalog",
    "Consent",
    "Conversation",
    "Coupon",
    "CouponUse",
    "CronRun",
    "Customer",
    "CustomerIdentity",
    "FlashSale",
    "FraudListEntry",
    "FraudReport",
    "GroupOrder",
    "Handover",
    "IdentityLink",
    "ImportDraft",
    "Job",
    "Lead",
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
    "Publication",
    "PushSubscription",
    "Receipt",
    "Refund",
    "ReturnRequest",
    "Review",
    "SaleHold",
    "SaleItem",
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
