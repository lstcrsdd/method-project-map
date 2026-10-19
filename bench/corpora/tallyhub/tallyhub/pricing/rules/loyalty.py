"""Loyalty-tier discounts."""

from __future__ import annotations

from decimal import Decimal

from tallyhub.core.registry import PRICING_RULES
from tallyhub.pricing.breakdown import Adjustment, PriceBreakdown
from tallyhub.pricing.context import PricingContext
from tallyhub.pricing.rules.base import PricingRule


@PRICING_RULES.register("loyalty")
class LoyaltyRule(PricingRule):
    name = "loyalty"

    def rate(self, tier: str) -> Decimal:
        tiers = self.deps.settings.get("loyalty.tiers", {})
        return Decimal(str(tiers.get(tier, "0")))

    def applies(self, breakdown: PriceBreakdown, ctx: PricingContext) -> bool:
        if not ctx.customer.loyalty_tier:
            return False
        stacking_allowed = self.deps.settings.get("pricing.allow_coupon_with_loyalty", False)
        if not stacking_allowed and breakdown.has_adjustment_from("coupon"):
            breakdown.notes.append("loyalty discount skipped: a coupon was applied")
            return False
        return True

    def apply(self, breakdown: PriceBreakdown, ctx: PricingContext) -> None:
        pct = self.rate(ctx.customer.loyalty_tier or "")
        if not pct:
            return
        base = breakdown.lines_net + breakdown.order_discount
        breakdown.add_order_adjustment(
            Adjustment(self.name, f"{ctx.customer.loyalty_tier} member {pct:%}", -(base * pct))
        )
