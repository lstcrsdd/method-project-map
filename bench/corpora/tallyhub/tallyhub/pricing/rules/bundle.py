"""Bundle discounts: a bundle costs its components minus the bundle's discount."""

from __future__ import annotations

from tallyhub.core.registry import PRICING_RULES
from tallyhub.pricing.breakdown import Adjustment, PriceBreakdown
from tallyhub.pricing.context import PricingContext
from tallyhub.pricing.rules.base import PricingRule


@PRICING_RULES.register("bundle")
class BundleRule(PricingRule):
    name = "bundle"

    def applies(self, breakdown: PriceBreakdown, ctx: PricingContext) -> bool:
        return any(line.is_bundle for line in breakdown.lines)

    def apply(self, breakdown: PriceBreakdown, ctx: PricingContext) -> None:
        for line in breakdown.lines:
            if not line.is_bundle:
                continue
            bundle = self.deps.catalog.bundle(line.sku)
            if bundle is None or not bundle.discount_pct:
                continue
            line.adjustments.append(
                Adjustment(self.name, f"bundle {bundle.name}", -(line.gross * bundle.discount_pct))
            )
