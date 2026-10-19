"""JSON <-> domain objects for the HTTP API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tallyhub.core.errors import ValidationError
from tallyhub.core.money import Money
from tallyhub.orders.models import Address, Order
from tallyhub.orders.requests import OrderRequest, PaymentInstruction

REQUIRED_ORDER_FIELDS = ("customer_id", "items", "currency", "shipping_address")


def money_json(money: Money) -> Dict[str, str]:
    return {"amount": str(money.amount), "currency": money.currency}


def parse_money(value: Any, currency: str) -> Money:
    if isinstance(value, dict):
        return Money.of(value["amount"], value.get("currency", currency))
    return Money.of(value, currency)


def parse_address(data: Dict[str, Any]) -> Address:
    return Address(
        line1=data.get("line1", ""),
        city=data.get("city", ""),
        country=str(data.get("country", "")).upper(),
        region=str(data.get("region", "")).upper(),
        postal_code=data.get("postal_code", ""),
        name=data.get("name", ""),
    )


def parse_payments(items: Optional[List[Dict[str, Any]]], currency: str) -> List[PaymentInstruction]:
    if not items:
        return [PaymentInstruction("card")]
    payments = []
    for item in items:
        amount = item.get("amount")
        payments.append(
            PaymentInstruction(
                method=item["method"],
                amount=parse_money(amount, currency) if amount is not None else None,
                token=item.get("token"),
            )
        )
    return payments


def parse_order_request(payload: Dict[str, Any], channel: str = "api") -> OrderRequest:
    missing = [name for name in REQUIRED_ORDER_FIELDS if not payload.get(name)]
    if missing:
        raise ValidationError([f"missing field {name}" for name in missing])
    currency = str(payload["currency"]).upper()
    return OrderRequest(
        customer_id=payload["customer_id"],
        items=[(item["sku"], int(item.get("quantity", 1))) for item in payload["items"]],
        currency=currency,
        shipping_address=parse_address(payload["shipping_address"]),
        shipping_method=payload.get("shipping_method", "standard"),
        coupon_code=payload.get("coupon_code"),
        tax_exempt_id=payload.get("tax_exempt_id"),
        payments=parse_payments(payload.get("payments"), currency),
        channel=channel,
        note=payload.get("note"),
    )


def serialize_order(order: Order) -> Dict[str, Any]:
    totals = order.totals
    return {
        "id": order.id,
        "customer_id": order.customer_id,
        "status": order.status,
        "currency": order.currency,
        "channel": order.channel,
        "lines": [
            {
                "sku": line.sku,
                "quantity": line.quantity,
                "unit_price": money_json(line.unit_price),
                "net": money_json(line.net),
                "tax": money_json(line.tax),
                "warehouse": line.warehouse,
            }
            for line in order.lines
        ],
        "totals": {
            "merchandise": money_json(totals.merchandise),
            "discount": money_json(totals.discount),
            "subtotal": money_json(totals.subtotal),
            "shipping": money_json(totals.shipping),
            "tax": money_json(totals.tax),
            "grand_total": money_json(totals.grand_total),
        },
        "shipping": {"method": order.shipping_method, "carrier": order.carrier, "tracking": order.tracking},
        "tax_jurisdiction": order.tax_jurisdiction,
        "tax_exempt": order.tax_exempt,
        "coupon_code": order.coupon_code,
        "payment_ids": list(order.payment_ids),
        "notes": list(order.notes),
    }
