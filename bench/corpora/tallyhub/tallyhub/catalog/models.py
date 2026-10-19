"""Catalog entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Tuple

from tallyhub.core.money import Money


@dataclass(frozen=True)
class Dimensions:
    length_cm: int
    width_cm: int
    height_cm: int

    @property
    def volume_cm3(self) -> int:
        return self.length_cm * self.width_cm * self.height_cm


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    base_price: Money
    tax_category: str = "standard"  # standard | reduced | zero | digital
    weight_g: int = 0
    dimensions: Dimensions = Dimensions(10, 10, 10)
    shippable: bool = True
    active: bool = True


@dataclass(frozen=True)
class Bundle:
    """A set of products sold together at a discount off the sum of component prices."""

    sku: str
    name: str
    components: Tuple[Tuple[str, int], ...]
    discount_pct: Decimal = Decimal("0")
    tags: List[str] = field(default_factory=list)

    def component_skus(self) -> List[str]:
        return [sku for sku, _qty in self.components]
