"""Segment- and currency-specific prices on top of catalog base prices."""

from __future__ import annotations

from typing import Dict, Tuple

from tallyhub.catalog.repository import CatalogRepository
from tallyhub.core.fx import FxRates
from tallyhub.core.money import Money


class PriceList:
    """Explicit prices per (sku, currency, segment); falls back to the converted base price."""

    def __init__(self, catalog: CatalogRepository, fx: FxRates) -> None:
        self.catalog = catalog
        self.fx = fx
        self._prices: Dict[Tuple[str, str, str], Money] = {}

    def set_price(self, sku: str, price: Money, segment: str = "retail") -> None:
        self._prices[(sku, price.currency, segment)] = price

    def unit_price(self, sku: str, currency: str, segment: str = "retail") -> Money:
        for seg in (segment, "retail"):
            explicit = self._prices.get((sku, currency, seg))
            if explicit is not None:
                return explicit
        product = self.catalog.product(sku)
        return self.fx.convert(product.base_price, currency)

    def bundle_price(self, sku: str, currency: str, segment: str = "retail") -> Money:
        bundle = self.catalog.bundle(sku)
        if bundle is None:
            return self.unit_price(sku, currency, segment)
        total = Money.zero(currency)
        for component, qty in bundle.components:
            total = total + self.unit_price(component, currency, segment) * qty
        return total
