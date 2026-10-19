"""Shipping quotes for an order."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Tuple

from tallyhub.catalog.repository import CatalogRepository
from tallyhub.core.fx import FxRates
from tallyhub.core.money import Money
from tallyhub.core.registry import CARRIERS
from tallyhub.core.settings import Settings
from tallyhub.orders.models import Address
from tallyhub.shipping import carriers  # noqa: F401  (register carriers)
from tallyhub.shipping.packaging import Parcel, pack


@dataclass
class ShippingQuote:
    amount: Money
    carrier: Optional[str]
    method: str
    parcels: List[Parcel] = field(default_factory=list)
    free_shipping: bool = False


class ShippingQuoter:
    def __init__(self, settings: Settings, catalog: CatalogRepository, fx: FxRates) -> None:
        self.settings = settings
        self.catalog = catalog
        self.fx = fx

    def free_threshold(self, currency: str) -> Optional[Money]:
        raw = self.settings.get(f"shipping.free_shipping_threshold.{currency}")
        return Money.of(Decimal(raw), currency) if raw is not None else None

    def quote(
        self,
        items: List[Tuple[str, int]],
        address: Address,
        method: str,
        currency: str,
        merchandise_subtotal: Money,
        origin_country: str = "US",
    ) -> ShippingQuote:
        products = [(self.catalog.product(sku), qty) for sku, qty in items]
        parcels = pack(products, int(self.settings.get("shipping.box_limit_g", 20000)))
        if not parcels:
            return ShippingQuote(Money.zero(currency), None, method)

        threshold = self.free_threshold(currency)
        if method == "standard" and threshold is not None and threshold <= merchandise_subtotal:
            return ShippingQuote(Money.zero(currency), None, method, parcels, free_shipping=True)

        destination = address.country.upper()
        offers = []
        for name in CARRIERS:
            carrier = CARRIERS.get(name)()
            if carrier.serves(origin_country, destination, method):
                offers.append((carrier.price(parcels, origin_country, destination, method), name))
        if not offers:
            raise ValueError(f"no carrier ships {method} from {origin_country} to {destination}")
        price, carrier_name = min(offers, key=lambda offer: offer[0].amount)
        return ShippingQuote(self.fx.convert(price, currency), carrier_name, method, parcels)
