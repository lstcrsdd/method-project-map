"""Refunds: returning money to the payment methods an order was paid with."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from tallyhub.billing.payments import Payment, PaymentService
from tallyhub.core.clock import Clock, IdGenerator
from tallyhub.core.errors import RefundError
from tallyhub.core.events import EventBus, RefundIssued
from tallyhub.core.money import Money, sum_money
from tallyhub.core.settings import Settings
from tallyhub.orders import state
from tallyhub.orders.models import Order
from tallyhub.storage.memory import Table


@dataclass
class Refund:
    id: str
    order_id: str
    total: Money
    reason: str
    created_at: datetime
    parts: List[Tuple[str, str, Money]] = field(default_factory=list)  # (payment id, method, amount)


class RefundService:
    def __init__(
        self,
        settings: Settings,
        payments: PaymentService,
        orders: Table,
        refunds: Table,
        ids: IdGenerator,
        clock: Clock,
        events: EventBus,
    ) -> None:
        self.settings = settings
        self.payments = payments
        self.orders = orders
        self.refunds = refunds
        self.ids = ids
        self.clock = clock
        self.events = events

    def refund_priority(self, payment: Payment) -> int:
        order = self.settings.get("billing.refund_order", [])
        return order.index(payment.method) if payment.method in order else len(order)

    def refundable(self, order: Order) -> Money:
        return sum_money([p.refundable for p in self.payments.for_order(order.id)], order.currency)

    def refund(self, order_id: str, amount: Optional[Money] = None, reason: str = "") -> Refund:
        order: Order = self.orders.get(order_id)
        if order.status not in state.REFUNDABLE:
            raise RefundError(f"order {order_id} cannot be refunded in status {order.status}")
        available = self.refundable(order)
        amount = amount or available
        if amount.currency != order.currency:
            raise RefundError(f"refund must be in {order.currency}")
        if amount.is_zero() or amount.is_negative():
            raise RefundError("refund amount must be positive")
        if available < amount:
            raise RefundError(f"only {available} can still be refunded")

        refund = Refund(self.ids.next("ref"), order.id, amount, reason, self.clock.now())
        left = amount
        for payment in sorted(self.payments.for_order(order.id), key=self.refund_priority):
            if left.is_zero():
                break
            share = payment.refundable if payment.refundable < left else left
            if share.is_zero():
                continue
            self.payments.method(payment.method).refund(payment, share)
            payment.refunded = (payment.refunded or Money.zero(order.currency)) + share
            refund.parts.append((payment.id, payment.method, share))
            left = left - share

        self.refunds.put(refund)
        fully = self.refundable(order).is_zero()
        state.transition(order, state.REFUNDED if fully else state.PARTIALLY_REFUNDED, self.clock.now())
        self.events.publish(RefundIssued(order.id, refund.id, {m: str(a.amount) for _p, m, a in refund.parts}))
        return refund
