"""Payment methods and charging an order."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from tallyhub.core.clock import Clock, IdGenerator
from tallyhub.core.errors import PaymentDeclined
from tallyhub.core.events import EventBus, PaymentCaptured
from tallyhub.core.money import Money, sum_money
from tallyhub.core.registry import PAYMENT_METHODS
from tallyhub.customers.repository import CustomerRepository
from tallyhub.orders.models import Order
from tallyhub.orders.requests import PaymentInstruction
from tallyhub.storage.memory import Table


@dataclass
class Payment:
    id: str
    order_id: str
    method: str
    amount: Money
    captured_at: datetime
    reference: str = ""
    refunded: Optional[Money] = None
    voided: bool = False

    @property
    def refundable(self) -> Money:
        if self.voided:
            return Money.zero(self.amount.currency)
        return self.amount - (self.refunded or Money.zero(self.amount.currency))


@dataclass
class PaymentContext:
    customers: CustomerRepository
    card_processor: "FakeCardProcessor"
    gift_cards: Dict[str, Money] = field(default_factory=dict)


class PaymentMethod(ABC):
    name = "abstract"

    def __init__(self, ctx: PaymentContext) -> None:
        self.ctx = ctx

    @abstractmethod
    def capture(self, order: Order, amount: Money, token: Optional[str]) -> str:
        """Take the money; return a provider reference. Raise PaymentDeclined on failure."""

    @abstractmethod
    def refund(self, payment: Payment, amount: Money) -> None:
        ...

    def void(self, payment: Payment) -> None:
        self.refund(payment, payment.amount)


class FakeCardProcessor:
    """Stands in for a card acquirer. Token 'tok_decline' always fails."""

    def __init__(self) -> None:
        self.charges: List[tuple] = []
        self.refunds: List[tuple] = []

    def charge(self, token: str, amount: Money) -> str:
        if token == "tok_decline":
            raise PaymentDeclined("card declined")
        self.charges.append((token, amount))
        return f"ch_{len(self.charges):05d}"

    def refund(self, reference: str, amount: Money) -> None:
        self.refunds.append((reference, amount))


@PAYMENT_METHODS.register("card")
class CardPayment(PaymentMethod):
    name = "card"

    def capture(self, order: Order, amount: Money, token: Optional[str]) -> str:
        return self.ctx.card_processor.charge(token or "tok_visa", amount)

    def refund(self, payment: Payment, amount: Money) -> None:
        self.ctx.card_processor.refund(payment.reference, amount)


@PAYMENT_METHODS.register("store_credit")
class StoreCreditPayment(PaymentMethod):
    name = "store_credit"

    def capture(self, order: Order, amount: Money, token: Optional[str]) -> str:
        self.ctx.customers.debit_credit(order.customer_id, amount)
        return f"credit:{order.customer_id}"

    def refund(self, payment: Payment, amount: Money) -> None:
        self.ctx.customers.credit(payment.reference.split(":", 1)[1], amount)


@PAYMENT_METHODS.register("gift_card")
class GiftCardPayment(PaymentMethod):
    name = "gift_card"

    def capture(self, order: Order, amount: Money, token: Optional[str]) -> str:
        balance = self.ctx.gift_cards.get(token or "")
        if balance is None or balance < amount:
            raise PaymentDeclined("gift card balance is too low")
        self.ctx.gift_cards[token or ""] = balance - amount
        return f"gift:{token}"

    def refund(self, payment: Payment, amount: Money) -> None:
        number = payment.reference.split(":", 1)[1]
        self.ctx.gift_cards[number] = self.ctx.gift_cards.get(number, Money.zero(amount.currency)) + amount


@PAYMENT_METHODS.register("bank_transfer")
class BankTransferPayment(PaymentMethod):
    name = "bank_transfer"

    def capture(self, order: Order, amount: Money, token: Optional[str]) -> str:
        return f"iban:{token or 'pending'}"

    def refund(self, payment: Payment, amount: Money) -> None:
        pass  # paid out manually by finance


class PaymentService:
    def __init__(self, ctx: PaymentContext, payments: Table, ids: IdGenerator, clock: Clock, events: EventBus) -> None:
        self.ctx = ctx
        self.payments = payments
        self.ids = ids
        self.clock = clock
        self.events = events

    def method(self, name: str) -> PaymentMethod:
        return PAYMENT_METHODS.get(name)(self.ctx)

    def for_order(self, order_id: str) -> List[Payment]:
        return sorted(self.payments.where(lambda p: p.order_id == order_id), key=lambda p: p.id)

    def charge(self, order: Order, instructions: List[PaymentInstruction]) -> List[Payment]:
        """Capture explicit amounts first and the remainder last; void everything if any capture fails."""
        total = order.totals.grand_total
        explicit = [i for i in instructions if i.amount is not None]
        remainder = [i for i in instructions if i.amount is None]
        covered = sum_money([i.amount for i in explicit], total.currency)  # type: ignore[misc]
        if total < covered:
            raise PaymentDeclined(f"payments of {covered} exceed the order total {total}")
        plan = [(i, i.amount) for i in explicit]
        left = total - covered
        if remainder:
            plan.append((remainder[0], left))
        elif not left.is_zero():
            raise PaymentDeclined(f"{left} of the order total is not covered by any payment")

        captured: List[Payment] = []
        try:
            for instruction, amount in plan:
                if amount is None or amount.is_zero():
                    continue
                reference = self.method(instruction.method).capture(order, amount, instruction.token)
                payment = Payment(self.ids.next("pay"), order.id, instruction.method, amount,
                                  self.clock.now(), reference)
                self.payments.put(payment)
                captured.append(payment)
        except PaymentDeclined:
            for payment in captured:
                self.method(payment.method).void(payment)
                payment.voided = True
            raise
        for payment in captured:
            order.payment_ids.append(payment.id)
            self.events.publish(PaymentCaptured(order.id, payment.id))
        return captured
