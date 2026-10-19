"""Application settings with layered overrides."""

from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

DEFAULTS: Dict[str, Any] = {
    "pricing": {
        # Rules run in this order; each name refers to an entry in core.registry.PRICING_RULES.
        "rules": ["volume", "bundle", "coupon", "loyalty"],
        "allow_coupon_with_loyalty": False,
        "volume_tiers": [[10, "0.05"], [50, "0.10"], [200, "0.15"]],
    },
    "loyalty": {"tiers": {"silver": "0.03", "gold": "0.07", "platinum": "0.10"}},
    "rounding": {"default": "half_up", "per_currency": {"SEK": "half_even"}},
    "shipping": {
        "free_shipping_threshold": {"USD": "150", "EUR": "140", "GBP": "120", "JPY": "20000"},
        "default_method": "standard",
        "box_limit_g": 20000,
    },
    "inventory": {"allocation_strategy": "nearest", "reservation_ttl_minutes": 30},
    "billing": {
        "invoice_prefix": "INV",
        # Refunds go back to payment methods in this order.
        "refund_order": ["store_credit", "gift_card", "card", "bank_transfer"],
    },
    "notifications": {"channels": ["email"], "locale": "en_US"},
    "api": {"keys": {}},
}


def _merge(base: Dict[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


class Settings:
    def __init__(self, overrides: Mapping[str, Any] | None = None) -> None:
        self._data = _merge(copy.deepcopy(DEFAULTS), overrides or {})

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, name: str) -> Dict[str, Any]:
        return copy.deepcopy(self._data.get(name, {}))

    def rounding_mode(self, currency: str) -> str:
        return self.get(f"rounding.per_currency.{currency}") or self.get("rounding.default")
