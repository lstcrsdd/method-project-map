"""Warehouses that hold stock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from tallyhub.core.errors import NotFound


@dataclass(frozen=True)
class Warehouse:
    code: str
    name: str
    country: str
    priority: int = 100  # lower ships first when several are equally close
    active: bool = True


class WarehouseDirectory:
    def __init__(self) -> None:
        self._warehouses: Dict[str, Warehouse] = {}

    def add(self, warehouse: Warehouse) -> None:
        self._warehouses[warehouse.code] = warehouse

    def get(self, code: str) -> Warehouse:
        try:
            return self._warehouses[code]
        except KeyError:
            raise NotFound("warehouse", code) from None

    def active(self) -> List[Warehouse]:
        return sorted((w for w in self._warehouses.values() if w.active), key=lambda w: (w.priority, w.code))
