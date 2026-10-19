"""Monetary amounts with currency-aware rounding."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Iterable, Sequence, Union

from tallyhub.core.errors import CurrencyMismatch

# Number of minor units per currency (ISO 4217 exponent).
CURRENCY_EXPONENTS = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CHF": 2,
    "SEK": 2,
    "CAD": 2,
    "JPY": 0,
    "KRW": 0,
    "KWD": 3,
}

ROUNDING_MODES = {"half_up": ROUND_HALF_UP, "half_even": ROUND_HALF_EVEN}

Number = Union[int, Decimal, str]


def exponent_for(currency: str) -> int:
    try:
        return CURRENCY_EXPONENTS[currency]
    except KeyError:
        raise ValueError(f"unsupported currency {currency!r}") from None


def quantum_for(currency: str) -> Decimal:
    """Smallest representable step for a currency, e.g. Decimal('0.01') for USD, Decimal('1') for JPY."""
    return Decimal(1).scaleb(-exponent_for(currency))


def round_amount(amount: Decimal, currency: str, mode: str = "half_up") -> Decimal:
    return amount.quantize(quantum_for(currency), rounding=ROUNDING_MODES[mode])


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    @classmethod
    def of(cls, amount: Number, currency: str) -> Money:
        return cls(round_amount(Decimal(str(amount)), currency), currency)

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls.of(0, currency)

    @classmethod
    def raw(cls, amount: Decimal, currency: str) -> Money:
        """An unrounded intermediate value; call `.rounded()` before showing or charging it."""
        return cls(amount, currency)

    def rounded(self, mode: str = "half_up") -> Money:
        return Money(round_amount(self.amount, self.currency, mode), self.currency)

    def _check(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"cannot combine Money with {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatch(self.currency, other.currency)

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: Number) -> Money:
        return Money(self.amount * Decimal(str(factor)), self.currency)

    __rmul__ = __mul__

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def is_zero(self) -> bool:
        return self.amount == 0

    def is_negative(self) -> bool:
        return self.amount < 0

    def allocate(self, weights: Sequence[Number]) -> list[Money]:
        """Split into parts proportional to `weights`; parts are rounded and always sum to `self`.

        Uses the largest-remainder method so no minor unit is lost or invented.
        """
        weights = [Decimal(str(w)) for w in weights]
        total_weight = sum(weights)
        if total_weight <= 0:
            raise ValueError("weights must sum to a positive number")
        quantum = quantum_for(self.currency)
        total_units = int((self.rounded().amount / quantum).to_integral_value())
        exact = [Decimal(total_units) * w / total_weight for w in weights]
        units = [int(e) for e in exact]
        remainder = total_units - sum(units)
        order = sorted(range(len(weights)), key=lambda i: exact[i] - units[i], reverse=True)
        for i in order[:remainder]:
            units[i] += 1
        return [Money(Decimal(u) * quantum, self.currency) for u in units]

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


def sum_money(items: Iterable[Money], currency: str) -> Money:
    total = Money.zero(currency)
    for item in items:
        total = total + item
    return total
