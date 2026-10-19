"""Invoice numbers: PREFIX-YEAR-SEQUENCE, the sequence restarting every calendar year."""

from __future__ import annotations

from typing import Dict

from tallyhub.core.clock import Clock


class InvoiceNumberer:
    def __init__(self, prefix: str, clock: Clock) -> None:
        self.prefix = prefix
        self.clock = clock
        self._last: Dict[int, int] = {}

    def next(self) -> str:
        year = self.clock.now().year
        self._last[year] = self._last.get(year, 0) + 1
        return f"{self.prefix}-{year}-{self._last[year]:05d}"
