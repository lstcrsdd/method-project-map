"""Request validation before any pricing or side effects."""

from __future__ import annotations

from typing import List

from tallyhub.catalog.repository import CatalogRepository
from tallyhub.core.money import CURRENCY_EXPONENTS
from tallyhub.core.registry import PAYMENT_METHODS
from tallyhub.orders.requests import OrderRequest


def validate_request(request: OrderRequest, catalog: CatalogRepository) -> List[str]:
    problems: List[str] = []
    if not request.items:
        problems.append("order has no items")
    for sku, quantity in request.items:
        if quantity <= 0:
            problems.append(f"{sku}: quantity must be positive")
        if not catalog.is_bundle(sku):
            product = catalog.find_product(sku)
            if product is None:
                problems.append(f"{sku}: unknown product")
            elif not product.active:
                problems.append(f"{sku}: product is no longer sold")
    if request.currency not in CURRENCY_EXPONENTS:
        problems.append(f"currency {request.currency} is not supported")
    if not request.shipping_address.country:
        problems.append("shipping address needs a country")
    remainder_payments = [p for p in request.payments if p.amount is None]
    if len(remainder_payments) > 1:
        problems.append("only one payment may cover the remaining balance")
    for payment in request.payments:
        if payment.method not in PAYMENT_METHODS:
            problems.append(f"payment method {payment.method} is not available")
        if payment.amount is not None and payment.amount.currency != request.currency:
            problems.append(f"{payment.method} payment must be in {request.currency}")
    return problems
