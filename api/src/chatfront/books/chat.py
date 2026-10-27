"""What the owner tells the books in their own chat (features 48 and 49): a photo of a bill, a
voice note ("aaj 25 hazaar kiraya diya"), or a typed line ("Bilal ko 3000 udhaar").

Spark Open reads the bill or the words; code turns the reading into exact entries, finds the
udhaar account, refuses a payback larger than what is owed, and shows a card. Nothing reaches
the ledger until the owner taps Save.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from chatfront.ai.client import AiClient
from chatfront.ai.prompts import load_prompt
from chatfront.ai.types import ChatMessage, ChatRequest, Part, Role, TextPart
from chatfront.audit.service import Actor
from chatfront.books import ledger, udhaar
from chatfront.books.models import Category, EntryKind, UdhaarAccount
from chatfront.catalog.drafts import ExpenseCategory, MoneyLine, Strict, price_from_words, to_minor
from chatfront.core.countries import shop_money
from chatfront.core.errors import AppError
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.owner.types import Problem

KIND_MONEY = "money"
OWNER_SOURCES = ("bill", "voice", "chat")

# A photo whose caption has one of these words is a bill to spend from, not a product to sell.
BILL_WORDS = frozenset(
    (
        *("bill", "bills", "receipt", "invoice", "expense", "kharcha", "kharch", "kharchay"),
        *(
            "بل",
            "بِل",
            "رسید",
            "خرچہ",
            "خرچ",
            "اخراجات",
            "बिल",
            "खर्च",
            "বিল",
            "রসিদ",
            "খরচ",
            "فاتورة",
        ),
    )
)


class BillReading(Strict):
    total: float | None
    vendor: str | None
    category: ExpenseCategory | None
    note: str


def is_bill(words: str) -> bool:
    return any(token.strip(".,:;!?-").casefold() in BILL_WORDS for token in words.split())


async def read_bill(
    ai: AiClient,
    *,
    tenant_id: uuid.UUID,
    context: dict[str, str],
    media: Sequence[Part],
    words: str,
    lang: str,
) -> BillReading:
    prompt = load_prompt("read_bill")
    request = ChatRequest(
        role=Role.VISION,
        messages=[
            ChatMessage("system", prompt.render(**context)),
            ChatMessage("user", [*media, TextPart(words or "(no words, only the bill)")]),
        ],
        tenant_id=tenant_id,
        prompt=prompt.ref,
        lang=lang,
    )
    reading, _ = await ai.complete_json(request, BillReading)
    return reading


def _category(value: str | None) -> str:
    return value if value in set(Category) - {Category.SALES} else Category.OTHER


def bill_op(
    reading: BillReading | None, words: str, digits: int, lang: str
) -> dict[str, Any] | Problem:
    """The expense a bill photo becomes. The owner's typed amount wins over what the model read
    on the bill; with neither, the owner is asked."""
    typed = price_from_words(words, lang)
    value = typed if typed is not None else (reading.total if reading is not None else None)
    amount = to_minor(value, digits) if value else None
    if amount is None:
        return ("books.need_amount", {})
    description = (reading.vendor or reading.note) if reading is not None else ""
    return {
        "op": EntryKind.EXPENSE.value,
        "amount": amount,
        "category": _category(reading.category if reading else None),
        "description": (description or "").strip()[:200],
    }


async def _one_account(
    session: AsyncSession, name: str
) -> tuple[UdhaarAccount | None, Problem | None]:
    found = await udhaar.find(session, name)
    if len(found) > 1:
        options = ", ".join(account.name for account in found[:3])
        return None, ("books.which_account", {"options": options})
    return (found[0] if found else None), None


async def resolve_money(
    session: AsyncSession, lines: Sequence[MoneyLine], digits: int
) -> tuple[list[dict[str, Any]], list[Problem]]:
    """Exact entries from what the owner said, or what to ask them."""
    ops: list[dict[str, Any]] = []
    problems: list[Problem] = []
    for line in lines:
        amount = to_minor(line.amount, digits) if line.amount else None
        if amount is None:
            problems.append(("books.need_amount", {}))
            continue
        if line.kind == "expense":
            ops.append(
                {
                    "op": EntryKind.EXPENSE.value,
                    "amount": amount,
                    "category": _category(line.category),
                    "description": (line.note or "").strip()[:200],
                }
            )
            continue
        name = " ".join((line.person or "").split())[:120]
        if not name:
            problems.append(("books.need_name", {}))
            continue
        account, problem = await _one_account(session, name)
        if problem is not None:
            problems.append(problem)
            continue
        if line.kind == "udhaar_paid" and account is None:
            problems.append(("books.no_account", {"name": name}))
            continue
        if line.kind == "udhaar_paid" and account is not None and amount > account.balance:
            problems.append(
                ("books.more_than_owed", {"name": account.name, "balance": account.balance})
            )
            continue
        ops.append(
            {
                "op": line.kind,
                "name": account.name if account else name,
                "account_id": str(account.id) if account else None,
                "amount": amount,
                "balance": account.balance if account else 0,
            }
        )
    return ops, problems


def problem_text(tenant: Tenant, problem: Problem, lang: str) -> str:
    key, values = problem
    if "balance" in values:
        values = values | {"balance": shop_money(tenant, values["balance"], lang)}
    return t(key, lang, **values)


def money_line(tenant: Tenant, op: dict[str, Any], lang: str) -> str:
    amount = shop_money(tenant, op["amount"], lang)
    if op["op"] == EntryKind.EXPENSE:
        category = t(f"books.category.{op['category']}", lang)
        what = f"{category}: {op['description']}" if op["description"] else category
        return t("books.card_expense", lang, amount=amount, what=what)
    sign = 1 if op["op"] == EntryKind.UDHAAR_GIVEN else -1
    after = shop_money(tenant, op["balance"] + sign * op["amount"], lang)
    return t(f"books.card_{op['op']}", lang, name=op["name"], amount=amount, after=after)


def money_card_text(tenant: Tenant, ops: Sequence[dict[str, Any]], lang: str) -> str:
    lines = "\n".join(f"- {money_line(tenant, op, lang)}" for op in ops)
    return t("books.preview", lang, lines=lines)


async def apply_money(
    session: AsyncSession,
    tenant: Tenant,
    ops: Sequence[dict[str, Any]],
    actor: Actor,
    *,
    source: str,
    media_id: uuid.UUID | None,
) -> int:
    """Write what the owner saved. Each entry is checked again as it lands; a payback that is
    now more than owed stops the whole card."""
    for op in ops:
        if op["op"] == EntryKind.EXPENSE:
            await ledger.post(
                session,
                tenant,
                EntryKind.EXPENSE,
                int(op["amount"]),
                actor,
                category=Category(op["category"]),
                source=source,
                description=op["description"],
                media_id=media_id,
            )
            continue
        account = (
            await session.get(UdhaarAccount, uuid.UUID(op["account_id"]))
            if op["account_id"]
            else await udhaar.open_account(session, tenant, op["name"])
        )
        if account is None:
            raise AppError("no_account", "That udhaar account is gone", status_code=409)
        move = udhaar.give if op["op"] == EntryKind.UDHAAR_GIVEN else udhaar.receive
        await move(session, tenant, account, int(op["amount"]), actor, source=source)
    return len(ops)
