"""Named plugin registries: pricing rules, payment methods, channels, exporters, strategies."""

from __future__ import annotations

from typing import Callable, Dict, Generic, Iterator, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: Dict[str, T] = {}

    def register(self, name: str) -> Callable[[T], T]:
        def decorator(item: T) -> T:
            if name in self._items:
                raise ValueError(f"{self.kind} {name!r} is already registered")
            self._items[name] = item
            return item

        return decorator

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            known = ", ".join(sorted(self._items)) or "none"
            raise KeyError(f"unknown {self.kind} {name!r} (known: {known})") from None

    def names(self) -> list[str]:
        return sorted(self._items)

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._items))


PRICING_RULES: Registry = Registry("pricing rule")
PAYMENT_METHODS: Registry = Registry("payment method")
NOTIFICATION_CHANNELS: Registry = Registry("notification channel")
REPORT_EXPORTERS: Registry = Registry("report exporter")
ALLOCATION_STRATEGIES: Registry = Registry("allocation strategy")
CARRIERS: Registry = Registry("carrier")
