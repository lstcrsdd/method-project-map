"""Order totals and per-line amounts from pricing, shipping and tax results."""

from __future__ import annotations

from typing import List

from tallyhub.core.money import Money, sum_money
from tallyhub.orders.models import OrderLine, OrderTotals
from tallyhub.pricing.breakdown import PriceBreakdown
from tallyhub.tax.calculator import TaxResult


def compute_totals(breakdown: PriceBreakdown, shipping: Money, tax: TaxResult) -> OrderTotals:
    currency = breakdown.currency
    merchandise = sum_money([line.gross for line in breakdown.lines], currency).rounded()
    subtotal = breakdown.subtotal
    discount = subtotal - merchandise
    tax_total = tax.total
    return OrderTotals(
        merchandise=merchandise,
        discount=discount,
        subtotal=subtotal,
        shipping=shipping,
        tax=tax_total,
        grand_total=subtotal + shipping + tax_total,
    )


def build_lines(breakdown: PriceBreakdown, tax: TaxResult) -> List[OrderLine]:
    nets = breakdown.line_nets_after_order_discounts()
    tax_by_sku = {line.sku: line.tax for line in tax.lines}
    zero = Money.zero(breakdown.currency)
    return [
        OrderLine(
            sku=line.sku,
            quantity=line.quantity,
            unit_price=line.unit_price,
            net=net,
            tax=tax_by_sku.get(line.sku, zero),
        )
        for line, net in zip(breakdown.lines, nets)
    ]
