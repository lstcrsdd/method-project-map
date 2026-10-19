"""Pricing rule interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from tallyhub.catalog.price_lists import PriceList
from tallyhub.catalog.repository import CatalogRepository
from tallyhub.core.settings import Settings
from tallyhub.pricing.breakdown import PriceBreakdown
from tallyhub.pricing.context import PricingContext
from tallyhub.pricing.coupons import CouponBook


@dataclass
class RuleDeps:
    settings: Settings
    catalog: CatalogRepository
    price_list: PriceList
    coupons: CouponBook


class PricingRule(ABC):
    """One step of the pricing pipeline. Rules run in the order configured in `pricing.rules`."""

    name = "abstract"

    def __init__(self, deps: RuleDeps) -> None:
        self.deps = deps

    def applies(self, breakdown: PriceBreakdown, ctx: PricingContext) -> bool:
        return True

    @abstractmethod
    def apply(self, breakdown: PriceBreakdown, ctx: PricingContext) -> None:
        ...

    def run(self, breakdown: PriceBreakdown, ctx: PricingContext) -> None:
        if self.applies(breakdown, ctx):
            self.apply(breakdown, ctx)
