"""All-or-nothing changes across several tables."""

from __future__ import annotations

from typing import Any, Dict, List

from tallyhub.storage.memory import Table


class UnitOfWork:
    """Snapshots the given tables on enter and restores them if the block raises."""

    def __init__(self, *tables: Table) -> None:
        self.tables: List[Table] = list(tables)
        self._saved: Dict[int, Dict[str, Any]] = {}

    def __enter__(self) -> "UnitOfWork":
        self._saved = {id(t): t.snapshot() for t in self.tables}
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            for table in self.tables:
                table.restore(self._saved[id(table)])
        self._saved = {}
        return False
