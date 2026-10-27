"""What the customer reads when money or stock is at stake: written by code from the
database, in the customer's language, never by a model. Each card's buttons carry the id of
the action they confirm, so an old card can never place a newer cart.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from chatfront.channels.types import Button, OutButtons
from chatfront.core.countries import shop_money
from chatfront.core.i18n import t
from chatfront.core.models import Tenant
from chatfront.orders.delivery import Quote
from chatfront.orders.models import Order, OrderItem
from chatfront.owner.actions import button_id
from chatfront.owner.models import PendingAction
from chatfront.payments.messages import pay_details


@dataclass(frozen=True)
class CardMaker:
    tenant: Tenant
    lang: str

    def money(self, amount: int) -> str:
        return shop_money(self.tenant, amount, self.lang)

    def line(self, item: OrderItem) -> str:
        options = " ".join(str(v) for v in item.options.values())
        name = f"{item.name} ({options})" if options else item.name
        return t(
            "order.line",
            self.lang,
            quantity=item.quantity,
            name=name,
            total=self.money(item.line_total),
        )

    def totals(self, order: Order, charge: Quote | None) -> list[str]:
        lines = [t("order.subtotal", self.lang, amount=self.money(order.subtotal))]
        if charge is not None:
            if charge.fee is None:
                lines.append(t("order.delivery_tbc", self.lang, city=charge.city))
            elif charge.fee == 0:
                lines.append(t("order.delivery_free", self.lang, city=charge.city))
            else:
                amount = self.money(charge.fee)
                lines.append(t("order.delivery", self.lang, city=charge.city, amount=amount))
        lines.append(t("order.total", self.lang, amount=self.money(order.total)))
        return lines

    def checkout(
        self,
        action: PendingAction,
        order: Order,
        items: Sequence[OrderItem],
        method: str,
        charge: Quote | None,
        advance: int = 0,
    ) -> OutButtons:
        address = order.address or {}
        text = "\n".join(
            [
                t("order.summary_title", self.lang),
                *(f"- {self.line(item)}" for item in items),
                *self.totals(order, charge),
                t(
                    "order.address",
                    self.lang,
                    address=f"{address.get('address', '')}, {address.get('city', '')}",
                ),
                t("order.pay_cod_advance", self.lang, advance=self.money(advance))
                if advance
                else t(f"order.pay_{method}", self.lang),
            ]
        )
        return OutButtons(
            text,
            (
                Button(button_id(action, "yes"), t("order.place", self.lang)),
                Button(button_id(action, "no"), t("order.not_yet", self.lang)),
            ),
        )

    def confirm_cancel(self, action: PendingAction, order: Order) -> OutButtons:
        return OutButtons(
            t("order.cancel_confirm", self.lang, code=order.code, total=self.money(order.total)),
            (
                Button(button_id(action, "yes"), t("order.cancel_yes", self.lang)),
                Button(button_id(action, "no"), t("order.keep", self.lang)),
            ),
        )

    def confirm_change(
        self, action: PendingAction, order: Order, item: OrderItem, quantity: int
    ) -> OutButtons:
        return OutButtons(
            t(
                "order.change_confirm",
                self.lang,
                code=order.code,
                name=item.name,
                old=item.quantity,
                new=quantity,
            ),
            (
                Button(button_id(action, "yes"), t("order.change_yes", self.lang)),
                Button(button_id(action, "no"), t("order.keep", self.lang)),
            ),
        )

    def placed(self, order: Order) -> str:
        """The order is placed: what to pay now, where, and how to make it easy to match."""
        total = self.money(order.total)
        if order.payment_method == "credit":
            return t("order.placed_credit", self.lang, code=order.code, total=total)
        if order.due_now is None:
            return t("order.placed_cod", self.lang, code=order.code, total=total)
        values = {
            "code": order.code,
            "total": total,
            "due": self.money(order.due_now),
            "account": pay_details(self.tenant, self.lang),
        }
        if order.payment_method == "cod":
            rest = self.money(order.total - order.due_now)
            text = t("order.placed_advance", self.lang, rest=rest, **values)
        else:
            text = t("order.placed_transfer", self.lang, **values)
        if order.unique_off:
            text += "\n" + t("order.unique_note", self.lang, off=self.money(order.unique_off))
        return text
