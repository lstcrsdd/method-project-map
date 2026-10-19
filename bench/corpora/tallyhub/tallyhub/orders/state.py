"""Order lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Set

from tallyhub.core.errors import InvalidTransition
from tallyhub.orders.models import Order

PLACED = "placed"
PAID = "paid"
SHIPPED = "shipped"
DELIVERED = "delivered"
CANCELLED = "cancelled"
PARTIALLY_REFUNDED = "partially_refunded"
REFUNDED = "refunded"

TRANSITIONS: Dict[str, Set[str]] = {
    PLACED: {PAID, CANCELLED},
    PAID: {SHIPPED, CANCELLED, PARTIALLY_REFUNDED, REFUNDED},
    SHIPPED: {DELIVERED, PARTIALLY_REFUNDED, REFUNDED},
    DELIVERED: {PARTIALLY_REFUNDED, REFUNDED},
    PARTIALLY_REFUNDED: {PARTIALLY_REFUNDED, REFUNDED, SHIPPED, DELIVERED},
    CANCELLED: set(),
    REFUNDED: set(),
}

CANCELLABLE = {PLACED, PAID}
REFUNDABLE = {PAID, SHIPPED, DELIVERED, PARTIALLY_REFUNDED}


def transition(order: Order, new_status: str, at: datetime) -> None:
    allowed = TRANSITIONS.get(order.status, set())
    if new_status not in allowed:
        raise InvalidTransition(f"order {order.id}: {order.status} -> {new_status} is not allowed")
    order.status = new_status
    order.history.append((new_status, at))
