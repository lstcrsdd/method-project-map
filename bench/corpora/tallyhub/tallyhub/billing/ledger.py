"""Double-entry bookkeeping of sales and refunds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from tallyhub.core.money import Money, sum_money


@dataclass(frozen=True)
class Entry:
    at: datetime
    account: str
    debit: Money
    credit: Money
    reference: str


class Ledger:
    def __init__(self) -> None:
        self.entries: List[Entry] = []

    def post(self, at: datetime, reference: str, debits: Dict[str, Money], credits: Dict[str, Money]) -> None:
        currencies = {m.currency for m in list(debits.values()) + list(credits.values())}
        if len(currencies) != 1:
            raise ValueError("a posting must use exactly one currency")
        currency = currencies.pop()
        zero = Money.zero(currency)
        if sum_money(debits.values(), currency) != sum_money(credits.values(), currency):
            raise ValueError(f"unbalanced posting {reference}")
        for account, amount in debits.items():
            self.entries.append(Entry(at, account, amount, zero, reference))
        for account, amount in credits.items():
            self.entries.append(Entry(at, account, zero, amount, reference))

    def balance(self, account: str, currency: str) -> Money:
        debit = sum_money([e.debit for e in self.entries if e.account == account and e.debit.currency == currency], currency)
        credit = sum_money([e.credit for e in self.entries if e.account == account and e.credit.currency == currency], currency)
        return debit - credit
