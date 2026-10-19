"""API key authentication."""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional

from tallyhub.core.errors import Unauthorized
from tallyhub.core.settings import Settings

SCOPES = {"orders:read", "orders:write", "refunds:write", "reports:read"}


class ApiKeyAuth:
    """Keys come from `api.keys` ({key: [scopes]}). With no keys configured the API is open (development)."""

    def __init__(self, settings: Settings) -> None:
        self.keys: Dict[str, List[str]] = settings.get("api.keys", {}) or {}

    def check(self, headers: Optional[Mapping[str, str]], scope: str) -> None:
        if not self.keys:
            return
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        key = headers.get("x-api-key", "")
        scopes = self.keys.get(key)
        if scopes is None:
            raise Unauthorized("missing or unknown API key")
        if scope not in scopes and "*" not in scopes:
            raise Unauthorized(f"API key lacks scope {scope}")
