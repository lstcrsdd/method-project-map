"""Demo data used by the CLI, the API examples and the tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from tallyhub.catalog.models import Bundle, Dimensions, Product
from tallyhub.core.money import Money
from tallyhub.customers.models import Customer, TaxCertificate
from tallyhub.inventory.warehouses import Warehouse
from tallyhub.pricing.coupons import Coupon
from tallyhub.tax.jurisdictions import Jurisdiction

D = Decimal


def seed_demo_data(app) -> None:
    usd = lambda v: Money.of(v, "USD")  # noqa: E731

    products = [
        Product("TEE-RED", "Red T-shirt", usd("20.00"), "standard", 200, Dimensions(30, 25, 3)),
        Product("MUG", "Enamel mug", usd("12.00"), "standard", 350, Dimensions(12, 12, 10)),
        Product("BOOK-PY", "Python in Practice (print)", usd("40.00"), "reduced", 800, Dimensions(24, 17, 4)),
        Product("EBOOK-PY", "Python in Practice (ebook)", usd("25.00"), "digital", 0, shippable=False),
        Product("DESK-LAMP", "Desk lamp", usd("60.00"), "standard", 1800, Dimensions(40, 20, 20)),
        Product("POSTER", "Conference poster", usd("15.00"), "standard", 150, Dimensions(60, 8, 8)),
        Product("CABLE", "USB-C cable", usd("9.99"), "standard", 60, Dimensions(10, 10, 2)),
        Product("OLD-CAP", "Discontinued cap", usd("14.00"), "standard", 90, active=False),
    ]
    for product in products:
        app.catalog.add_product(product)
    app.catalog.add_bundle(Bundle("BUNDLE-DEV", "Developer starter kit",
                                  (("BOOK-PY", 1), ("MUG", 1), ("TEE-RED", 1)), D("0.15")))

    app.prices.set_price("TEE-RED", Money.of("19.00", "EUR"))
    app.prices.set_price("TEE-RED", Money.of("2900", "JPY"))
    app.prices.set_price("BOOK-PY", Money.of("6000", "JPY"))
    app.prices.set_price("MUG", Money.of("1800", "JPY"))
    app.prices.set_price("TEE-RED", Money.of("14.00", "USD"), segment="wholesale")
    app.prices.set_price("MUG", Money.of("8.00", "USD"), segment="wholesale")

    app.customers.add(Customer("c_alice", "Alice Moreno", "alice@example.com", "US", "CA", loyalty_tier="gold",
                               certificates=[TaxCertificate("CA-123456", "US", "CA")],
                               store_credit=usd("50.00")))
    app.customers.add(Customer("c_bob", "Bob Ito", "bob@example.com", "US", "NY",
                               certificates=[TaxCertificate("NY-20240001", "US", "NY")]))
    app.customers.add(Customer("c_hana", "Hana Sato", "hana@example.jp", "JP", locale="ja_JP",
                               loyalty_tier="silver"))
    app.customers.add(Customer("c_ernst", "Ernst Vogel GmbH", "einkauf@vogel.example", "DE", segment="wholesale",
                               certificates=[TaxCertificate("DE123456789", "DE", "")], locale="de_DE"))
    app.customers.add(Customer("c_sam", "Sam Staff", "sam@tallyhub.example", "US", "TX", segment="staff"))
    app.customers.add(Customer("c_tess", "Tess Lin", "tess@example.com", "US", "TX", loyalty_tier="platinum",
                               store_credit=usd("30.00")))

    for code, name, country, priority in [
        ("US-WEST", "Reno, NV", "US", 10),
        ("US-EAST", "Newark, NJ", "US", 20),
        ("EU-CENTRAL", "Leipzig", "DE", 10),
        ("JP-TOKYO", "Tokyo", "JP", 10),
    ]:
        app.warehouses.add(Warehouse(code, name, country, priority))
    stock = {
        "US-WEST": {"TEE-RED": 120, "MUG": 80, "BOOK-PY": 40, "DESK-LAMP": 6, "POSTER": 200, "CABLE": 500},
        "US-EAST": {"TEE-RED": 60, "MUG": 30, "BOOK-PY": 25, "DESK-LAMP": 10, "CABLE": 300},
        "EU-CENTRAL": {"TEE-RED": 50, "MUG": 40, "BOOK-PY": 20, "DESK-LAMP": 4},
        "JP-TOKYO": {"TEE-RED": 30, "MUG": 30, "BOOK-PY": 15},
    }
    for warehouse, levels in stock.items():
        for sku, qty in levels.items():
            app.stock.set_on_hand(sku, warehouse, qty)

    for jurisdiction in [
        Jurisdiction("US-CA", "California", "US", "CA", {"standard": D("0.0725"), "reduced": D("0"),
                                                          "digital": D("0")}),
        Jurisdiction("US-NY", "New York", "US", "NY", {"standard": D("0.08875"), "reduced": D("0.08875"),
                                                        "digital": D("0.08875")}, tax_shipping=True),
        Jurisdiction("US-TX", "Texas", "US", "TX", {"standard": D("0.0625"), "reduced": D("0"),
                                                     "digital": D("0.0625")}, tax_shipping=True),
        Jurisdiction("DE", "Germany", "DE", "", {"standard": D("0.19"), "reduced": D("0.07"), "digital": D("0.19")},
                     tax_shipping=True),
        Jurisdiction("JP", "Japan", "JP", "", {"standard": D("0.10"), "reduced": D("0.08"), "digital": D("0.10")},
                     tax_shipping=True, exemptions_allowed=False),
        Jurisdiction("GB", "United Kingdom", "GB", "", {"standard": D("0.20"), "reduced": D("0"),
                                                         "digital": D("0.20")}),
    ]:
        app.jurisdictions.add(jurisdiction)

    app.coupons.add(Coupon("WELCOME10", "percent", D("0.10")))
    app.coupons.add(Coupon("SAVE5", "fixed", D("5"), currency="USD", min_subtotal=usd("30.00")))
    app.coupons.add(Coupon("VIP20", "percent", D("0.20"),
                           expires_at=datetime(2024, 1, 1, tzinfo=timezone.utc)))
    app.coupons.add(Coupon("STAFFONLY", "percent", D("0.30"), segments=["staff"]))

    app.payment_ctx.gift_cards["GC-1000"] = usd("100.00")
