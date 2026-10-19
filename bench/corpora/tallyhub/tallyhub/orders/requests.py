"""What a caller (API, CLI, POS) asks the order service to do."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from tallyhub.core.money import Money
from tallyhub.orders.models import Address


@dataclass
class PaymentInstruction:
    method: str  # name in core.registry.PAYMENT_METHODS
    amount: Optional[Money] = None  # None: pay whatever is left
    token: Optional[str] = None  # card token, gift card number, ...


@dataclass
class OrderRequest:
    customer_id: str
    items: List[Tuple[str, int]]
    currency: str
    shipping_address: Address
    shipping_method: str = "standard"
    coupon_code: Optional[str] = None
    tax_exempt_id: Optional[str] = None
    payments: List[PaymentInstruction] = field(default_factory=lambda: [PaymentInstruction("card")])
    channel: str = "web"
    note: Optional[str] = None
