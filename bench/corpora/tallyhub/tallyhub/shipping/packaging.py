"""Packs order items into parcels."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from tallyhub.catalog.models import Product


@dataclass
class Parcel:
    weight_g: int = 0
    volume_cm3: int = 0
    items: List[Tuple[str, int]] = field(default_factory=list)

    @property
    def weight_kg(self) -> float:
        return self.weight_g / 1000


PACKAGING_WEIGHT_G = 150


def pack(items: List[Tuple[Product, int]], box_limit_g: int) -> List[Parcel]:
    """First-fit-decreasing by unit weight; a single item heavier than the limit gets its own parcel."""
    units: List[Product] = []
    for product, quantity in items:
        if product.shippable:
            units.extend([product] * quantity)
    units.sort(key=lambda p: p.weight_g, reverse=True)
    parcels: List[Parcel] = []
    for product in units:
        target = next((p for p in parcels if p.weight_g + product.weight_g <= box_limit_g), None)
        if target is None:
            target = Parcel(weight_g=PACKAGING_WEIGHT_G)
            parcels.append(target)
        target.weight_g += product.weight_g
        target.volume_cm3 += product.dimensions.volume_cm3
        for i, (sku, qty) in enumerate(target.items):
            if sku == product.sku:
                target.items[i] = (sku, qty + 1)
                break
        else:
            target.items.append((product.sku, 1))
    return parcels
