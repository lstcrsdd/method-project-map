"""Price breakdown produced by the pricing engine and consumed by tax, totals and invoices."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

from tallyhub.core.money import Money, sum_money


@dataclass(frozen=True)
class Adjustment:
    """A price change made by one rule. Discounts are negative amounts."""

    rule: str
    label: str
    amount: Money
    code: Optional[str] = None


@dataclass
class LineBreakdown:
    sku: str
    quantity: int
    unit_price: Money
    tax_category: str
    adjustments: List[Adjustment] = field(default_factory=list)
    is_bundle: bool = False

    @property
    def gross(self) -> Money:
        return self.unit_price * self.quantity

    @property
    def net(self) -> Money:
        return sum_money([self.gross] + [a.amount for a in self.adjustments], self.unit_price.currency)


@dataclass
class PriceBreakdown:
    currency: str
    lines: List[LineBreakdown] = field(default_factory=list)
    order_adjustments: List[Adjustment] = field(default_factory=list)
    applied_codes: Set[str] = field(default_factory=set)
    notes: List[str] = field(default_factory=list)

    def add_order_adjustment(self, adjustment: Adjustment) -> None:
        self.order_adjustments.append(adjustment)
        if adjustment.code:
            self.applied_codes.add(adjustment.code)

    def has_adjustment_from(self, rule: str) -> bool:
        if any(a.rule == rule for a in self.order_adjustments):
            return True
        return any(a.rule == rule for line in self.lines for a in line.adjustments)

    @property
    def lines_net(self) -> Money:
        return sum_money([line.net for line in self.lines], self.currency)

    @property
    def order_discount(self) -> Money:
        return sum_money([a.amount for a in self.order_adjustments], self.currency)

    @property
    def discount_total(self) -> Money:
        line_discounts = sum_money([a.amount for line in self.lines for a in line.adjustments], self.currency)
        return (line_discounts + self.order_discount).rounded()

    @property
    def subtotal(self) -> Money:
        """Merchandise total after all discounts, rounded to the currency."""
        return (self.lines_net + self.order_discount).rounded()

    def line_nets_after_order_discounts(self) -> List[Money]:
        """Each line's share of the subtotal; order-level discounts are spread by line value.

        The parts are rounded and add up exactly to `subtotal`.
        """
        if not self.lines:
            return []
        weights = [max(line.net.amount, 0) for line in self.lines]
        if sum(weights) == 0:
            return [Money.zero(self.currency) for _ in self.lines]
        return self.subtotal.allocate(weights)
