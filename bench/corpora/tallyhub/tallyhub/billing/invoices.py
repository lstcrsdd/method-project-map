"""Invoices generated from placed orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from tallyhub.billing.numbering import InvoiceNumberer
from tallyhub.catalog.repository import CatalogRepository
from tallyhub.core.clock import Clock
from tallyhub.core.money import Money
from tallyhub.orders.models import Order, OrderTotals
from tallyhub.storage.memory import Table


@dataclass(frozen=True)
class InvoiceLine:
    sku: str
    description: str
    quantity: int
    unit_price: Money
    net: Money
    tax: Money


@dataclass
class Invoice:
    number: str
    order_id: str
    customer_id: str
    issued_at: datetime
    currency: str
    lines: List[InvoiceLine] = field(default_factory=list)
    totals: OrderTotals = None  # type: ignore[assignment]
    tax_note: str = ""


class InvoiceService:
    def __init__(self, numberer: InvoiceNumberer, catalog: CatalogRepository, invoices: Table, clock: Clock) -> None:
        self.numberer = numberer
        self.catalog = catalog
        self.invoices = invoices
        self.clock = clock

    def describe(self, sku: str) -> str:
        bundle = self.catalog.bundle(sku)
        if bundle is not None:
            return bundle.name
        product = self.catalog.find_product(sku)
        return product.name if product else sku

    def issue(self, order: Order) -> Invoice:
        invoice = Invoice(
            number=self.numberer.next(),
            order_id=order.id,
            customer_id=order.customer_id,
            issued_at=self.clock.now(),
            currency=order.currency,
            totals=order.totals,
        )
        for line in order.lines:
            invoice.lines.append(
                InvoiceLine(line.sku, self.describe(line.sku), line.quantity, line.unit_price, line.net, line.tax)
            )
        if order.tax_exempt:
            invoice.tax_note = f"Tax exempt (certificate {order.tax_exempt_id})"
        self.invoices.put(invoice)
        return invoice

    def for_order(self, order_id: str) -> List[Invoice]:
        return self.invoices.where(lambda inv: inv.order_id == order_id)
