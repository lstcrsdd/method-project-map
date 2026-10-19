"""Customer accounts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from tallyhub.core.money import Money


@dataclass
class TaxCertificate:
    """Proof of tax exemption registered for a customer in one region."""

    certificate_id: str
    country: str
    region: str
    active: bool = True


@dataclass
class Customer:
    id: str
    name: str
    email: str
    country: str
    region: str = ""
    segment: str = "retail"  # retail | wholesale | staff
    loyalty_tier: Optional[str] = None  # silver | gold | platinum
    certificates: List[TaxCertificate] = field(default_factory=list)
    store_credit: Optional[Money] = None
    locale: str = "en_US"

    def certificate(self, certificate_id: str) -> Optional[TaxCertificate]:
        for cert in self.certificates:
            if cert.certificate_id == certificate_id:
                return cert
        return None
