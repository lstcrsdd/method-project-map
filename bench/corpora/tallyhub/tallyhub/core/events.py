"""In-process domain events."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, DefaultDict, Dict, List, Type


@dataclass(frozen=True)
class Event:
    pass


@dataclass(frozen=True)
class OrderPlaced(Event):
    order_id: str
    customer_id: str


@dataclass(frozen=True)
class OrderCancelled(Event):
    order_id: str
    reason: str


@dataclass(frozen=True)
class OrderShipped(Event):
    order_id: str
    carrier: str
    tracking: str


@dataclass(frozen=True)
class PaymentCaptured(Event):
    order_id: str
    payment_id: str


@dataclass(frozen=True)
class RefundIssued(Event):
    order_id: str
    refund_id: str
    amounts: Dict[str, Any] = field(default_factory=dict)


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: DefaultDict[Type[Event], List[Handler]] = defaultdict(list)
        self.history: List[Event] = []

    def subscribe(self, event_type: Type[Event], handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        self.history.append(event)
        for handler in list(self._handlers[type(event)]):
            handler(event)

    def of_type(self, event_type: Type[Event]) -> List[Event]:
        return [e for e in self.history if isinstance(e, event_type)]
