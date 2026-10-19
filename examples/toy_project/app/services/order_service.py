"""Business logic for creating and processing customer orders."""

from typing import Any
from app.db.repository import OrderRepository
from app.models.order import Order


def validate_discount(amount: float) -> float:
    """Apply standard volume discount if threshold is met."""
    if amount >= 100.0:
        return round(amount * 0.9, 2)
    return amount


class OrderService:
    """Orchestrates order validation, pricing, and persistence."""

    def __init__(self) -> None:
        self.repo = OrderRepository()

    def create_order(self, customer: str, amount: float, hook: Any = None) -> Order:
        """Create, validate, and persist a new order."""
        final_amount = validate_discount(amount)
        order = Order(order_id=0, customer=customer, amount=final_amount, status="created")
        if not order.is_valid():
            raise ValueError("Invalid order parameters")
        saved_id = self.repo.save_order(order)
        order.order_id = saved_id
        if hook is not None:
            # Dynamic callback: target cannot be determined statically -> unresolved call
            hook.on_order_created(order)
        return order
