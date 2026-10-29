"""Imports every model so Alembic and tests see the whole schema."""

from chatfront.ai.models import AiCall
from chatfront.audit.models import AuditEntry
from chatfront.bookings.models import Appointment
from chatfront.books.models import (
    CreditPassport,
    LedgerEntry,
    StaffPay,
    StaffPayment,
    UdhaarAccount,
)
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
from chatfront.core.models import Branch, Customer, Staff, Tenant, TenantModule
from chatfront.crm.models import Broadcast, BroadcastRecipient
from chatfront.delivery.models import (
    CodSettlement,
    CourierAccount,
    DeliveryProof,
    PackingCheck,
    Shipment,
)
from chatfront.insights.models import CoachNote
from chatfront.jobs.models import CronRun, Job
from chatfront.media.models import Media
from chatfront.network.models import (
    A2ABuyerPolicy,
    A2ARound,
    B2BOrder,
    MarketplacePayment,
    WholesaleListing,
)
from chatfront.orders.models import (
    Bundle,
    Coupon,
    CouponUse,
    CustomRequest,
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
from chatfront.shield.models import (
    Approval,
    Consent,
    FraudListEntry,
    FraudReport,
    Review,
    ScamReport,
)
from chatfront.sim.models import SimChat, SimRun
from chatfront.stock.models import BatchAllocation, OwnerNotice, StockBatch

__all__ = [
    "A2ABuyerPolicy",
    "A2ARound",
    "AiCall",
    "Appointment",
    "Approval",
    "AuditEntry",
    "B2BOrder",
    "Base",
    "BatchAllocation",
    "Branch",
    "Broadcast",
    "BroadcastRecipient",
    "Bundle",
    "CatalogImport",
    "ChannelAccount",
    "CoachNote",
    "CodSettlement",
    "CommerceCatalog",
    "Consent",
    "Conversation",
    "Coupon",
    "CouponUse",
    "CourierAccount",
    "CreditPassport",
    "CronRun",
    "CustomRequest",
    "Customer",
    "CustomerIdentity",
    "DeliveryProof",
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
    "MarketplacePayment",
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
    "ScamReport",
    "Shipment",
    "SimChat",
    "SimRun",
    "SocialPost",
    "Staff",
    "StaffPay",
    "StaffPayment",
    "StockBatch",
    "StockMovement",
    "Tenant",
    "TenantModule",
    "UdhaarAccount",
    "Variant",
    "WaitlistEntry",
    "WalletCredit",
    "WholesaleListing",
]
