"""Wires all services together."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from tallyhub.billing.invoices import InvoiceService
from tallyhub.billing.ledger import Ledger
from tallyhub.billing.numbering import InvoiceNumberer
from tallyhub.billing.payments import FakeCardProcessor, PaymentContext, PaymentService
from tallyhub.billing.refunds import RefundService
from tallyhub.catalog.price_lists import PriceList
from tallyhub.catalog.repository import CatalogRepository
from tallyhub.core.clock import Clock, IdGenerator
from tallyhub.core.events import EventBus
from tallyhub.core.fx import FxRates
from tallyhub.core.settings import Settings
from tallyhub.customers.repository import CustomerRepository
from tallyhub.inventory.allocator import Allocator
from tallyhub.inventory.stock import StockBook
from tallyhub.inventory.warehouses import WarehouseDirectory
from tallyhub.notifications.dispatcher import Notifier
from tallyhub.orders.service import OrderService
from tallyhub.pricing.coupons import CouponBook
from tallyhub.pricing.engine import PricingEngine
from tallyhub.pricing.rules.base import RuleDeps
from tallyhub.reports.sales import SalesReport
from tallyhub.shipping.rates import ShippingQuoter
from tallyhub.storage.memory import Table
from tallyhub.tax.calculator import TaxCalculator
from tallyhub.tax.exemptions import ExemptionPolicy
from tallyhub.tax.jurisdictions import JurisdictionResolver

DEFAULT_FX = {"EUR": "0.92", "GBP": "0.79", "JPY": "151.30", "SEK": "10.40", "CHF": "0.88", "CAD": "1.36",
              "KRW": "1350", "KWD": "0.307"}


@dataclass
class App:
    settings: Settings
    clock: Clock
    ids: IdGenerator
    events: EventBus
    fx: FxRates
    catalog: CatalogRepository
    prices: PriceList
    customers: CustomerRepository
    coupons: CouponBook
    jurisdictions: JurisdictionResolver
    warehouses: WarehouseDirectory
    stock: StockBook
    card_processor: FakeCardProcessor
    payment_ctx: PaymentContext
    payments: PaymentService
    orders: OrderService
    refunds: RefundService
    invoices: InvoiceService
    ledger: Ledger
    notifier: Notifier
    sales: SalesReport
    tables: Any


@dataclass
class Tables:
    orders: Table
    payments: Table
    refunds: Table
    invoices: Table


def build_app(overrides: Optional[Mapping[str, Any]] = None, clock: Optional[Clock] = None,
              seed: bool = True) -> App:
    settings = Settings(overrides)
    clock = clock or Clock()
    ids = IdGenerator()
    events = EventBus()
    fx = FxRates(DEFAULT_FX)
    tables = Tables(
        orders=Table("order", key=lambda o: o.id),
        payments=Table("payment", key=lambda p: p.id),
        refunds=Table("refund", key=lambda r: r.id),
        invoices=Table("invoice", key=lambda i: i.number),
    )

    catalog = CatalogRepository()
    prices = PriceList(catalog, fx)
    customers = CustomerRepository()
    coupons = CouponBook()
    jurisdictions = JurisdictionResolver()
    warehouses = WarehouseDirectory()
    stock = StockBook()

    card_processor = FakeCardProcessor()
    payment_ctx = PaymentContext(customers=customers, card_processor=card_processor)
    payments = PaymentService(payment_ctx, tables.payments, ids, clock, events)
    pricing = PricingEngine(RuleDeps(settings, catalog, prices, coupons))
    orders = OrderService(
        clock=clock,
        ids=ids,
        catalog=catalog,
        customers=customers,
        pricing=pricing,
        coupons=coupons,
        shipping=ShippingQuoter(settings, catalog, fx),
        tax=TaxCalculator(settings, jurisdictions, ExemptionPolicy()),
        allocator=Allocator(settings, warehouses, stock),
        stock=stock,
        payments=payments,
        orders=tables.orders,
        events=events,
    )
    refunds = RefundService(settings, payments, tables.orders, tables.refunds, ids, clock, events)
    invoices = InvoiceService(InvoiceNumberer(settings.get("billing.invoice_prefix", "INV"), clock), catalog,
                              tables.invoices, clock)
    notifier = Notifier(settings, customers, tables.orders, tables.refunds)
    notifier.attach(events)

    app = App(
        settings=settings, clock=clock, ids=ids, events=events, fx=fx, catalog=catalog, prices=prices,
        customers=customers, coupons=coupons, jurisdictions=jurisdictions, warehouses=warehouses, stock=stock,
        card_processor=card_processor, payment_ctx=payment_ctx, payments=payments, orders=orders,
        refunds=refunds, invoices=invoices, ledger=Ledger(), notifier=notifier,
        sales=SalesReport(tables.orders, tables.refunds), tables=tables,
    )
    if seed:
        from tallyhub.seed import seed_demo_data

        seed_demo_data(app)
    return app
