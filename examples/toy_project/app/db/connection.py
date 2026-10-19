"""Low-level database connection and storage."""

_STORAGE: list[dict] = []


class DatabaseConnection:
    """In-memory database connection handle."""

    def execute_insert(self, record: dict) -> int:
        """Insert a raw record into storage and return its assigned ID."""
        record_id = len(_STORAGE) + 1
        record["id"] = record_id
        _STORAGE.append(record)
        return record_id


def execute_raw_sql(query: str) -> list[dict]:
    """Execute a raw query directly against the storage engine."""
    if not query:
        return []
    return list(_STORAGE)
