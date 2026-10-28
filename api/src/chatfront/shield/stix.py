"""The scam report for NCCIA and CERT (feature 47), as a STIX 2.1 bundle.

Only reports Chatfront confirmed go in: the scammers' phone numbers and wallet or bank
accounts as user accounts, and fake login or support links as URLs, each an indicator of
malicious activity with what it posed as. Customers are never in it. Ids are derived from the
values, so the same scam keeps its id from one report to the next and a reader can merge them.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from chatfront.shield.models import ScamKind, ScamReport

NAMESPACE = uuid.UUID("2f7a7c1e-4d5b-4e8a-9c41-6a3f0b9d2e17")  # Chatfront's, for STIX ids
IDENTITY = f"identity--{uuid.uuid5(NAMESPACE, 'chatfront')}"
NAMES = {
    ScamKind.PHONE: "Scam phone number",
    ScamKind.WALLET: "Account used to collect scam money",
    ScamKind.URL: "Fake login or support link",
}


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _quoted(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def pattern(report: ScamReport) -> str:
    value = _quoted(report.value)
    if report.kind == ScamKind.URL:
        return f"[url:value = '{value}']"
    account_type = "phone-number" if report.kind == ScamKind.PHONE else report.posing_as
    return (
        f"[user-account:account_login = '{value}' AND "
        f"user-account:account_type = '{_quoted(account_type)}']"
    )


def indicator(report: ScamReport, now: str) -> dict[str, Any]:
    first = _stamp(report.reviewed_at or report.created_at)
    return {
        "type": "indicator",
        "spec_version": "2.1",
        "id": f"indicator--{uuid.uuid5(NAMESPACE, f'{report.kind}:{report.value}')}",
        "created": first,
        "modified": now,
        "created_by_ref": IDENTITY,
        "name": NAMES[ScamKind(report.kind)],
        "description": f"Posed as: {report.posing_as}. Reported by a shop through {report.source}.",
        "indicator_types": ["malicious-activity"],
        "pattern": pattern(report),
        "pattern_type": "stix",
        "valid_from": first,
        "labels": [report.kind, report.posing_as],
    }


def bundle(reports: Sequence[ScamReport], now: datetime) -> dict[str, Any]:
    stamp = _stamp(now)
    seen: dict[str, dict[str, Any]] = {}
    for report in reports:
        found = indicator(report, stamp)
        seen.setdefault(found["id"], found)  # one indicator however many shops reported it
    identity = {
        "type": "identity",
        "spec_version": "2.1",
        "id": IDENTITY,
        "created": "2026-10-27T00:00:00.000Z",
        "modified": "2026-10-27T00:00:00.000Z",
        "name": "Chatfront",
        "identity_class": "system",
        "description": "Scams reported by small shops using Chatfront, reviewed before sharing.",
    }
    return {
        "type": "bundle",
        "id": f"bundle--{uuid.uuid5(NAMESPACE, stamp + ','.join(sorted(seen)))}",
        "objects": [identity, *seen.values()],
    }
