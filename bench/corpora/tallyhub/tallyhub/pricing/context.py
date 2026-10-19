"""Inputs to a pricing run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from tallyhub.customers.models import Customer


@dataclass
class PricingContext:
    customer: Customer
    currency: str
    items: List[Tuple[str, int]]
    at: datetime
    coupon_code: Optional[str] = None
    channel: str = "web"  # web | api | cli | pos
    flags: List[str] = field(default_factory=list)
