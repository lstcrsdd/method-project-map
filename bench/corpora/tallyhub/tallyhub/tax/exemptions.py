"""Tax-exemption certificates: who may buy tax-free, and where."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from tallyhub.customers.models import Customer
from tallyhub.tax.jurisdictions import Jurisdiction

# Certificate id formats per (country, region); "" region means country-wide.
CERTIFICATE_FORMATS: Dict[Tuple[str, str], str] = {
    ("US", "CA"): r"CA-\d{6}",
    ("US", "NY"): r"NY-\d{8}",
    ("US", "TX"): r"TX-\d{6,8}",
    ("DE", ""): r"DE\d{9}",
}
DEFAULT_FORMAT = r"[A-Z]{2}-?\d{5,10}"


@dataclass(frozen=True)
class ExemptionDecision:
    exempt: bool
    reason: str


class ExemptionPolicy:
    def certificate_format(self, jurisdiction: Jurisdiction) -> str:
        return (
            CERTIFICATE_FORMATS.get((jurisdiction.country, jurisdiction.region))
            or CERTIFICATE_FORMATS.get((jurisdiction.country, ""))
            or DEFAULT_FORMAT
        )

    def decide(self, customer: Customer, certificate_id: Optional[str], jurisdiction: Jurisdiction) -> ExemptionDecision:
        if not certificate_id:
            return ExemptionDecision(False, "no exemption certificate on the order")
        if not jurisdiction.exemptions_allowed:
            return ExemptionDecision(False, f"{jurisdiction.code} does not accept exemption certificates")
        cert = customer.certificate(certificate_id)
        if cert is None:
            return ExemptionDecision(False, f"certificate {certificate_id} is not registered for {customer.id}")
        if not cert.active:
            return ExemptionDecision(False, f"certificate {certificate_id} is inactive")
        if cert.country != jurisdiction.country or (jurisdiction.region and cert.region != jurisdiction.region):
            return ExemptionDecision(False, f"certificate {certificate_id} is not valid in {jurisdiction.code}")
        if not re.fullmatch(self.certificate_format(jurisdiction), certificate_id):
            return ExemptionDecision(False, f"certificate {certificate_id} has an invalid format")
        return ExemptionDecision(True, f"exempt under certificate {certificate_id}")
