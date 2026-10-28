"""The call and voice-note guardian (feature 39): a recording or a forwarded voice note from
someone claiming to be a courier, JazzCash, a bank or the FIA, heard for the usual tricks.

Spark Open listens and says what the caller claimed to be and asked for: a code, a PIN, money,
an app to install, and whether they threatened or hurried. Code decides: asking for a code or a
PIN is always a scam, as is an official-sounding caller who wants money or an app or makes
threats; the rest is suspicious or shows no signs. The owner hears the verdict with the rules
that keep them safe, and can report the numbers and accounts the caller gave, which Chatfront
reviews before they go in the report for NCCIA and CERT (feature 47).
"""

from __future__ import annotations

import re
import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Role, TextPart
from chatfront.audit.service import Actor, record
from chatfront.channels.types import Button, Outbound, OutButtons, OutText
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.media import service as media_service
from chatfront.media.models import Media
from chatfront.shield.models import ScamKind, ScamReport

if TYPE_CHECKING:
    from chatfront.media.storage import Storage

PROMPT = "call_guardian"
BUTTON = "sr:"  # sr:<kind>:<posing as>:<value>
Posing = Literal[
    "courier",
    "jazzcash",
    "easypaisa",
    "bank",
    "fia",
    "police",
    "meta",
    "government",
    "family",
    "other",
    "none",
]
OFFICIAL = frozenset(
    {"courier", "jazzcash", "easypaisa", "bank", "fia", "police", "meta", "government"}
)
MAX_VALUE = 120
_PHONE = re.compile(r"^\+?[\d\s()-]{8,20}$")


class CallReading(BaseModel):
    posing_as: Posing = "none"
    asks_for_code: bool = False  # a one-time code from an SMS
    asks_for_pin_or_password: bool = False
    asks_for_money: bool = False
    asks_to_install_app: bool = False
    threat_or_hurry: bool = False
    numbers: list[str] = Field(default_factory=list, max_length=5)  # numbers and accounts said
    summary: str = Field(default="", max_length=300)


class Verdict(StrEnum):
    SCAM = "scam"
    SUSPICIOUS = "suspicious"
    NO_SIGNS = "no_signs"


def judge(reading: CallReading) -> Verdict:
    if reading.asks_for_code or reading.asks_for_pin_or_password:
        return Verdict.SCAM
    official = reading.posing_as in OFFICIAL
    pressing = reading.asks_for_money or reading.asks_to_install_app or reading.threat_or_hurry
    if official and pressing:
        return Verdict.SCAM
    if official or pressing:
        return Verdict.SUSPICIOUS
    return Verdict.NO_SIGNS


def kind_of(value: str) -> ScamKind:
    return ScamKind.PHONE if _PHONE.match(value.strip()) else ScamKind.WALLET


async def listen(ai: AiClient, storage: Storage, tenant_id: uuid.UUID, media: Media) -> CallReading:
    prompt = load_prompt(PROMPT)
    request = ChatRequest(
        role=Role.AUDIO,
        messages=[
            ChatMessage("system", prompt.render()),
            ChatMessage(
                "user",
                [TextPart("The recording:"), await media_service.model_part(storage, media)],
            ),
        ],
        tenant_id=tenant_id,
        prompt=prompt.ref,
        ref=("media", str(media.id)),
    )
    reading, _ = await ai.complete_json(request, CallReading)
    return reading


def _rules(verdict: Verdict, lang: str) -> list[str]:
    if verdict == Verdict.NO_SIGNS:
        return [t("guardian.rule_check", lang)]
    return [t("guardian.rule_code", lang), t("guardian.rule_call_back", lang)]


def reply(reading: CallReading, lang: str) -> list[Outbound]:
    """The verdict for the owner, and a button to report each number or account given."""
    verdict = judge(reading)
    posing = t(f"guardian.posing.{reading.posing_as}", lang)
    lines = [t(f"guardian.{verdict}", lang, posing=posing), *_rules(verdict, lang)]
    if reading.summary:
        lines.insert(1, t("guardian.heard", lang, summary=reading.summary))
    text = "\n".join(lines)
    values = [v.strip()[:MAX_VALUE] for v in reading.numbers if v.strip()][:3]
    if verdict == Verdict.NO_SIGNS or not values:
        return [OutText(text)]
    buttons = tuple(
        Button(
            f"{BUTTON}{kind_of(v)}:{reading.posing_as}:{v}",
            t("guardian.report", lang, value=v[-12:]),
        )
        for v in values
    )
    return [OutButtons(text, buttons)]


def parse_button(button_id: str | None) -> tuple[ScamKind, str, str] | None:
    if not button_id or not button_id.startswith(BUTTON):
        return None
    parts = button_id[len(BUTTON) :].split(":", 2)
    if len(parts) != 3 or parts[0] not in tuple(ScamKind) or not parts[2].strip():
        return None
    return ScamKind(parts[0]), parts[1][:20], parts[2].strip()[:MAX_VALUE]


async def report(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    kind: ScamKind,
    value: str,
    posing_as: str,
    source: str,
    actor: Actor,
    *,
    staff_id: uuid.UUID | None = None,
    evidence: dict[str, object] | None = None,
) -> ScamReport:
    """Keep a scammer's number, account or link once per shop, for Chatfront's review."""
    plain = " ".join(value.split())[: MAX_VALUE if kind != ScamKind.URL else 300]
    if not plain:
        raise AppError("empty_report", "Give the number, account or link")
    found = await session.scalar(
        select(ScamReport).where(ScamReport.kind == kind, ScamReport.value == plain)
    )
    if found is not None:
        return found
    found = ScamReport(
        tenant_id=tenant_id,
        kind=kind,
        value=plain,
        posing_as=posing_as or "none",
        source=source,
        evidence=evidence or {},
        reported_by=staff_id,
    )
    session.add(found)
    await session.flush()
    await record(
        session,
        tenant_id,
        actor,
        "scam.reported",
        entity=("scam_report", str(found.id)),
        data={"kind": kind, "posing_as": found.posing_as, "source": source},
    )
    return found
