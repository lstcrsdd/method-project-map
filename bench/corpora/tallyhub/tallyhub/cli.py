"""Command-line front end: `python -m tallyhub.cli place --customer c_alice --item TEE-RED:2 ...`."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from tallyhub.api.serializers import serialize_order
from tallyhub.app import build_app
from tallyhub.core.errors import TallyError
from tallyhub.core.money import Money
from tallyhub.orders.models import Address
from tallyhub.orders.requests import OrderRequest, PaymentInstruction
from tallyhub.reports.exporters import export


def parse_items(values: List[str]) -> List[tuple]:
    items = []
    for value in values:
        sku, _, qty = value.partition(":")
        items.append((sku, int(qty or 1)))
    return items


def parse_payments(values: Optional[List[str]], currency: str) -> List[PaymentInstruction]:
    if not values:
        return [PaymentInstruction("card")]
    payments = []
    for value in values:
        method, _, amount = value.partition(":")
        payments.append(PaymentInstruction(method, Money.of(amount, currency) if amount else None))
    return payments


def request_from_args(args: argparse.Namespace) -> OrderRequest:
    return OrderRequest(
        customer_id=args.customer,
        items=parse_items(args.item),
        currency=args.currency,
        shipping_address=Address(args.street, args.city, args.country.upper(), (args.region or "").upper()),
        shipping_method=args.method,
        coupon_code=args.coupon,
        tax_exempt_id=args.tax_exempt_id,
        payments=parse_payments(args.pay, args.currency),
        channel="cli",
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="tallyhub")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("quote", "place"):
        p = sub.add_parser(name)
        p.add_argument("--customer", required=True)
        p.add_argument("--item", action="append", required=True, help="SKU[:QTY]")
        p.add_argument("--currency", default="USD")
        p.add_argument("--country", default="US")
        p.add_argument("--region")
        p.add_argument("--city", default="Springfield")
        p.add_argument("--street", default="1 Main St")
        p.add_argument("--method", default="standard")
        p.add_argument("--coupon")
        p.add_argument("--tax-exempt-id")
        p.add_argument("--pay", action="append", help="METHOD[:AMOUNT]")
    report = sub.add_parser("report")
    report.add_argument("--format", default="csv")
    args = parser.parse_args(argv)

    app = build_app()
    try:
        if args.command == "quote":
            quote = app.orders.quote(request_from_args(args))
            print(json.dumps({"grand_total": str(quote.totals.grand_total), "tax": str(quote.totals.tax)}))
        elif args.command == "place":
            order = app.orders.place_order(request_from_args(args))
            print(json.dumps(serialize_order(order), indent=2))
        else:
            print(export(app.sales.daily(), args.format))
    except TallyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
