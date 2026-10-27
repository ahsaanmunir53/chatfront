"""What the customer and the owner read about money, written by code in their language."""

from __future__ import annotations

from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.orders.models import Order
from chatfront.payments.models import PaymentClaim, Receipt, WalletCredit

DASH = "—"


def receipt_url(web_url: str, receipt: Receipt) -> str:
    return f"{web_url}/r/{receipt.code}"


def paid_text(tenant: Tenant, order: Order, receipt: Receipt, lang: str, web_url: str) -> str:
    """The customer's message when money is settled: what arrived, what is left, the receipt."""
    amount = shop_money(tenant, int(receipt.payload["amount"]), lang)
    balance = max(order.total - order.paid, 0)
    if order.payment_method == "cod":
        head = t(
            "payment.received_advance",
            lang,
            amount=amount,
            code=order.code,
            rest=shop_money(tenant, balance, lang),
        )
    elif balance:
        head = t(
            "payment.received_part",
            lang,
            amount=amount,
            code=order.code,
            balance=shop_money(tenant, balance, lang),
        )
    else:
        head = t("payment.received_full", lang, amount=amount, code=order.code)
    return f"{head}\n{t('payment.receipt', lang, url=receipt_url(web_url, receipt))}"


def pay_details(tenant: Tenant, lang: str) -> str:
    """Where to send money: the owner's own words, else a note that they will follow."""
    written = str((tenant.profile or {}).get("payment") or "").strip()
    return written or t("order.account_later", lang)


def evidence_lines(
    tenant: Tenant,
    order: Order,
    claim: PaymentClaim,
    credit: WalletCredit | None,
    lang: str,
) -> list[str]:
    """The facts behind a payment the owner is asked to approve, one per line."""

    def money(amount: int | None) -> str:
        return shop_money(tenant, amount, lang) if amount is not None else DASH

    lines = [t("payment.asked", lang, amount=money(order.due_now or order.total - order.paid))]
    if claim.media_id is not None:
        lines.append(
            t(
                "payment.screenshot",
                lang,
                amount=money(claim.amount),
                provider=claim.provider or DASH,
                tid=claim.transaction_id or DASH,
            )
        )
    if credit is not None:
        lines.append(
            t("payment.sms_match", lang, amount=money(credit.amount), tid=credit.transaction_id)
        )
    else:
        lines.append(t("payment.no_sms", lang))
    for signal in claim.signals:
        lines.append(t(f"payment.signal.{signal['code']}", lang, detail=signal.get("detail", "")))
    return lines
