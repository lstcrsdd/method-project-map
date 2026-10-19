"""Grouping helpers for reports."""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Dict, Iterable, List, TypeVar

from tallyhub.core.money import Money

T = TypeVar("T")


def group_by(rows: Iterable[T], key: Callable[[T], str]) -> Dict[str, List[T]]:
    groups: Dict[str, List[T]] = OrderedDict()
    for row in rows:
        groups.setdefault(key(row), []).append(row)
    return groups


def sum_by_currency(amounts: Iterable[Money]) -> Dict[str, Money]:
    totals: Dict[str, Money] = {}
    for amount in amounts:
        totals[amount.currency] = totals.get(amount.currency, Money.zero(amount.currency)) + amount
    return dict(sorted(totals.items()))
