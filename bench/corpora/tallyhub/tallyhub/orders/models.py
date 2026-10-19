"""Order entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from tallyhub.core.money import Money


@dataclass(frozen=True)
class Address:
    line1: str
    city: str
    country: str
    region: str = ""
    postal_code: str = ""
    name: str = ""


@dataclass
class OrderLine:
    sku: str
    quantity: int
    unit_price: Money
    net: Money  # after line and order discounts
    tax: Money
    warehouse: Optional[str] = None


@dataclass
class OrderTotals:
    merchandise: Money  # before discounts
    discount: Money  # negative or zero
    subtotal: Money  # merchandise + discount
    shipping: Money
    tax: Money
    grand_total: Money


@dataclass
class Order:
    id: str
    customer_id: str
    currency: str
    lines: List[OrderLine]
    totals: OrderTotals
    shipping_address: Address
    shipping_method: str
    carrier: Optional[str]
    status: str
    created_at: datetime
    channel: str = "web"
    coupon_code: Optional[str] = None
    tax_exempt_id: Optional[str] = None
    tax_exempt: bool = False
    tax_jurisdiction: Optional[str] = None
    payment_ids: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    history: List[Tuple[str, datetime]] = field(default_factory=list)
    tracking: Optional[str] = None
