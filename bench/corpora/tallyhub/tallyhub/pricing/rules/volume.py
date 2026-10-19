"""Quantity-based discounts per line."""

from __future__ import annotations

from decimal import Decimal
from typing import List, Tuple

from tallyhub.core.registry import PRICING_RULES
from tallyhub.pricing.breakdown import Adjustment, PriceBreakdown
from tallyhub.pricing.context import PricingContext
from tallyhub.pricing.rules.base import PricingRule


@PRICING_RULES.register("volume")
class VolumeDiscountRule(PricingRule):
    name = "volume"

    def tiers(self) -> List[Tuple[int, Decimal]]:
        raw = self.deps.settings.get("pricing.volume_tiers", [])
        return sorted(((int(q), Decimal(str(p))) for q, p in raw), reverse=True)

    def rate_for(self, quantity: int) -> Decimal:
        for threshold, pct in self.tiers():
            if quantity >= threshold:
                return pct
        return Decimal(0)

    def applies(self, breakdown: PriceBreakdown, ctx: PricingContext) -> bool:
        return ctx.customer.segment != "staff"

    def apply(self, breakdown: PriceBreakdown, ctx: PricingContext) -> None:
        for line in breakdown.lines:
            if line.is_bundle:
                continue
            pct = self.rate_for(line.quantity)
            if pct:
                line.adjustments.append(
                    Adjustment(self.name, f"volume {pct:%}", -(line.gross * pct))
                )
