"""Domain models for orders."""

from dataclasses import dataclass


@dataclass
class Order:
    """Represents a customer order in the system."""
    order_id: int
    customer: str
    amount: float
    status: str = "pending"

    def is_valid(self) -> bool:
        """Check whether the order has a positive amount and customer name."""
        return bool(self.customer.strip()) and self.amount > 0.0
