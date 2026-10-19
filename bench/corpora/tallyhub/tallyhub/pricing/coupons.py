"""Coupon definitions and redemption limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from tallyhub.core.money import Money


@dataclass
class Coupon:
    code: str
    kind: str  # "percent" | "fixed"
    value: Decimal  # fraction for percent coupons, amount for fixed coupons
    currency: Optional[str] = None  # required for fixed coupons
    min_subtotal: Optional[Money] = None
    starts_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    max_redemptions: Optional[int] = None
    segments: List[str] = field(default_factory=lambda: ["retail", "wholesale", "staff"])
    redemptions: int = 0

    def is_active(self, at: datetime) -> bool:
        if self.starts_at and at < self.starts_at:
            return False
        if self.expires_at and at >= self.expires_at:
            return False
        if self.max_redemptions is not None and self.redemptions >= self.max_redemptions:
            return False
        return True


class CouponBook:
    def __init__(self) -> None:
        self._coupons: Dict[str, Coupon] = {}

    def add(self, coupon: Coupon) -> None:
        self._coupons[coupon.code.upper()] = coupon

    def find(self, code: Optional[str]) -> Optional[Coupon]:
        if not code:
            return None
        return self._coupons.get(code.strip().upper())

    def redeem(self, code: str) -> None:
        coupon = self.find(code)
        if coupon is not None:
            coupon.redemptions += 1
