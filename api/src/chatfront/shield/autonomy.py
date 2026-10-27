"""Autonomy levels per action (feature 104): for each kind of decision the owner says whether
Chatfront does it on its own, asks first, or never does it.

- discounts: auto (bargain down to the lowest price the owner set), ask (a discount of more
  than 10% goes to the owner first), never (prices are fixed);
- sms_payments: auto (a verified SMS match settles a payment), ask (the owner approves each);
- risk_advance: auto (high-risk cash on delivery asks for an advance), never;
- udhaar_reminders: auto (sent on schedule), ask (the owner sends or skips each), never;
- low_stock_alerts: auto, never.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from chatfront.core.errors import AppError
from chatfront.core.models import Tenant

PROFILE_KEY = "autonomy"
ASK_DISCOUNT_OVER = 10  # percent below the list price that "ask" sends to the owner


class Level(StrEnum):
    AUTO = "auto"
    ASK = "ask"
    NEVER = "never"


class Action(StrEnum):
    DISCOUNTS = "discounts"
    SMS_PAYMENTS = "sms_payments"
    RISK_ADVANCE = "risk_advance"
    UDHAAR_REMINDERS = "udhaar_reminders"
    LOW_STOCK_ALERTS = "low_stock_alerts"


@dataclass(frozen=True, slots=True)
class Rule:
    levels: tuple[Level, ...]
    default: Level


RULES: dict[Action, Rule] = {
    Action.DISCOUNTS: Rule((Level.AUTO, Level.ASK, Level.NEVER), Level.AUTO),
    Action.SMS_PAYMENTS: Rule((Level.AUTO, Level.ASK), Level.AUTO),
    Action.RISK_ADVANCE: Rule((Level.AUTO, Level.NEVER), Level.AUTO),
    Action.UDHAAR_REMINDERS: Rule((Level.AUTO, Level.ASK, Level.NEVER), Level.ASK),
    Action.LOW_STOCK_ALERTS: Rule((Level.AUTO, Level.NEVER), Level.AUTO),
}


def level(tenant: Tenant, action: Action) -> Level:
    chosen = ((tenant.profile or {}).get(PROFILE_KEY) or {}).get(action)
    rule = RULES[action]
    return Level(chosen) if chosen in rule.levels else rule.default


def levels(tenant: Tenant) -> dict[str, str]:
    return {str(action): str(level(tenant, action)) for action in RULES}


def set_level(tenant: Tenant, action: str, wanted: str) -> dict[str, Any]:
    """The tenant's profile with the new level; refuses levels the action does not have."""
    try:
        rule, chosen = RULES[Action(action)], Level(wanted)
    except ValueError as exc:
        raise AppError("unknown_level", "No such action or level") from exc
    if chosen not in rule.levels:
        raise AppError("unknown_level", "This action cannot be set to that level")
    profile = dict(tenant.profile or {})
    profile[PROFILE_KEY] = {**(profile.get(PROFILE_KEY) or {}), action: chosen}
    return profile
