"""Turns domain events into customer messages."""

from __future__ import annotations

from typing import List

from tallyhub.core.events import EventBus, OrderCancelled, OrderPlaced, OrderShipped, RefundIssued
from tallyhub.core.money import Money
from tallyhub.core.registry import NOTIFICATION_CHANNELS
from tallyhub.core.settings import Settings
from tallyhub.customers.repository import CustomerRepository
from tallyhub.notifications import channels  # noqa: F401  (register channels)
from tallyhub.notifications.channels import Message
from tallyhub.notifications.formatting import format_date, format_money
from tallyhub.notifications.templates import render
from tallyhub.storage.memory import Table


class Notifier:
    def __init__(self, settings: Settings, customers: CustomerRepository, orders: Table, refunds: Table) -> None:
        self.settings = settings
        self.customers = customers
        self.orders = orders
        self.refunds = refunds
        self.outbox: List[Message] = []

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(OrderPlaced, self.on_order_placed)
        bus.subscribe(OrderShipped, self.on_order_shipped)
        bus.subscribe(RefundIssued, self.on_refund_issued)
        bus.subscribe(OrderCancelled, self.on_order_cancelled)

    def _send(self, customer_id: str, template: str, **values) -> None:
        customer = self.customers.get(customer_id)
        content = render(template, name=customer.name, **values)
        for name in self.settings.get("notifications.channels", ["email"]):
            NOTIFICATION_CHANNELS.get(name)(self.outbox).send(customer, content)

    def on_order_placed(self, event: OrderPlaced) -> None:
        order = self.orders.get(event.order_id)
        locale = self.customers.get(order.customer_id).locale
        totals = order.totals
        discount_line = ""
        if not totals.discount.is_zero():
            discount_line = f"You saved {format_money(-totals.discount, locale)}"
        self._send(
            order.customer_id,
            "order_confirmation",
            order_id=order.id,
            date=format_date(order.created_at, locale),
            item_count=sum(line.quantity for line in order.lines),
            subtotal=format_money(totals.subtotal, locale),
            shipping=format_money(totals.shipping, locale),
            tax=format_money(totals.tax, locale),
            total=format_money(totals.grand_total, locale),
            discount_line=discount_line,
        )

    def on_order_shipped(self, event: OrderShipped) -> None:
        order = self.orders.get(event.order_id)
        self._send(order.customer_id, "order_shipped", order_id=order.id, carrier=event.carrier,
                   tracking=event.tracking)

    def on_refund_issued(self, event: RefundIssued) -> None:
        order = self.orders.get(event.order_id)
        refund = self.refunds.get(event.refund_id)
        locale = self.customers.get(order.customer_id).locale
        parts = "\n".join(f"- {method}: {format_money(amount, locale)}" for _pid, method, amount in refund.parts)
        self._send(order.customer_id, "refund_issued", order_id=order.id,
                   total=format_money(refund.total, locale), parts=parts)

    def on_order_cancelled(self, event: OrderCancelled) -> None:
        order = self.orders.get(event.order_id)
        self._send(order.customer_id, "order_cancelled", order_id=order.id, reason=event.reason)
