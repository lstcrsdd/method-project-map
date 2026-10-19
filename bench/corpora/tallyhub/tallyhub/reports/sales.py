"""Sales reporting over placed orders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional

from tallyhub.core.money import Money
from tallyhub.orders import state
from tallyhub.reports.aggregations import group_by, sum_by_currency
from tallyhub.storage.memory import Table

COUNTED_STATUSES = {state.PAID, state.SHIPPED, state.DELIVERED, state.PARTIALLY_REFUNDED, state.REFUNDED}


@dataclass(frozen=True)
class SalesRow:
    day: str
    currency: str
    orders: int
    gross: Money
    refunded: Money
    net: Money
    tax: Money


class SalesReport:
    def __init__(self, orders: Table, refunds: Table) -> None:
        self.orders = orders
        self.refunds = refunds

    def daily(self, since: Optional[date] = None) -> List[SalesRow]:
        orders = [o for o in self.orders if o.status in COUNTED_STATUSES]
        if since is not None:
            orders = [o for o in orders if o.created_at.date() >= since]
        refunded_by_order: Dict[str, List[Money]] = {}
        for refund in self.refunds:
            refunded_by_order.setdefault(refund.order_id, []).append(refund.total)

        rows: List[SalesRow] = []
        for key, group in group_by(orders, lambda o: f"{o.created_at.date().isoformat()}|{o.currency}").items():
            day, currency = key.split("|")
            gross = sum_by_currency(o.totals.grand_total for o in group).get(currency, Money.zero(currency))
            tax = sum_by_currency(o.totals.tax for o in group).get(currency, Money.zero(currency))
            refunded = sum_by_currency(
                amount for o in group for amount in refunded_by_order.get(o.id, [])
            ).get(currency, Money.zero(currency))
            rows.append(SalesRow(day, currency, len(group), gross, refunded, gross - refunded, tax))
        return sorted(rows, key=lambda r: (r.day, r.currency))

    def top_products(self, limit: int = 5) -> List[tuple]:
        units: Dict[str, int] = {}
        for order in self.orders:
            if order.status in COUNTED_STATUSES:
                for line in order.lines:
                    units[line.sku] = units.get(line.sku, 0) + line.quantity
        return sorted(units.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
