"""Time and identifier sources (replaceable in tests)."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterator


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock(Clock):
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, **kwargs: float) -> None:
        self._at = self._at + timedelta(**kwargs)


class IdGenerator:
    """Sequential, prefixed identifiers: ord_000001, pay_000001, ..."""

    def __init__(self) -> None:
        self._counters: Dict[str, Iterator[int]] = {}

    def next(self, prefix: str) -> str:
        counter = self._counters.setdefault(prefix, itertools.count(1))
        return f"{prefix}_{next(counter):06d}"
