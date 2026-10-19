"""Catalog storage and lookup."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from tallyhub.catalog.models import Bundle, Product
from tallyhub.core.errors import NotFound


class CatalogRepository:
    def __init__(self) -> None:
        self._products: Dict[str, Product] = {}
        self._bundles: Dict[str, Bundle] = {}

    def add_product(self, product: Product) -> None:
        self._products[product.sku] = product

    def add_bundle(self, bundle: Bundle) -> None:
        for sku in bundle.component_skus():
            if sku not in self._products:
                raise NotFound("product", sku)
        self._bundles[bundle.sku] = bundle

    def product(self, sku: str) -> Product:
        try:
            return self._products[sku]
        except KeyError:
            raise NotFound("product", sku) from None

    def find_product(self, sku: str) -> Optional[Product]:
        return self._products.get(sku)

    def bundle(self, sku: str) -> Optional[Bundle]:
        return self._bundles.get(sku)

    def is_bundle(self, sku: str) -> bool:
        return sku in self._bundles

    def products(self, active_only: bool = True) -> List[Product]:
        items: Iterable[Product] = self._products.values()
        if active_only:
            items = (p for p in items if p.active)
        return sorted(items, key=lambda p: p.sku)

    def by_tax_category(self, category: str) -> List[Product]:
        return [p for p in self.products() if p.tax_category == category]
