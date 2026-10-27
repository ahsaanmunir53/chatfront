"""Imports every model so Alembic and tests see the whole schema."""

from chatfront.ai.models import AiCall
from chatfront.audit.models import AuditEntry
from chatfront.channels.models import ChannelAccount, Conversation, CustomerIdentity, Message
from chatfront.core.db import Base
from chatfront.core.models import Customer, Staff, Tenant, TenantModule
from chatfront.jobs.models import CronRun, Job
from chatfront.media.models import Media

# Tables that carry tenant_id and get the row-level security policy.
TENANT_TABLES = (
    "tenant_modules",
    "staff",
    "customers",
    "channel_accounts",
    "customer_identities",
    "conversations",
    "messages",
    "media",
    "audit_log",
    "ai_calls",
    "jobs",
)

__all__ = [
    "TENANT_TABLES",
    "AiCall",
    "AuditEntry",
    "Base",
    "ChannelAccount",
    "Conversation",
    "CronRun",
    "Customer",
    "CustomerIdentity",
    "Job",
    "Media",
    "Message",
    "Staff",
    "Tenant",
    "TenantModule",
]
