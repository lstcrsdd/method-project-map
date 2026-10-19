"""Placing, cancelling and shipping orders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from tallyhub.billing.payments import PaymentService
from tallyhub.catalog.repository import CatalogRepository
from tallyhub.core.clock import Clock, IdGenerator
from tallyhub.core.errors import InvalidTransition, ValidationError
from tallyhub.core.events import EventBus, OrderCancelled, OrderPlaced, OrderShipped
from tallyhub.customers.repository import CustomerRepository
from tallyhub.inventory.allocator import Allocator
from tallyhub.inventory.stock import StockBook
from tallyhub.orders import state
from tallyhub.orders.models import Order, OrderTotals
from tallyhub.orders.requests import OrderRequest
from tallyhub.orders.totals import build_lines, compute_totals
from tallyhub.orders.validators import validate_request
from tallyhub.pricing.breakdown import PriceBreakdown
from tallyhub.pricing.context import PricingContext
from tallyhub.pricing.coupons import CouponBook
from tallyhub.pricing.engine import PricingEngine
from tallyhub.shipping.rates import ShippingQuote, ShippingQuoter
from tallyhub.storage.memory import Table
from tallyhub.storage.unit_of_work import UnitOfWork
from tallyhub.tax.calculator import TaxCalculator, TaxResult


@dataclass
class Quote:
    breakdown: PriceBreakdown
    shipping: ShippingQuote
    tax: TaxResult
    totals: OrderTotals


class OrderService:
    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdGenerator,
        catalog: CatalogRepository,
        customers: CustomerRepository,
        pricing: PricingEngine,
        coupons: CouponBook,
        shipping: ShippingQuoter,
        tax: TaxCalculator,
        allocator: Allocator,
        stock: StockBook,
        payments: PaymentService,
        orders: Table,
        events: EventBus,
    ) -> None:
        self.clock = clock
        self.ids = ids
        self.catalog = catalog
        self.customers = customers
        self.pricing = pricing
        self.coupons = coupons
        self.shipping = shipping
        self.tax = tax
        self.allocator = allocator
        self.stock = stock
        self.payments = payments
        self.orders = orders
        self.events = events

    # ------------------------------------------------------------ quoting

    def expand_items(self, items: List[Tuple[str, int]]) -> Dict[str, int]:
        """Physical units per product sku: bundles are replaced by their components."""
        units: Dict[str, int] = {}
        for sku, quantity in items:
            bundle = self.catalog.bundle(sku)
            parts = bundle.components if bundle else ((sku, 1),)
            for component, per_bundle in parts:
                units[component] = units.get(component, 0) + per_bundle * quantity
        return units

    def quote(self, request: OrderRequest) -> Quote:
        problems = validate_request(request, self.catalog)
        if problems:
            raise ValidationError(problems)
        customer = self.customers.get(request.customer_id)
        ctx = PricingContext(
            customer=customer,
            currency=request.currency,
            items=list(request.items),
            at=self.clock.now(),
            coupon_code=request.coupon_code,
            channel=request.channel,
        )
        breakdown = self.pricing.price(ctx)
        shipping = self.shipping.quote(
            list(self.expand_items(request.items).items()),
            request.shipping_address,
            request.shipping_method,
            request.currency,
            merchandise_subtotal=breakdown.subtotal,
        )
        tax = self.tax.compute(
            breakdown,
            customer,
            request.shipping_address,
            shipping=shipping.amount,
            certificate_id=request.tax_exempt_id,
        )
        return Quote(breakdown, shipping, tax, compute_totals(breakdown, shipping.amount, tax))

    # ------------------------------------------------------------ lifecycle

    def place_order(self, request: OrderRequest) -> Order:
        quote = self.quote(request)
        now = self.clock.now()
        order = Order(
            id=self.ids.next("ord"),
            customer_id=request.customer_id,
            currency=request.currency,
            lines=build_lines(quote.breakdown, quote.tax),
            totals=quote.totals,
            shipping_address=request.shipping_address,
            shipping_method=request.shipping_method,
            carrier=quote.shipping.carrier,
            status=state.PLACED,
            created_at=now,
            channel=request.channel,
            coupon_code=request.coupon_code if request.coupon_code in quote.breakdown.applied_codes else None,
            tax_exempt_id=request.tax_exempt_id,
            tax_exempt=quote.tax.exempt,
            tax_jurisdiction=quote.tax.jurisdiction,
            notes=list(quote.breakdown.notes) + ([request.note] if request.note else []),
            history=[(state.PLACED, now)],
        )

        plan = self.allocator.plan(self.expand_items(request.items), request.shipping_address)
        with UnitOfWork(self.orders):
            for sku, quantity in self.expand_items(request.items).items():
                self.stock.reserve(order.id, sku, plan[sku], quantity, now)
            for line in order.lines:
                if not self.catalog.is_bundle(line.sku):
                    line.warehouse = plan.get(line.sku)
            self.orders.put(order)
            try:
                self.payments.charge(order, request.payments)
            except Exception:
                self.stock.release(order.id)
                raise

        if order.coupon_code:
            self.coupons.redeem(order.coupon_code)
        state.transition(order, state.PAID, self.clock.now())
        self.events.publish(OrderPlaced(order.id, order.customer_id))
        return order

    def get(self, order_id: str) -> Order:
        return self.orders.get(order_id)

    def cancel_order(self, order_id: str, reason: str = "customer request") -> Order:
        order = self.get(order_id)
        if order.status not in state.CANCELLABLE:
            raise InvalidTransition(f"order {order_id} cannot be cancelled once {order.status}")
        for payment in self.payments.for_order(order.id):
            if not payment.voided:
                self.payments.method(payment.method).void(payment)
                payment.voided = True
        self.stock.release(order.id)
        state.transition(order, state.CANCELLED, self.clock.now())
        self.events.publish(OrderCancelled(order.id, reason))
        return order

    def ship_order(self, order_id: str, tracking: str) -> Order:
        order = self.get(order_id)
        self.stock.commit(order.id)
        order.tracking = tracking
        state.transition(order, state.SHIPPED, self.clock.now())
        self.events.publish(OrderShipped(order.id, order.carrier or "unknown", tracking))
        return order
