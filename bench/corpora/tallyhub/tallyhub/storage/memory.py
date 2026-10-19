"""In-memory tables used by the repositories."""

from __future__ import annotations

import copy
from typing import Callable, Dict, Generic, Iterator, List, Optional, TypeVar

from tallyhub.core.errors import NotFound

T = TypeVar("T")


class Table(Generic[T]):
    def __init__(self, name: str, key: Callable[[T], str]) -> None:
        self.name = name
        self._key = key
        self._rows: Dict[str, T] = {}

    def put(self, row: T) -> T:
        self._rows[self._key(row)] = row
        return row

    def get(self, key: str) -> T:
        try:
            return self._rows[key]
        except KeyError:
            raise NotFound(self.name, key) from None

    def find(self, key: str) -> Optional[T]:
        return self._rows.get(key)

    def where(self, predicate: Callable[[T], bool]) -> List[T]:
        return [row for row in self._rows.values() if predicate(row)]

    def delete(self, key: str) -> None:
        self._rows.pop(key, None)

    def __iter__(self) -> Iterator[T]:
        return iter(list(self._rows.values()))

    def __len__(self) -> int:
        return len(self._rows)

    def snapshot(self) -> Dict[str, T]:
        return copy.deepcopy(self._rows)

    def restore(self, rows: Dict[str, T]) -> None:
        self._rows = rows
