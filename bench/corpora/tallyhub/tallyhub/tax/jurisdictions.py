"""Tax jurisdictions and address resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from tallyhub.orders.models import Address


@dataclass(frozen=True)
class Jurisdiction:
    code: str
    name: str
    country: str
    region: str = ""
    rates: Dict[str, Decimal] = field(default_factory=dict)  # tax category -> rate
    tax_shipping: bool = False  # shipping fees are taxed at the standard rate
    exemptions_allowed: bool = True

    def rate_for(self, category: str) -> Decimal:
        if category in self.rates:
            return self.rates[category]
        return self.rates.get("standard", Decimal(0))


class JurisdictionResolver:
    """Finds the jurisdiction of an address: an exact region match first, then the whole country."""

    def __init__(self) -> None:
        self._by_key: Dict[Tuple[str, str], Jurisdiction] = {}

    def add(self, jurisdiction: Jurisdiction) -> None:
        self._by_key[(jurisdiction.country, jurisdiction.region)] = jurisdiction

    def resolve(self, address: Address) -> Optional[Jurisdiction]:
        country = address.country.upper()
        region = (address.region or "").upper()
        return self._by_key.get((country, region)) or self._by_key.get((country, ""))

    def all(self) -> List[Jurisdiction]:
        return sorted(self._by_key.values(), key=lambda j: j.code)
