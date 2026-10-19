"""Order-level coupon discounts."""

from __future__ import annotations

from tallyhub.core.money import Money
from tallyhub.core.registry import PRICING_RULES
from tallyhub.pricing.breakdown import Adjustment, PriceBreakdown
from tallyhub.pricing.context import PricingContext
from tallyhub.pricing.rules.base import PricingRule


@PRICING_RULES.register("coupon")
class CouponRule(PricingRule):
    name = "coupon"

    def applies(self, breakdown: PriceBreakdown, ctx: PricingContext) -> bool:
        return bool(ctx.coupon_code)

    def apply(self, breakdown: PriceBreakdown, ctx: PricingContext) -> None:
        coupon = self.deps.coupons.find(ctx.coupon_code)
        if coupon is None:
            breakdown.notes.append(f"coupon {ctx.coupon_code!r} is unknown")
            return
        if not coupon.is_active(ctx.at):
            breakdown.notes.append(f"coupon {coupon.code} is not active")
            return
        if ctx.customer.segment not in coupon.segments:
            breakdown.notes.append(f"coupon {coupon.code} is not valid for {ctx.customer.segment} customers")
            return
        base = breakdown.lines_net
        if coupon.min_subtotal is not None:
            if coupon.min_subtotal.currency != base.currency or base < coupon.min_subtotal:
                breakdown.notes.append(f"coupon {coupon.code} needs a subtotal of {coupon.min_subtotal}")
                return

        if coupon.kind == "percent":
            amount = base * coupon.value
        elif coupon.kind == "fixed":
            if coupon.currency != breakdown.currency:
                breakdown.notes.append(f"coupon {coupon.code} is only valid in {coupon.currency}")
                return
            amount = Money.of(coupon.value, breakdown.currency)
            if base < amount:
                amount = base
        else:
            raise ValueError(f"unknown coupon kind {coupon.kind!r}")
        breakdown.add_order_adjustment(Adjustment(self.name, f"coupon {coupon.code}", -amount, code=coupon.code))
