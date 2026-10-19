"""Customer lookup and store-credit bookkeeping."""

from __future__ import annotations

from typing import Dict, List

from tallyhub.core.errors import NotFound, PaymentDeclined
from tallyhub.core.money import Money
from tallyhub.customers.models import Customer


class CustomerRepository:
    def __init__(self) -> None:
        self._customers: Dict[str, Customer] = {}

    def add(self, customer: Customer) -> None:
        self._customers[customer.id] = customer

    def get(self, customer_id: str) -> Customer:
        try:
            return self._customers[customer_id]
        except KeyError:
            raise NotFound("customer", customer_id) from None

    def all(self) -> List[Customer]:
        return sorted(self._customers.values(), key=lambda c: c.id)

    def debit_credit(self, customer_id: str, amount: Money) -> None:
        customer = self.get(customer_id)
        balance = customer.store_credit or Money.zero(amount.currency)
        if balance < amount:
            raise PaymentDeclined(f"store credit balance {balance} is below {amount}")
        customer.store_credit = balance - amount

    def credit(self, customer_id: str, amount: Money) -> None:
        customer = self.get(customer_id)
        balance = customer.store_credit or Money.zero(amount.currency)
        customer.store_credit = balance + amount
