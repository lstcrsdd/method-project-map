"""Message templates."""

from __future__ import annotations

from typing import Any, Dict

TEMPLATES: Dict[str, Dict[str, str]] = {
    "order_confirmation": {
        "subject": "Your order {order_id} is confirmed",
        "body": (
            "Hi {name},\n\nThanks for your order placed on {date}.\n"
            "Items: {item_count}\nSubtotal: {subtotal}\nShipping: {shipping}\nTax: {tax}\n"
            "Total charged: {total}\n{discount_line}\n"
        ),
    },
    "order_shipped": {
        "subject": "Order {order_id} is on its way",
        "body": "Hi {name},\n\nYour order shipped with {carrier}. Tracking number: {tracking}.\n",
    },
    "refund_issued": {
        "subject": "Refund for order {order_id}",
        "body": "Hi {name},\n\nWe refunded {total}:\n{parts}\n",
    },
    "order_cancelled": {
        "subject": "Order {order_id} was cancelled",
        "body": "Hi {name},\n\nYour order was cancelled ({reason}). Any payment has been voided.\n",
    },
}


class _Missing(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(template: str, **values: Any) -> Dict[str, str]:
    spec = TEMPLATES[template]
    data = _Missing(values)
    return {"subject": spec["subject"].format_map(data), "body": spec["body"].format_map(data)}
