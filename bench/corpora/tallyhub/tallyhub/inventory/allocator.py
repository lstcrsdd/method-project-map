"""Chooses the warehouse that ships each item of an order."""

from __future__ import annotations

from typing import Dict, List, Optional

from tallyhub.core.errors import OutOfStock
from tallyhub.core.registry import ALLOCATION_STRATEGIES
from tallyhub.core.settings import Settings
from tallyhub.inventory.stock import StockBook
from tallyhub.inventory.warehouses import Warehouse, WarehouseDirectory
from tallyhub.orders.models import Address


class AllocationStrategy:
    name = "abstract"

    def __init__(self, warehouses: WarehouseDirectory, stock: StockBook) -> None:
        self.warehouses = warehouses
        self.stock = stock

    def candidates(self, address: Address) -> List[Warehouse]:
        return self.warehouses.active()

    def choose(self, sku: str, quantity: int, address: Address) -> Optional[Warehouse]:
        for warehouse in self.candidates(address):
            if self.stock.level(sku, warehouse.code).available >= quantity:
                return warehouse
        return None


@ALLOCATION_STRATEGIES.register("priority")
class PriorityStrategy(AllocationStrategy):
    """Always prefer the warehouse with the best priority that has the stock."""

    name = "priority"


@ALLOCATION_STRATEGIES.register("nearest")
class NearestStrategy(AllocationStrategy):
    """Prefer warehouses in the destination country, then fall back to priority order."""

    name = "nearest"

    def candidates(self, address: Address) -> List[Warehouse]:
        active = self.warehouses.active()
        domestic = [w for w in active if w.country == address.country.upper()]
        foreign = [w for w in active if w.country != address.country.upper()]
        return domestic + foreign

    def choose(self, sku: str, quantity: int, address: Address) -> Optional[Warehouse]:
        for warehouse in self.candidates(address):
            level = self.stock.level(sku, warehouse.code)
            if level.available >= quantity:
                return warehouse
        return None


class Allocator:
    def __init__(self, settings: Settings, warehouses: WarehouseDirectory, stock: StockBook) -> None:
        name = settings.get("inventory.allocation_strategy", "nearest")
        self.strategy: AllocationStrategy = ALLOCATION_STRATEGIES.get(name)(warehouses, stock)
        self.stock = stock

    def plan(self, needs: Dict[str, int], address: Address) -> Dict[str, str]:
        """sku -> warehouse code for every needed sku; raises OutOfStock if one cannot be served."""
        plan: Dict[str, str] = {}
        for sku, quantity in needs.items():
            warehouse = self.strategy.choose(sku, quantity, address)
            if warehouse is None:
                raise OutOfStock(sku, quantity, self.stock.total_available(sku))
            plan[sku] = warehouse.code
        return plan
