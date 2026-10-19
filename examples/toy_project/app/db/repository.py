"""Repository implementations for domain entities."""

from app.db.connection import DatabaseConnection
from app.models.order import Order


class BaseRepository:
    """Base repository providing connection lifecycle."""

    def __init__(self) -> None:
        self.conn = DatabaseConnection()


class OrderRepository(BaseRepository):
    """Persistence repository for Order entities."""

    def save_order(self, order: Order) -> int:
        """Persist a validated Order instance."""
        payload = {
            "customer": order.customer,
            "amount": order.amount,
            "status": order.status,
        }
        return self.conn.execute_insert(payload)
