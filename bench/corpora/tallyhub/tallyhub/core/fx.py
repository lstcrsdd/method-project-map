"""Foreign-exchange conversion between supported currencies."""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, Tuple

from tallyhub.core.money import Money


class FxRates:
    """Rates are stored against USD; cross rates are derived."""

    def __init__(self, usd_rates: Dict[str, str]) -> None:
        self._usd = {code: Decimal(rate) for code, rate in usd_rates.items()}
        self._usd["USD"] = Decimal(1)

    def rate(self, source: str, target: str) -> Decimal:
        if source == target:
            return Decimal(1)
        try:
            return self._usd[target] / self._usd[source]
        except KeyError as exc:
            raise ValueError(f"no FX rate for {exc.args[0]}") from None

    def convert(self, money: Money, target: str) -> Money:
        if money.currency == target:
            return money
        return Money.of(money.amount * self.rate(money.currency, target), target)

    def pairs(self) -> list[Tuple[str, str]]:
        codes = sorted(self._usd)
        return [(a, b) for a in codes for b in codes if a != b]
