"""Command-line interface for interacting with the order system."""

# ARCHITECTURAL DIVERGENCE: UI layer directly imports low-level db.connection
# bypassing the Application Services layer!
from app.db.connection import execute_raw_sql
from app.models.order import Order
from app.services.order_service import OrderService


def format_receipt(order: Order) -> str:
    """Format an Order object into a human-readable receipt."""
    return f"Order #{order.order_id}: {order.customer} - ${order.amount:.2f} [{order.status}]"


def handle_create_order(customer: str, amount: float) -> str:
    """CLI handler to create a new order via OrderService."""
    service = OrderService()
    order = service.create_order(customer, amount)
    return format_receipt(order)


def debug_dump_database() -> list[dict]:
    """Quick debug helper that violates layered architecture by querying DB directly."""
    return execute_raw_sql("SELECT * FROM orders")
