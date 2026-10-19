"""Runs the configured pricing rules over an order's items."""

from __future__ import annotations

from typing import List

from tallyhub.core.registry import PRICING_RULES
from tallyhub.pricing.breakdown import LineBreakdown, PriceBreakdown
from tallyhub.pricing.context import PricingContext
from tallyhub.pricing.rules import bundle, coupon, loyalty, volume  # noqa: F401  (register rules)
from tallyhub.pricing.rules.base import PricingRule, RuleDeps


class PricingEngine:
    def __init__(self, deps: RuleDeps) -> None:
        self.deps = deps
        self.rules: List[PricingRule] = [
            PRICING_RULES.get(name)(deps) for name in deps.settings.get("pricing.rules", [])
        ]

    def _line(self, sku: str, quantity: int, ctx: PricingContext) -> LineBreakdown:
        catalog, prices = self.deps.catalog, self.deps.price_list
        segment = ctx.customer.segment
        if catalog.is_bundle(sku):
            components = catalog.bundle(sku).components  # type: ignore[union-attr]
            categories = {catalog.product(c).tax_category for c, _ in components}
            category = categories.pop() if len(categories) == 1 else "standard"
            return LineBreakdown(sku, quantity, prices.bundle_price(sku, ctx.currency, segment), category,
                                 is_bundle=True)
        product = catalog.product(sku)
        return LineBreakdown(sku, quantity, prices.unit_price(sku, ctx.currency, segment), product.tax_category)

    def price(self, ctx: PricingContext) -> PriceBreakdown:
        breakdown = PriceBreakdown(currency=ctx.currency)
        merged: dict = {}
        for sku, quantity in ctx.items:
            merged[sku] = merged.get(sku, 0) + quantity
        breakdown.lines = [self._line(sku, qty, ctx) for sku, qty in merged.items()]
        for rule in self.rules:
            rule.run(breakdown, ctx)
        return breakdown
