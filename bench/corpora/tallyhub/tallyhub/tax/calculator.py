"""Sales tax on priced orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from tallyhub.core.money import Money, sum_money
from tallyhub.core.settings import Settings
from tallyhub.customers.models import Customer
from tallyhub.orders.models import Address
from tallyhub.pricing.breakdown import PriceBreakdown
from tallyhub.tax.exemptions import ExemptionPolicy
from tallyhub.tax.jurisdictions import JurisdictionResolver


@dataclass(frozen=True)
class LineTax:
    sku: str
    taxable: Money
    rate: Decimal
    tax: Money


@dataclass
class TaxResult:
    currency: str
    jurisdiction: Optional[str]
    lines: List[LineTax] = field(default_factory=list)
    shipping_tax: Optional[Money] = None
    exempt: bool = False
    reason: str = ""

    @property
    def total(self) -> Money:
        parts = [line.tax for line in self.lines]
        if self.shipping_tax is not None:
            parts.append(self.shipping_tax)
        return sum_money(parts, self.currency)


class TaxCalculator:
    def __init__(self, settings: Settings, resolver: JurisdictionResolver, exemptions: ExemptionPolicy) -> None:
        self.settings = settings
        self.resolver = resolver
        self.exemptions = exemptions

    def _tax(self, base: Money, rate: Decimal) -> Money:
        mode = self.settings.rounding_mode(base.currency)
        return Money.raw(base.amount * rate, base.currency).rounded(mode)

    def compute(
        self,
        breakdown: PriceBreakdown,
        customer: Customer,
        address: Address,
        shipping: Optional[Money] = None,
        certificate_id: Optional[str] = None,
    ) -> TaxResult:
        currency = breakdown.currency
        jurisdiction = self.resolver.resolve(address)
        if jurisdiction is None:
            return TaxResult(currency, None, reason=f"no tax jurisdiction for {address.country}")

        decision = self.exemptions.decide(customer, certificate_id, jurisdiction)
        if decision.exempt:
            return TaxResult(currency, jurisdiction.code, exempt=True, reason=decision.reason)

        result = TaxResult(currency, jurisdiction.code, reason=decision.reason)
        shares = breakdown.line_nets_after_order_discounts()
        for line, taxable in zip(breakdown.lines, shares):
            rate = jurisdiction.rate_for(line.tax_category)
            result.lines.append(LineTax(line.sku, taxable, rate, self._tax(taxable, rate)))
        if shipping is not None and jurisdiction.tax_shipping and not shipping.is_zero():
            result.shipping_tax = self._tax(shipping, jurisdiction.rate_for("standard"))
        return result
