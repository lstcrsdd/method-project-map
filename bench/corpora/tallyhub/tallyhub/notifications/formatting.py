"""Locale-aware money and date formatting for messages."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from tallyhub.core.money import Money, exponent_for

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "SEK": "kr", "CHF": "CHF", "CAD": "CA$", "KRW": "₩",
           "KWD": "KD"}
LOCALE_SEPARATORS = {"en_US": (",", "."), "en_GB": (",", "."), "de_DE": (".", ","), "sv_SE": (" ", ","),
                     "ja_JP": (",", ".")}
SYMBOL_AFTER = {"de_DE", "sv_SE"}


def format_money(money: Money, locale: str = "en_US") -> str:
    thousands, decimal_mark = LOCALE_SEPARATORS.get(locale, (",", "."))
    places = exponent_for(money.currency)
    amount = money.rounded().amount
    sign = "-" if amount < 0 else ""
    integer, _, fraction = f"{abs(amount):.{places}f}".partition(".")
    groups = []
    while len(integer) > 3:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    groups.insert(0, integer)
    number = thousands.join(groups) + (decimal_mark + fraction if places else "")
    symbol = SYMBOLS.get(money.currency, money.currency)
    if locale in SYMBOL_AFTER:
        return f"{sign}{number} {symbol}"
    return f"{sign}{symbol}{number}"


def format_date(at: datetime, locale: str = "en_US") -> str:
    if locale in ("de_DE", "sv_SE"):
        return at.strftime("%d.%m.%Y")
    if locale == "ja_JP":
        return at.strftime("%Y/%m/%d")
    return at.strftime("%b %d, %Y")


def format_percent(fraction: Decimal) -> str:
    return f"{(fraction * 100).normalize():f}%"
