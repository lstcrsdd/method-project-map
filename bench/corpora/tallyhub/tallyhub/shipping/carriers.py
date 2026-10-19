"""Carrier rate tables (all carrier prices are in USD)."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Dict, List

from tallyhub.core.money import Money
from tallyhub.core.registry import CARRIERS
from tallyhub.shipping.packaging import Parcel


class Carrier:
    name = "abstract"
    methods: List[str] = []

    def serves(self, origin: str, destination: str, method: str) -> bool:
        return method in self.methods

    def parcel_price(self, parcel: Parcel, origin: str, destination: str, method: str) -> Money:
        raise NotImplementedError

    def price(self, parcels: List[Parcel], origin: str, destination: str, method: str) -> Money:
        total = Money.zero("USD")
        for parcel in parcels:
            total = total + self.parcel_price(parcel, origin, destination, method)
        return total


@CARRIERS.register("postline")
class Postline(Carrier):
    """Domestic post: flat price per started kilogram."""

    name = "postline"
    methods = ["standard", "economy"]
    per_kg = {"standard": Decimal("4.50"), "economy": Decimal("3.20")}

    def serves(self, origin: str, destination: str, method: str) -> bool:
        return origin == destination and method in self.methods

    def parcel_price(self, parcel: Parcel, origin: str, destination: str, method: str) -> Money:
        kilos = max(1, math.ceil(parcel.weight_kg))
        return Money.of(self.per_kg[method] * kilos, "USD")


@CARRIERS.register("globex")
class Globex(Carrier):
    """International ground: base fee by destination zone plus weight."""

    name = "globex"
    methods = ["standard", "express"]
    zones: Dict[str, str] = {"US": "A", "CA": "A", "GB": "B", "DE": "B", "FR": "B", "SE": "B", "JP": "C"}
    base = {"A": Decimal("9.00"), "B": Decimal("14.00"), "C": Decimal("19.00")}
    per_kg = Decimal("2.10")
    express_multiplier = Decimal("1.8")

    def serves(self, origin: str, destination: str, method: str) -> bool:
        return method in self.methods and destination in self.zones

    def parcel_price(self, parcel: Parcel, origin: str, destination: str, method: str) -> Money:
        zone = self.zones[destination]
        price = self.base[zone] + self.per_kg * Decimal(math.ceil(parcel.weight_kg))
        if method == "express":
            price *= self.express_multiplier
        return Money.of(price, "USD")


@CARRIERS.register("swiftair")
class SwiftAir(Carrier):
    """Overnight air; only for parcels under 5 kg."""

    name = "swiftair"
    methods = ["overnight"]

    def serves(self, origin: str, destination: str, method: str) -> bool:
        return method == "overnight"

    def parcel_price(self, parcel: Parcel, origin: str, destination: str, method: str) -> Money:
        if parcel.weight_g > 5000:
            raise ValueError("swiftair only takes parcels up to 5 kg")
        return Money.of(Decimal("32.00") + Decimal("6.00") * Decimal(math.ceil(parcel.weight_kg)), "USD")
