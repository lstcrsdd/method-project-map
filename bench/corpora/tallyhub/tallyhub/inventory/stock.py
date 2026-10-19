"""Stock levels and reservations per (sku, warehouse)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Tuple


@dataclass
class StockLevel:
    on_hand: int = 0
    reserved: int = 0

    @property
    def available(self) -> int:
        """Units that can still be promised to new orders."""
        return self.on_hand - self.reserved


@dataclass(frozen=True)
class Reservation:
    order_id: str
    sku: str
    warehouse: str
    quantity: int
    created_at: datetime


class StockBook:
    """Bookkeeping only: deciding *where* stock comes from is the allocator's job."""

    def __init__(self) -> None:
        self._levels: Dict[Tuple[str, str], StockLevel] = {}
        self._reservations: List[Reservation] = []

    def level(self, sku: str, warehouse: str) -> StockLevel:
        return self._levels.setdefault((sku, warehouse), StockLevel())

    def set_on_hand(self, sku: str, warehouse: str, quantity: int) -> None:
        self.level(sku, warehouse).on_hand = quantity

    def total_available(self, sku: str) -> int:
        return sum(level.available for (s, _w), level in self._levels.items() if s == sku)

    def reserve(self, order_id: str, sku: str, warehouse: str, quantity: int, at: datetime) -> Reservation:
        self.level(sku, warehouse).reserved += quantity
        reservation = Reservation(order_id, sku, warehouse, quantity, at)
        self._reservations.append(reservation)
        return reservation

    def reservations_for(self, order_id: str) -> List[Reservation]:
        return [r for r in self._reservations if r.order_id == order_id]

    def release(self, order_id: str) -> int:
        """Give back every unit reserved for the order; returns the number of units released."""
        released = 0
        for reservation in self.reservations_for(order_id):
            self.level(reservation.sku, reservation.warehouse).reserved -= reservation.quantity
            released += reservation.quantity
        self._reservations = [r for r in self._reservations if r.order_id != order_id]
        return released

    def commit(self, order_id: str) -> None:
        """The order shipped: reserved units leave the warehouse."""
        for reservation in self.reservations_for(order_id):
            level = self.level(reservation.sku, reservation.warehouse)
            level.reserved -= reservation.quantity
            level.on_hand -= reservation.quantity
        self._reservations = [r for r in self._reservations if r.order_id != order_id]
