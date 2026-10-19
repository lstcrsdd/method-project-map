"""Builds `tallyhub-large`: the hand-written tallyhub core plus ~30 generated business domains.

The generated domains are ordinary, working code wired into the core (money, tax jurisdictions,
registries, stock, coupons, customers) and covered by their own tests. They share the vocabulary of
the benchmark symptoms (tax, refunds, shipping, coupons, store credit, exemption certificates), so text
search for a symptom returns many plausible-looking places, as in a real large codebase.

Deterministic: the same script always produces the same corpus.

Usage: python3 bench/corpora/gen_large.py <dest-dir>
"""

from __future__ import annotations

import random
import re
import shutil
import sys
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORE = HERE / "tallyhub"

# package, description, entities, (money field 1, money field 2), integrations, statuses
DOMAINS = [
    ("subscriptions", "Recurring subscriptions and billing cycles", ["Subscription", "BillingCycle", "PlanChange"],
     ("setup_fee", "monthly_fee"), {"tax", "fx"}, ["trialing", "active", "paused", "cancelled"]),
    ("marketplace", "Third-party seller marketplace", ["Seller", "Listing", "Payout"],
     ("commission", "shipping_fee"), {"tax", "fx"}, ["pending", "live", "suspended", "closed"]),
    ("returns", "Return merchandise authorisations", ["ReturnRequest", "Inspection", "Restock"],
     ("refund_total", "restocking_fee"), {"stock", "refund"}, ["requested", "received", "inspected", "closed"]),
    ("fraud", "Fraud screening of orders", ["Screening", "RiskSignal", "ManualReview"],
     ("exposure", "chargeback_fee"), set(), ["queued", "scoring", "flagged", "cleared"]),
    ("b2b_contracts", "Negotiated B2B contracts", ["Contract", "PriceAgreement", "CreditLine"],
     ("credit_limit", "minimum_spend"), {"tax", "customers", "exempt"}, ["draft", "signed", "active", "expired"]),
    ("procurement", "Supplier purchase orders", ["Supplier", "PurchaseOrder", "GoodsReceipt"],
     ("unit_cost", "freight"), {"stock", "fx"}, ["draft", "ordered", "received", "cancelled"]),
    ("transfers", "Inter-warehouse stock transfers", ["Transfer", "TransferLeg", "Discrepancy"],
     ("handling_fee", "freight"), {"stock"}, ["planned", "in_transit", "arrived", "cancelled"]),
    ("promotions", "Marketing campaign promotions", ["Campaign", "PromoCode", "PromoRedemption"],
     ("budget", "discount_amount"), {"coupons", "pricing_rule"}, ["draft", "running", "paused", "ended"]),
    ("points", "Loyalty points ledger", ["PointsAccount", "Accrual", "PointsRedemption"],
     ("point_value", "bonus_value"), {"customers"}, ["open", "active", "frozen", "closed"]),
    ("preorders", "Pre-orders and deposits", ["Preorder", "Deposit", "PreorderAllocation"],
     ("deposit", "balance_due"), {"stock", "tax"}, ["reserved", "confirmed", "fulfilled", "cancelled"]),
    ("rentals", "Equipment rentals", ["Rental", "RentalPeriod", "DamageClaim"],
     ("daily_rate", "security_deposit"), {"tax", "refund"}, ["booked", "out", "returned", "cancelled"]),
    ("affiliates", "Affiliate referrals", ["Affiliate", "Referral", "CommissionStatement"],
     ("commission", "bonus"), {"fx"}, ["applied", "approved", "suspended", "terminated"]),
    ("payment_terms", "Net payment terms for business customers", ["TermsAccount", "OpenItem", "TermsStatement"],
     ("late_fee", "credit_limit"), {"customers", "tax", "exempt"}, ["pending", "approved", "on_hold", "closed"]),
    ("dunning", "Collections for overdue invoices", ["DunningCase", "Reminder", "PaymentPlan"],
     ("penalty", "outstanding"), set(), ["open", "reminded", "escalated", "resolved"]),
    ("customs", "Customs declarations and import duties", ["Declaration", "TariffLine", "DutyAssessment"],
     ("duty", "import_tax"), {"tax", "fx"}, ["draft", "filed", "assessed", "released"]),
    ("donations", "Checkout donations", ["Charity", "Donation", "DonationReceipt"],
     ("donation", "matching"), set(), ["pledged", "collected", "receipted", "refunded"]),
    ("insurance", "Shipping insurance", ["InsurancePolicy", "Coverage", "InsuranceClaim"],
     ("premium", "insured_value"), {"tax", "refund"}, ["quoted", "bound", "claimed", "lapsed"]),
    ("quotes", "Sales quotes and RFQs", ["QuoteRequest", "QuoteLine", "QuoteRevision"],
     ("list_price", "negotiated_price"), {"tax", "coupons", "exempt"}, ["received", "priced", "sent", "accepted"]),
    ("gifting", "Gift wrapping and messages", ["GiftOrder", "WrapOption", "GiftMessage"],
     ("wrap_fee", "card_fee"), {"tax"}, ["new", "wrapped", "shipped", "cancelled"]),
    ("bundling", "Dynamic bundle builder", ["BundleTemplate", "BundleSlot", "BundleQuote"],
     ("slot_price", "bundle_discount"), {"coupons", "pricing_rule"}, ["draft", "published", "retired", "archived"]),
    ("warranties", "Extended warranties", ["Warranty", "WarrantyPlan", "RepairTicket"],
     ("plan_price", "deductible"), {"tax", "refund"}, ["offered", "active", "claimed", "expired"]),
    ("pickups", "Store pickup and lockers", ["PickupPoint", "PickupSlot", "Locker"],
     ("handling_fee", "storage_fee"), {"stock"}, ["open", "booked", "collected", "expired"]),
    ("audit", "Audit trail and compliance exports", ["AuditEntry", "RetentionRule", "ExportJob"],
     ("amount_logged", "threshold"), set(), ["recorded", "reviewed", "exported", "purged"]),
    ("pricing_experiments", "A/B price experiments", ["Experiment", "Variant", "Assignment"],
     ("price_delta", "revenue"), {"fx", "pricing_rule"}, ["draft", "running", "stopped", "concluded"]),
    ("catalog_feeds", "Product feeds for external marketplaces", ["Feed", "FeedItem", "FeedRun"],
     ("listed_price", "shipping_fee"), {"fx", "tax"}, ["configured", "syncing", "failed", "disabled"]),
    ("price_matching", "Competitor price matching", ["MatchRequest", "CompetitorPrice", "MatchDecision"],
     ("competitor_price", "refund_due"), {"coupons", "refund"}, ["submitted", "verified", "rejected", "paid"]),
    ("store_credit_programs", "Store credit campaigns", ["CreditGrant", "CreditCampaign", "ExpiryRun"],
     ("credit_amount", "expired_amount"), {"customers", "refund"}, ["scheduled", "granted", "expired", "revoked"]),
    ("carrier_contracts", "Negotiated carrier rates", ["CarrierContract", "RateCard", "Surcharge"],
     ("base_rate", "fuel_surcharge"), {"fx"}, ["negotiating", "active", "suspended", "expired"]),
    ("tax_filings", "Periodic sales tax filings", ["Filing", "FilingLine", "Remittance"],
     ("taxable_sales", "tax_due"), {"tax"}, ["open", "prepared", "filed", "amended"]),
    ("exemption_registry", "Tax exemption certificate intake", ["CertificateRequest", "Verification", "Renewal"],
     ("exempt_sales", "review_fee"), {"tax", "customers", "exempt"}, ["submitted", "verified", "rejected", "expired"]),
    ("shipping_rules", "Shipping restriction and surcharge rules", ["Restriction", "ZoneRule", "ShippingOverride"],
     ("shipping_fee", "surcharge"), {"tax"}, ["draft", "active", "paused", "retired"]),
    ("refund_requests", "Customer refund request portal", ["RefundRequest", "RefundApproval", "RefundBatch"],
     ("requested_amount", "approved_amount"), {"customers", "refund"}, ["submitted", "approved", "paid", "declined"]),
]

CATEGORIES = ["standard", "reduced", "digital", "zero"]
REGIONS = ["US", "DE", "GB", "JP", "CA", "SE"]


def camel(snake: str) -> str:
    return "".join(part.capitalize() for part in snake.split("_"))


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def plural(word: str) -> str:
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "ch", "sh")):
        return word + "es"
    return word + "s"


def dedent(text: str) -> str:
    """Dedent a template block; leading newlines are kept so appended blocks stay separated."""
    return textwrap.dedent(text)


def header(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


class Domain:
    def __init__(self, spec: tuple) -> None:
        self.pkg, self.desc, self.entities, (self.m1, self.m2), self.integrations, self.statuses = spec
        self.title = camel(self.pkg)
        self.rng = random.Random(zlib.crc32(self.pkg.encode()))
        self.e1 = self.entities[0]
        self.prefix = "".join(w[0] for w in self.pkg.split("_"))[:3] + self.pkg[1:3]
        self.rate = self.rng.choice(["0.015", "0.025", "0.04", "0.05", "0.08", "0.12"])
        self.limit = self.rng.choice(["5000", "10000", "25000", "50000"])
        self.v1 = self.rng.choice(["120.00", "75.50", "240.00", "18.90", "999.00"])
        self.v2 = self.rng.choice(["15.50", "4.99", "32.00", "7.25", "60.00"])

    def has(self, feature: str) -> bool:
        return feature in self.integrations

    # ------------------------------------------------------------------ files

    def models(self) -> str:
        out = [header(f'''
            """{self.desc}: entities."""

            from __future__ import annotations

            from dataclasses import dataclass, field, replace
            from datetime import datetime
            from decimal import Decimal
            from typing import List, Optional

            from tallyhub.core.money import Money

            STATUSES = {tuple(self.statuses)!r}
            OPEN_STATUSES = {tuple(self.statuses[:3])!r}
            ''')]
        gross_forms = [
            f"self.{self.m1} + self.{self.m2} * self.quantity",
            f"self.{self.m1} * self.quantity + self.{self.m2}",
            f"(self.{self.m1} + self.{self.m2}) * self.quantity",
        ]
        for i, entity in enumerate(self.entities):
            category = self.rng.choice(CATEGORIES[:3])
            region = self.rng.choice(REGIONS)
            extra = self.rng.choice([
                f'''
                def {self.m1}_shares(self, parts: int) -> List[Money]:
                    """Split {self.m1.replace("_", " ")} into equal parts that add up exactly."""
                    return self.{self.m1}.allocate([1] * max(parts, 1))
                ''',
                f'''
                def exceeds(self, limit: Money) -> bool:
                    return limit < self.gross()
                ''',
                f'''
                def weighted_{self.m2}(self) -> Money:
                    return Money.raw(self.{self.m2}.amount * self.rate, self.currency).rounded()
                ''',
            ])
            out.append(dedent(f'''


                @dataclass
                class {entity}:
                    """{self.desc}: {snake(entity).replace("_", " ")} record."""

                    id: str
                    customer_id: str
                    currency: str
                    {self.m1}: Money
                    {self.m2}: Money
                    quantity: int = 1
                    rate: Decimal = Decimal("{self.rate}")
                    region: str = "{region}"
                    category: str = "{category}"
                    status: str = "{self.statuses[0]}"
                    created_at: Optional[datetime] = None
                    parent_id: Optional[str] = None
                    tags: List[str] = field(default_factory=list)

                    @property
                    def is_open(self) -> bool:
                        return self.status in OPEN_STATUSES

                    def gross(self) -> Money:
                        return {gross_forms[(i + len(self.pkg)) % len(gross_forms)]}

                    def with_status(self, status: str) -> "{entity}":
                        if status not in STATUSES:
                            raise ValueError(f"unknown status {{status!r}}")
                        return replace(self, status=status)
                '''))
            out.append("\n" + textwrap.indent(dedent(extra).strip("\n") + "\n", "    "))
        return "".join(out).rstrip() + "\n"

    def events(self) -> str:
        out = [header(f'''
            """{self.desc}: domain events."""

            from __future__ import annotations

            from dataclasses import dataclass

            from tallyhub.core.events import Event
            ''')]
        for entity in self.entities:
            key = snake(entity)
            out.append(dedent(f'''


                @dataclass(frozen=True)
                class {entity}Created(Event):
                    {key}_id: str
                    customer_id: str


                @dataclass(frozen=True)
                class {entity}StatusChanged(Event):
                    {key}_id: str
                    old: str
                    new: str
                '''))
        return "".join(out)

    def repository(self) -> str:
        names = ", ".join(self.entities)
        lines = [header(f'''
            """{self.desc}: storage."""

            from __future__ import annotations

            from typing import List

            from tallyhub.storage.memory import Table
            from tallyhub.{self.pkg}.models import {names}


            class {self.title}Repository:
                def __init__(self) -> None:
            ''')]
        for entity in self.entities:
            key = snake(entity)
            lines.append(f'        self.{plural(key)}: Table = Table("{key}", key=lambda row: row.id)\n')
        for entity in self.entities:
            key, many = snake(entity), plural(snake(entity))
            lines.append(textwrap.indent(dedent(f'''

                def add_{key}(self, item: {entity}) -> {entity}:
                    return self.{many}.put(item)

                def get_{key}(self, item_id: str) -> {entity}:
                    return self.{many}.get(item_id)

                def {many}_for_customer(self, customer_id: str) -> List[{entity}]:
                    return sorted(self.{many}.where(lambda row: row.customer_id == customer_id), key=lambda row: row.id)

                def open_{many}(self) -> List[{entity}]:
                    return [row for row in self.{many} if row.is_open]
                '''), "    "))
        return "".join(lines)

    def calculators(self) -> str:
        r = self.rng
        thresholds = sorted(r.sample(["50", "100", "250", "500", "1000", "2500"], 2), key=float)
        rates = sorted(r.sample(["0.01", "0.02", "0.03", "0.05", "0.07", "0.1"], 3), key=float, reverse=True)
        upper = self.m1.upper()
        text = header(f'''
            """{self.desc}: money calculations."""

            from __future__ import annotations

            from decimal import Decimal
            from typing import List, Optional, Sequence, Tuple

            from tallyhub.core.money import Money

            # (threshold, rate): the rate of the highest threshold not above the base amount applies.
            {upper}_SCHEDULE: List[Tuple[str, str]] = [("0", "{rates[0]}"), ("{thresholds[0]}", "{rates[1]}"), ("{thresholds[1]}", "{rates[2]}")]


            def {self.m1}_rate(base: Money) -> Decimal:
                rate = Decimal(0)
                for threshold, value in {upper}_SCHEDULE:
                    if base.amount >= Decimal(threshold):
                        rate = Decimal(value)
                return rate


            def {self.m1}_for(base: Money, *, minimum: Optional[Money] = None, cap: Optional[Money] = None,
                         mode: str = "half_up") -> Money:
                amount = Money.raw(base.amount * {self.m1}_rate(base), base.currency).rounded(mode)
                if minimum is not None and amount < minimum:
                    amount = minimum
                if cap is not None and cap < amount:
                    amount = cap
                return amount


            def prorate_{self.m2}(amount: Money, used_days: int, period_days: int = 30) -> Money:
                if period_days <= 0:
                    raise ValueError("period_days must be positive")
                used = max(0, min(used_days, period_days))
                return Money.raw(amount.amount * Decimal(used) / Decimal(period_days), amount.currency).rounded()


            def split_{self.m2}(amount: Money, weights: Sequence[int]) -> List[Money]:
                return amount.allocate(list(weights) or [1])
            ''')
        if self.has("tax"):
            text += dedent(f'''


                def tax_on_{self.m1}(amount: Money, rate: Decimal, mode: str = "half_up") -> Money:
                    """Tax on a {self.m1.replace("_", " ")}, rounded to the currency's minor unit."""
                    return Money.raw(amount.amount * rate, amount.currency).rounded(mode)
                ''')
        if self.has("refund"):
            text += dedent(f'''


                def refundable_{self.m2}(paid: Money, already_refunded: Money) -> Money:
                    remaining = paid - already_refunded
                    return remaining if not remaining.is_negative() else Money.zero(paid.currency)
                ''')
        return text

    def policies(self) -> str:
        r = self.rng
        review_status = self.statuses[2]
        qty_limit = r.choice([10, 25, 50, 100])
        blocked = sorted(r.sample(["XX", "ZZ", "KP", "IR", "SY"], 2))
        text = header(f'''
            """{self.desc}: business rules evaluated before quoting."""

            from __future__ import annotations

            from abc import ABC, abstractmethod
            from dataclasses import dataclass
            from decimal import Decimal
            from typing import Any, Dict, List, Optional

            from tallyhub.core.money import Money
            from tallyhub.core.registry import Registry
            from tallyhub.{self.pkg}.models import {self.e1}

            {self.pkg.upper()}_RULES: Registry = Registry("{self.pkg} rule")


            @dataclass(frozen=True)
            class Decision:
                rule: str
                allowed: bool
                reason: str = ""
                adjustment: Optional[Money] = None


            class {self.title}Rule(ABC):
                name = "abstract"

                def __init__(self, settings: Dict[str, Any]) -> None:
                    self.settings = settings

                def applies(self, item: {self.e1}) -> bool:
                    return True

                @abstractmethod
                def evaluate(self, item: {self.e1}) -> Decision:
                    ...


            @{self.pkg.upper()}_RULES.register("max_{self.m1}")
            class Max{camel(self.m1)}Rule({self.title}Rule):
                name = "max_{self.m1}"

                def evaluate(self, item: {self.e1}) -> Decision:
                    limit = Money.of(self.settings.get("max_{self.m1}", "{self.limit}"), item.currency)
                    if limit < item.{self.m1}:
                        return Decision(self.name, False, f"{self.m1} {{item.{self.m1}}} exceeds {{limit}}")
                    return Decision(self.name, True)


            @{self.pkg.upper()}_RULES.register("blocked_regions")
            class BlockedRegionsRule({self.title}Rule):
                name = "blocked_regions"

                def evaluate(self, item: {self.e1}) -> Decision:
                    blocked = self.settings.get("blocked_regions", {blocked!r})
                    if item.region in blocked:
                        return Decision(self.name, False, f"region {{item.region}} is not served")
                    return Decision(self.name, True)


            @{self.pkg.upper()}_RULES.register("quantity_limit")
            class QuantityLimitRule({self.title}Rule):
                name = "quantity_limit"

                def evaluate(self, item: {self.e1}) -> Decision:
                    limit = int(self.settings.get("max_quantity", {qty_limit}))
                    if item.quantity > limit:
                        return Decision(self.name, False, f"quantity {{item.quantity}} is above {{limit}}")
                    return Decision(self.name, True)


            @{self.pkg.upper()}_RULES.register("{review_status}_review")
            class ReviewRule({self.title}Rule):
                name = "{review_status}_review"

                def applies(self, item: {self.e1}) -> bool:
                    return item.status == "{review_status}"

                def evaluate(self, item: {self.e1}) -> Decision:
                    return Decision(self.name, False, "needs manual review while {review_status}")


            @{self.pkg.upper()}_RULES.register("volume_{self.m2}")
            class Volume{camel(self.m2)}Rule({self.title}Rule):
                name = "volume_{self.m2}"

                def applies(self, item: {self.e1}) -> bool:
                    return item.quantity >= int(self.settings.get("volume_threshold", {qty_limit // 5}))

                def evaluate(self, item: {self.e1}) -> Decision:
                    pct = Decimal(str(self.settings.get("volume_pct", "0.05")))
                    return Decision(self.name, True, "volume adjustment", -(item.{self.m2} * pct).rounded())


            def evaluate_all(item: {self.e1}, settings: Dict[str, Any], names: Optional[List[str]] = None) -> List[Decision]:
                decisions = []
                for name in names or {self.pkg.upper()}_RULES.names():
                    rule = {self.pkg.upper()}_RULES.get(name)(settings)
                    if rule.applies(item):
                        decisions.append(rule.evaluate(item))
                return decisions
            ''')
        if self.has("pricing_rule"):
            rule_name = {"promotions": "campaign", "bundling": "dynamic_bundle",
                         "pricing_experiments": "experiment_price"}[self.pkg]
            text += dedent(f'''


                # Optional checkout pricing step; enable it by adding "{rule_name}" to settings `pricing.rules`.
                from tallyhub.core.registry import PRICING_RULES  # noqa: E402
                from tallyhub.pricing.breakdown import Adjustment, PriceBreakdown  # noqa: E402
                from tallyhub.pricing.context import PricingContext  # noqa: E402
                from tallyhub.pricing.rules.base import PricingRule  # noqa: E402


                @PRICING_RULES.register("{rule_name}")
                class {camel(rule_name)}PricingRule(PricingRule):
                    name = "{rule_name}"

                    def applies(self, breakdown: PriceBreakdown, ctx: PricingContext) -> bool:
                        if breakdown.has_adjustment_from("coupon"):
                            return False
                        return "{rule_name}" in ctx.flags

                    def apply(self, breakdown: PriceBreakdown, ctx: PricingContext) -> None:
                        pct = Decimal(str(self.deps.settings.get("{self.pkg}.checkout_pct", "{self.rate}")))
                        base = breakdown.lines_net + breakdown.order_discount
                        breakdown.add_order_adjustment(Adjustment(self.name, "{self.desc.lower()}", -(base * pct)))
                ''')
        return text

    def validators(self) -> str:
        return header(f'''
            """{self.desc}: payload validation."""

            from __future__ import annotations

            from typing import Any, Dict, List

            from tallyhub.core.money import CURRENCY_EXPONENTS

            REQUIRED = ("customer_id", "currency", "{self.m1}", "{self.m2}")


            def validate_payload(payload: Dict[str, Any], required=REQUIRED) -> List[str]:
                problems = [f"missing field {{name}}" for name in required if payload.get(name) in (None, "")]
                currency = str(payload.get("currency") or "").upper()
                if currency and currency not in CURRENCY_EXPONENTS:
                    problems.append(f"currency {{currency}} is not supported")
                quantity = payload.get("quantity", 1)
                if not isinstance(quantity, int) or quantity <= 0:
                    problems.append("quantity must be a positive integer")
                region = str(payload.get("region") or "")
                if region and (len(region) != 2 or not region.isalpha()):
                    problems.append(f"region {{region!r}} must be a two-letter country code")
                return problems
            ''')

    def serializers(self) -> str:
        names = ", ".join(self.entities)
        text = header(f'''
            """{self.desc}: JSON <-> entities."""

            from __future__ import annotations

            from typing import Any, Dict, Optional

            from tallyhub.core.clock import IdGenerator
            from tallyhub.core.money import Money
            from tallyhub.{self.pkg}.models import {names}


            def money_json(money: Money) -> Dict[str, str]:
                return {{"amount": str(money.amount), "currency": money.currency}}
            ''')
        for i, entity in enumerate(self.entities):
            key = snake(entity)
            extra = ""
            if self.has("exempt") and i == 0:
                extra = dedent('''
                    certificate = payload.get("tax_exempt_id")
                    if certificate:
                        item.tags.append(f"exempt:{certificate}")
                    ''')
            if self.has("coupons") and i == 0:
                extra += dedent('''
                    if payload.get("coupon_code"):
                        item.tags.append(f"coupon:{str(payload['coupon_code']).upper()}")
                    ''')
            text += dedent(f'''


                def parse_{key}(payload: Dict[str, Any], ids: IdGenerator, parent_id: Optional[str] = None) -> {entity}:
                    currency = str(payload["currency"]).upper()
                    item = {entity}(
                        id=ids.next("{self.prefix}{i}"),
                        customer_id=payload["customer_id"],
                        currency=currency,
                        {self.m1}=Money.of(payload["{self.m1}"], currency),
                        {self.m2}=Money.of(payload["{self.m2}"], currency),
                        quantity=int(payload.get("quantity", 1)),
                        region=str(payload.get("region") or "US").upper(),
                        category=payload.get("category", "standard"),
                        parent_id=parent_id,
                    )
                ''')
            text += textwrap.indent(extra, "    ")
            text += "    return item\n"
            text += dedent(f'''


                def {key}_json(item: {entity}) -> Dict[str, Any]:
                    return {{
                        "id": item.id,
                        "customer_id": item.customer_id,
                        "status": item.status,
                        "{self.m1}": money_json(item.{self.m1}),
                        "{self.m2}": money_json(item.{self.m2}),
                        "quantity": item.quantity,
                        "gross": money_json(item.gross()),
                        "region": item.region,
                        "parent_id": item.parent_id,
                        "tags": list(item.tags),
                    }}
                ''')
        return text

    def service(self) -> str:
        e1 = self.e1
        k1 = snake(e1)
        s = self.statuses
        imports = [
            "from decimal import Decimal",
            "from typing import Any, Dict, List, Optional",
            "",
            "from tallyhub.core.clock import Clock, IdGenerator",
            "from tallyhub.core.errors import InvalidTransition, ValidationError",
            "from tallyhub.core.events import EventBus",
            "from tallyhub.core.money import Money, sum_money",
            "from tallyhub.core.settings import Settings",
            "from tallyhub.orders.models import Address",
            f"from tallyhub.{self.pkg}.calculators import {self.m1}_for, prorate_{self.m2}, split_{self.m2}"
            + (f", tax_on_{self.m1}" if self.has("tax") else "")
            + (f", refundable_{self.m2}" if self.has("refund") else ""),
            f"from tallyhub.{self.pkg}.events import "
            + ", ".join(f"{e}Created, {e}StatusChanged" for e in self.entities),
            f"from tallyhub.{self.pkg}.models import " + ", ".join(self.entities),
            f"from tallyhub.{self.pkg}.policies import evaluate_all",
            f"from tallyhub.{self.pkg}.repository import {self.title}Repository",
            f"from tallyhub.{self.pkg}.serializers import " + ", ".join(f"parse_{snake(e)}" for e in self.entities),
            f"from tallyhub.{self.pkg}.validators import validate_payload",
        ]
        text = f'"""{self.desc}: application service."""\n\nfrom __future__ import annotations\n\n' + "\n".join(imports)
        text += dedent(f'''


            class {self.title}Service:
                TRANSITIONS = {{
                    "{s[0]}": {{"{s[1]}", "{s[3]}"}},
                    "{s[1]}": {{"{s[2]}", "{s[3]}"}},
                    "{s[2]}": {{"{s[1]}", "{s[3]}"}},
                    "{s[3]}": set(),
                }}

                def __init__(
                    self,
                    settings: Settings,
                    clock: Clock,
                    ids: IdGenerator,
                    events: EventBus,
                    repo: {self.title}Repository,
                    *,
                    jurisdictions: Any = None,
                    fx: Any = None,
                    customers: Any = None,
                    stock: Any = None,
                    coupons: Any = None,
                ) -> None:
                    self.settings = settings
                    self.clock = clock
                    self.ids = ids
                    self.events = events
                    self.repo = repo
                    self.jurisdictions = jurisdictions
                    self.fx = fx
                    self.customers = customers
                    self.stock = stock
                    self.coupons = coupons

                def rules_config(self) -> Dict[str, Any]:
                    return self.settings.get("{self.pkg}", {{}}) or {{}}

                # ---------------------------------------------------------------- {k1}

                def create_{k1}(self, payload: Dict[str, Any]) -> {e1}:
                    problems = validate_payload(payload)
                    if problems:
                        raise ValidationError(problems)
                    item = parse_{k1}(payload, self.ids)
                    item.created_at = self.clock.now()
                    self.repo.add_{k1}(item)
                    self.events.publish({e1}Created(item.id, item.customer_id))
                    return item

                def change_status(self, item_id: str, new_status: str) -> {e1}:
                    item = self.repo.get_{k1}(item_id)
                    if new_status not in self.TRANSITIONS.get(item.status, set()):
                        raise InvalidTransition(f"{k1} {{item_id}}: {{item.status}} -> {{new_status}} is not allowed")
                    updated = item.with_status(new_status)
                    self.repo.add_{k1}(updated)
                    self.events.publish({e1}StatusChanged(item.id, item.status, new_status))
                    return updated

                def blocked_reasons(self, item_id: str) -> List[str]:
                    item = self.repo.get_{k1}(item_id)
                    return [d.reason for d in evaluate_all(item, self.rules_config()) if not d.allowed]

                def quote_{k1}(self, item_id: str, address: Optional[Address] = None) -> Dict[str, Money]:
                    item = self.repo.get_{k1}(item_id)
                    gross = item.gross()
                    mode = self.settings.rounding_mode(item.currency)
                    charge = {self.m1}_for(gross, mode=mode)
                    decisions = evaluate_all(item, self.rules_config())
                    adjustments = [d.adjustment for d in decisions if d.adjustment is not None]
                    net = sum_money([gross, charge] + adjustments, item.currency)
                    result: Dict[str, Money] = {{"gross": gross, "{self.m1}_charge": charge, "net": net}}
            ''')
        if self.has("tax"):
            text += textwrap.indent(dedent(f'''
                    if self.jurisdictions is not None and address is not None:
                        jurisdiction = self.jurisdictions.resolve(address)
                        rate = jurisdiction.rate_for(item.category) if jurisdiction else Decimal(0)
                        exempt = any(tag.startswith("exempt:") for tag in item.tags)
                        result["tax"] = Money.zero(item.currency) if exempt else tax_on_{self.m1}(net, rate, mode)
                '''), "        ")
        if self.has("fx"):
            text += textwrap.indent(dedent('''
                    if self.fx is not None and item.currency != "USD":
                        result["net_usd"] = self.fx.convert(net, "USD")
                '''), "        ")
        text += "        return result\n"
        text += textwrap.indent(dedent(f'''

            def customer_exposure(self, customer_id: str, currency: str = "USD") -> Money:
                total = Money.zero(currency)
                for item in self.repo.{plural(k1)}_for_customer(customer_id):
                    if not item.is_open:
                        continue
                    gross = item.gross()
                    if gross.currency != currency:
                        if self.fx is None:
                            continue
                        gross = self.fx.convert(gross, currency)
                    total = total + gross
                return total

            def prorated_{self.m2}(self, item_id: str, used_days: int, period_days: int = 30) -> Money:
                return prorate_{self.m2}(self.repo.get_{k1}(item_id).{self.m2}, used_days, period_days)

            def split_{self.m2}_between(self, item_id: str, weights: List[int]) -> List[Money]:
                return split_{self.m2}(self.repo.get_{k1}(item_id).{self.m2}, weights)
            '''), "    ")
        if self.has("refund"):
            text += textwrap.indent(dedent(f'''

                def refund_sequence(self) -> List[str]:
                    """Payment methods in the order refunds go back to them (shared with billing)."""
                    return list(self.settings.get("billing.refund_order", []))

                def refundable(self, item_id: str, already_refunded: Optional[Money] = None) -> Money:
                    item = self.repo.get_{k1}(item_id)
                    return refundable_{self.m2}(item.{self.m2}, already_refunded or Money.zero(item.currency))
                '''), "    ")
        if self.has("stock"):
            text += textwrap.indent(dedent(f'''

                def reserve_stock(self, item_id: str, sku: str, warehouse: str) -> bool:
                    """Reserve units for the record if the warehouse can still promise them."""
                    item = self.repo.get_{k1}(item_id)
                    level = self.stock.level(sku, warehouse)
                    if level.available < item.quantity:
                        return False
                    self.stock.reserve(item.id, sku, warehouse, item.quantity, self.clock.now())
                    return True

                def release_stock(self, item_id: str) -> int:
                    return self.stock.release(item_id)
                '''), "    ")
        if self.has("customers"):
            text += textwrap.indent(dedent(f'''

                def customer_tier(self, item_id: str) -> Optional[str]:
                    return self.customers.get(self.repo.get_{k1}(item_id).customer_id).loyalty_tier

                def available_store_credit(self, item_id: str) -> Money:
                    item = self.repo.get_{k1}(item_id)
                    credit = self.customers.get(item.customer_id).store_credit
                    return credit if credit is not None and credit.currency == item.currency else Money.zero(item.currency)
                '''), "    ")
        if self.has("coupons"):
            text += textwrap.indent(dedent(f'''

                def coupon_discount(self, item_id: str, code: str) -> Money:
                    item = self.repo.get_{k1}(item_id)
                    coupon = self.coupons.find(code)
                    if coupon is None or not coupon.is_active(self.clock.now()):
                        return Money.zero(item.currency)
                    if coupon.kind == "percent":
                        return -(item.gross() * coupon.value).rounded()
                    if coupon.currency == item.currency:
                        return -Money.of(coupon.value, item.currency)
                    return Money.zero(item.currency)
                '''), "    ")
        for i, entity in enumerate(self.entities[1:], start=1):
            key = snake(entity)
            text += textwrap.indent(dedent(f'''

                # ---------------------------------------------------------------- {key}

                def add_{key}(self, parent_id: str, payload: Dict[str, Any]) -> {entity}:
                    parent = self.repo.get_{k1}(parent_id)
                    problems = validate_payload(payload, required=("{self.m1}", "{self.m2}"))
                    if problems:
                        raise ValidationError(problems)
                    data = dict(payload, customer_id=parent.customer_id, currency=parent.currency)
                    item = parse_{key}(data, self.ids, parent_id=parent.id)
                    item.created_at = self.clock.now()
                    self.repo.add_{key}(item)
                    self.events.publish({entity}Created(item.id, item.customer_id))
                    return item

                def {plural(key)}_total(self, parent_id: str) -> Money:
                    parent = self.repo.get_{k1}(parent_id)
                    children = self.repo.{plural(key)}.where(lambda row: row.parent_id == parent_id)
                    return sum_money([child.gross() for child in children], parent.currency)
                '''), "    ")
        return text

    def handlers(self) -> str:
        k1 = snake(self.e1)
        many = plural(k1)
        return header(f'''
            """{self.desc}: HTTP handlers (same conventions as tallyhub.api.handlers)."""

            from __future__ import annotations

            import re
            from typing import Any, Dict, Optional, Tuple

            from tallyhub.api.serializers import parse_address
            from tallyhub.core.errors import InvalidTransition, NotFound, ValidationError
            from tallyhub.{self.pkg}.serializers import {k1}_json, money_json
            from tallyhub.{self.pkg}.service import {self.title}Service

            Response = Tuple[int, Any]


            class {self.title}Api:
                def __init__(self, service: {self.title}Service) -> None:
                    self.service = service
                    self.routes = [
                        ("POST", re.compile(r"^/{self.pkg}/{many}$"), self.create),
                        ("GET", re.compile(r"^/{self.pkg}/{many}/(?P<item_id>[\\w-]+)$"), self.get),
                        ("POST", re.compile(r"^/{self.pkg}/{many}/(?P<item_id>[\\w-]+)/status$"), self.status),
                        ("POST", re.compile(r"^/{self.pkg}/{many}/(?P<item_id>[\\w-]+)/quote$"), self.quote),
                    ]

                def handle(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Response:
                    for verb, pattern, handler in self.routes:
                        match = pattern.match(path)
                        if verb == method.upper() and match:
                            try:
                                return handler(body or {{}}, **match.groupdict())
                            except ValidationError as exc:
                                return 422, {{"error": "ValidationError", "detail": exc.problems}}
                            except NotFound as exc:
                                return 404, {{"error": "NotFound", "detail": str(exc)}}
                            except InvalidTransition as exc:
                                return 409, {{"error": "InvalidTransition", "detail": str(exc)}}
                    return 404, {{"error": "NoRoute", "detail": f"{{method}} {{path}}"}}

                def create(self, body: Dict[str, Any]) -> Response:
                    return 201, {k1}_json(self.service.create_{k1}(body))

                def get(self, body: Dict[str, Any], item_id: str) -> Response:
                    return 200, {k1}_json(self.service.repo.get_{k1}(item_id))

                def status(self, body: Dict[str, Any], item_id: str) -> Response:
                    return 200, {k1}_json(self.service.change_status(item_id, body.get("status", "")))

                def quote(self, body: Dict[str, Any], item_id: str) -> Response:
                    address = parse_address(body["shipping_address"]) if body.get("shipping_address") else None
                    quote = self.service.quote_{k1}(item_id, address)
                    return 200, {{name: money_json(amount) for name, amount in quote.items()}}
            ''')

    def reports(self) -> str:
        k1 = snake(self.e1)
        return header(f'''
            """{self.desc}: reporting."""

            from __future__ import annotations

            from typing import Any, Dict

            from tallyhub.reports.aggregations import group_by, sum_by_currency
            from tallyhub.{self.pkg}.repository import {self.title}Repository


            def {k1}_summary(repo: {self.title}Repository) -> Dict[str, Any]:
                rows = list(repo.{plural(k1)})
                by_status = {{status: len(items) for status, items in group_by(rows, lambda r: r.status).items()}}
                open_gross = sum_by_currency(r.gross() for r in rows if r.is_open)
                return {{"count": len(rows), "by_status": by_status, "open_gross": {{c: str(m.amount) for c, m in open_gross.items()}}}}
            ''')

    def tests(self) -> str:
        k1 = snake(self.e1)
        s = self.statuses
        second = snake(self.entities[1])
        return header(f'''
            import unittest

            from tallyhub.core.errors import InvalidTransition, ValidationError
            from tallyhub.{self.pkg}.handlers import {self.title}Api
            from tallyhub.{self.pkg}.reports import {k1}_summary
            from tests.helpers import CA, make_app


            class {self.title}Test(unittest.TestCase):
                def setUp(self):
                    self.app = make_app()
                    self.service = self.app.ext.{self.pkg}

                def payload(self, **extra):
                    body = {{"customer_id": "c_bob", "currency": "USD", "{self.m1}": "{self.v1}", "{self.m2}": "{self.v2}",
                            "quantity": 2, "region": "US"}}
                    body.update(extra)
                    return body

                def test_create_and_quote(self):
                    item = self.service.create_{k1}(self.payload())
                    quote = self.service.quote_{k1}(item.id, CA)
                    self.assertEqual(quote["gross"], item.gross())
                    self.assertEqual(quote["net"].currency, "USD")

                def test_status_flow(self):
                    item = self.service.create_{k1}(self.payload())
                    self.assertEqual(self.service.change_status(item.id, "{s[1]}").status, "{s[1]}")
                    with self.assertRaises(InvalidTransition):
                        self.service.change_status(item.id, "{s[0]}")

                def test_validation(self):
                    with self.assertRaises(ValidationError):
                        self.service.create_{k1}(self.payload(currency="XXX", quantity=0))

                def test_children_total(self):
                    item = self.service.create_{k1}(self.payload())
                    self.service.add_{second}(item.id, {{"{self.m1}": "1.00", "{self.m2}": "2.00"}})
                    self.assertEqual(self.service.{plural(second)}_total(item.id).currency, "USD")

                def test_api_and_report(self):
                    api = {self.title}Api(self.service)
                    status, body = api.handle("POST", "/{self.pkg}/{plural(k1)}", self.payload())
                    self.assertEqual(status, 201)
                    self.assertEqual(api.handle("GET", f"/{self.pkg}/{plural(k1)}/{{body['id']}}")[0], 200)
                    self.assertEqual({k1}_summary(self.service.repo)["count"], 1)


            if __name__ == "__main__":
                unittest.main()
            ''')

    def write(self, root: Path) -> None:
        pkg_dir = root / "tallyhub" / self.pkg
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "__init__.py").write_text(f'"""{self.desc}."""\n', encoding="utf-8")
        for name in ("models", "events", "repository", "calculators", "policies", "validators", "serializers",
                     "service", "handlers", "reports"):
            (pkg_dir / f"{name}.py").write_text(getattr(self, name)(), encoding="utf-8")
        (root / "tests" / f"test_ext_{self.pkg}.py").write_text(self.tests(), encoding="utf-8")


def extensions_module(domains: list[Domain]) -> str:
    lines = [
        '"""Business extensions installed on every app (see build_app)."""',
        "",
        "from __future__ import annotations",
        "",
        "from types import SimpleNamespace",
        "",
    ]
    for d in domains:
        lines.append(f"from tallyhub.{d.pkg}.repository import {d.title}Repository")
        lines.append(f"from tallyhub.{d.pkg}.service import {d.title}Service")
    lines += ["", "", "def install_extensions(app) -> None:", "    ext = SimpleNamespace()"]
    for d in domains:
        kwargs = []
        if d.has("tax"):
            kwargs.append("jurisdictions=app.jurisdictions")
        if d.has("fx"):
            kwargs.append("fx=app.fx")
        if d.has("customers"):
            kwargs.append("customers=app.customers")
        if d.has("stock"):
            kwargs.append("stock=app.stock")
        if d.has("coupons"):
            kwargs.append("coupons=app.coupons")
        extra = (", " + ", ".join(kwargs)) if kwargs else ""
        lines.append(
            f"    ext.{d.pkg} = {d.title}Service(app.settings, app.clock, app.ids, app.events, "
            f"{d.title}Repository(){extra})"
        )
    lines.append("    app.ext = ext")
    return "\n".join(lines) + "\n"


def build(dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(CORE, dest, ignore=shutil.ignore_patterns("__pycache__", "._*", ".pmap"))
    domains = [Domain(spec) for spec in DOMAINS]
    for domain in domains:
        domain.write(dest)
    (dest / "tallyhub" / "extensions.py").write_text(extensions_module(domains), encoding="utf-8")

    app_py = dest / "tallyhub" / "app.py"
    text = app_py.read_text(encoding="utf-8")
    anchor = "    if seed:\n        from tallyhub.seed import seed_demo_data\n"
    assert anchor in text
    text = text.replace(anchor, "    from tallyhub.extensions import install_extensions\n\n"
                                "    install_extensions(app)\n" + anchor)
    app_py.write_text(text, encoding="utf-8")

    readme = dest / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nBusiness extensions (subscriptions, marketplace, "
                      "returns, customs, ...) live in their own packages under `tallyhub/` and are installed on "
                      "the app as `app.ext.<name>` by `tallyhub/extensions.py`.\n", encoding="utf-8")
    return dest


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "tallyhub-large"
    build(target)
    print(target)
