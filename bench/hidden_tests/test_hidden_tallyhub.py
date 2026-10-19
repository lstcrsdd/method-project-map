"""Hidden acceptance tests for the tallyhub benchmark tasks (never copied into the agent workspace)."""

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from tallyhub.api.handlers import ApiApp
from tallyhub.app import build_app
from tallyhub.core.clock import FixedClock
from tallyhub.core.errors import OutOfStock
from tallyhub.core.money import Money
from tallyhub.orders.models import Address
from tallyhub.orders.requests import OrderRequest, PaymentInstruction

NOW = datetime(2026, 5, 2, 9, 30, tzinfo=timezone.utc)
CA = Address("1 Market St", "San Francisco", "US", "CA")
NY = Address("5 Fifth Ave", "New York", "US", "NY")
TX = Address("1 Congress Ave", "Austin", "US", "TX")
JP = Address("1-1 Chiyoda", "Tokyo", "JP")
DE = Address("Hauptstr. 1", "Leipzig", "DE")


def app(**overrides):
    return build_app(overrides or None, clock=FixedClock(NOW))


def req(customer, items, address, currency="USD", **kw):
    return OrderRequest(customer_id=customer, items=list(items), currency=currency, shipping_address=address, **kw)


def usd(value):
    return Money.of(value, "USD")


def api_body(customer, items, address, **extra):
    body = {
        "customer_id": customer,
        "items": [{"sku": s, "quantity": q} for s, q in items],
        "currency": extra.pop("currency", "USD"),
        "shipping_address": {"line1": address.line1, "city": address.city, "country": address.country,
                             "region": address.region},
    }
    body.update(extra)
    return body


class TestApiTaxExempt(unittest.TestCase):
    """Task api-tax-exempt: certificates sent through the HTTP API must be honoured."""

    def test_api_order_with_certificate_is_tax_free(self):
        api = ApiApp(app())
        status, order = api.handle("POST", "/orders", api_body("c_alice", [("MUG", 2)], CA, tax_exempt_id="CA-123456"))
        self.assertEqual(status, 201)
        self.assertTrue(order["tax_exempt"])
        self.assertEqual(order["totals"]["tax"], {"amount": "0.00", "currency": "USD"})

    def test_api_quote_with_certificate(self):
        api = ApiApp(app())
        status, quote = api.handle("POST", "/quotes", api_body("c_bob", [("MUG", 2)], NY, tax_exempt_id="NY-20240001"))
        self.assertEqual(status, 200)
        self.assertEqual(quote["tax"]["amount"], "0.00")

    def test_api_invalid_certificate_is_taxed(self):
        api = ApiApp(app())
        status, order = api.handle("POST", "/orders", api_body("c_alice", [("MUG", 2)], CA, tax_exempt_id="CA-000000"))
        self.assertFalse(order["tax_exempt"])
        self.assertEqual(order["totals"]["tax"]["amount"], "1.62")  # gold tier: 24.00 - 7%, then 7.25%


class TestAllocationRespectsReservations(unittest.TestCase):
    """Task allocation-reserved-stock: units promised to earlier orders are not available."""

    def test_second_order_goes_to_warehouse_with_free_stock(self):
        a = app()
        a.stock.set_on_hand("MUG", "US-WEST", 1)
        a.stock.set_on_hand("MUG", "US-EAST", 5)
        first = a.orders.place_order(req("c_bob", [("MUG", 1)], CA))
        second = a.orders.place_order(req("c_bob", [("MUG", 1)], CA))
        self.assertEqual(first.lines[0].warehouse, "US-WEST")
        self.assertEqual(second.lines[0].warehouse, "US-EAST")

    def test_never_reserves_more_than_on_hand(self):
        a = app()
        for _ in range(2):
            a.orders.place_order(req("c_bob", [("DESK-LAMP", 6)], CA))
        with self.assertRaises(OutOfStock):
            a.orders.place_order(req("c_bob", [("DESK-LAMP", 6)], CA))
        for warehouse in ("US-WEST", "US-EAST"):
            level = a.stock.level("DESK-LAMP", warehouse)
            self.assertLessEqual(level.reserved, level.on_hand)

    def test_priority_strategy_too(self):
        a = app(inventory={"allocation_strategy": "priority"})
        a.stock.set_on_hand("TEE-RED", "US-WEST", 2)
        a.orders.place_order(req("c_bob", [("TEE-RED", 2)], CA))
        second = a.orders.place_order(req("c_bob", [("TEE-RED", 2)], CA))
        self.assertNotEqual(second.lines[0].warehouse, "US-WEST")


class TestZeroDecimalCurrencyTax(unittest.TestCase):
    """Task jpy-tax-rounding: tax is rounded to the currency's minor unit (whole yen)."""

    def assert_whole(self, money):
        self.assertEqual(money.amount, money.amount.to_integral_value(), f"{money} has fractional yen")

    def test_jpy_order_totals_are_whole_yen(self):
        order = app().orders.place_order(req("c_hana", [("TEE-RED", 3), ("BOOK-PY", 1)], JP, "JPY"))
        self.assertEqual(order.totals.tax, Money.of(1661, "JPY"))
        self.assertEqual(order.totals.grand_total, Money.of(19430, "JPY"))
        for line in order.lines:
            self.assert_whole(line.tax)
        self.assert_whole(order.totals.grand_total)

    def test_card_charge_matches_order_total(self):
        a = app()
        order = a.orders.place_order(req("c_hana", [("MUG", 1)], JP, "JPY"))
        self.assertEqual(order.totals.tax, Money.of(494, "JPY"))
        self.assertEqual(a.card_processor.charges[-1][1], Money.of(5432, "JPY"))

    def test_two_decimal_currencies_unchanged(self):
        quote = app().orders.quote(req("c_ernst", [("MUG", 3)], DE, "EUR"))
        self.assertEqual(quote.totals.tax, Money.of("9.47", "EUR"))


class TestCouponLoyaltyStacking(unittest.TestCase):
    """Task coupon-loyalty-stacking: coupons and loyalty discounts combine only when settings allow it."""

    def test_coupon_replaces_loyalty_by_default(self):
        quote = app().orders.quote(req("c_alice", [("DESK-LAMP", 1)], CA, coupon_code="WELCOME10"))
        self.assertEqual(quote.totals.subtotal, usd("54.00"))
        self.assertTrue(any("loyalty" in note for note in quote.breakdown.notes))

    def test_stacking_when_allowed(self):
        quote = app(pricing={"allow_coupon_with_loyalty": True}).orders.quote(
            req("c_alice", [("DESK-LAMP", 1)], CA, coupon_code="WELCOME10"))
        self.assertEqual(quote.totals.subtotal, usd("50.22"))

    def test_loyalty_without_coupon(self):
        quote = app().orders.quote(req("c_alice", [("DESK-LAMP", 1)], CA))
        self.assertEqual(quote.totals.subtotal, usd("55.80"))

    def test_fixed_coupon_also_blocks_loyalty(self):
        quote = app().orders.quote(req("c_tess", [("DESK-LAMP", 1)], TX, coupon_code="SAVE5"))
        self.assertEqual(quote.totals.subtotal, usd("55.00"))


class TestRefundOrder(unittest.TestCase):
    """Task refund-store-credit-first: refunds restore store credit before going back to the card."""

    def place(self, a):
        return a.orders.place_order(req(
            "c_tess", [("DESK-LAMP", 1)], TX,
            payments=[PaymentInstruction("store_credit", usd("30")), PaymentInstruction("card")]))

    def test_partial_refund_goes_to_store_credit(self):
        a = app()
        order = self.place(a)
        refund = a.refunds.refund(order.id, usd("20"))
        self.assertEqual([(m, amt) for _p, m, amt in refund.parts], [("store_credit", usd("20"))])
        self.assertEqual(a.customers.get("c_tess").store_credit, usd("20.00"))
        self.assertEqual(a.card_processor.refunds, [])

    def test_refund_spills_over_to_card(self):
        a = app()
        order = self.place(a)
        a.refunds.refund(order.id, usd("20"))
        refund = a.refunds.refund(order.id, usd("15"))
        self.assertEqual([(m, amt) for _p, m, amt in refund.parts],
                         [("store_credit", usd("10")), ("card", usd("5"))])
        self.assertEqual(a.customers.get("c_tess").store_credit, usd("30.00"))

    def test_api_refund_parts(self):
        a = app()
        order = self.place(a)
        status, refund = ApiApp(a).handle("POST", f"/orders/{order.id}/refunds", {"amount": "25"})
        self.assertEqual(status, 201)
        self.assertEqual(refund["parts"][0]["method"], "store_credit")


class TestShippingTax(unittest.TestCase):
    """Task shipping-tax: jurisdictions that tax shipping charge tax on the shipping fee."""

    def test_new_york_taxes_shipping(self):
        order = app().orders.place_order(req("c_bob", [("MUG", 2)], NY))
        self.assertEqual(order.totals.shipping, usd("4.50"))
        self.assertEqual(order.totals.tax, usd("2.53"))
        self.assertEqual(order.totals.grand_total, usd("31.03"))

    def test_texas_quote(self):
        quote = app().orders.quote(req("c_tess", [("CABLE", 3)], TX))
        self.assertEqual(quote.tax.shipping_tax, usd("0.28"))
        self.assertEqual(quote.totals.tax, usd("1.97"))

    def test_california_does_not(self):
        quote = app().orders.quote(req("c_bob", [("MUG", 2)], CA))
        self.assertIsNone(quote.tax.shipping_tax)
        self.assertEqual(quote.totals.tax, usd("1.74"))

    def test_germany_via_api(self):
        status, quote = ApiApp(app()).handle("POST", "/quotes", api_body("c_ernst", [("MUG", 3)], DE, currency="EUR"))
        self.assertEqual(quote["tax"]["amount"], "9.47")


if __name__ == "__main__":
    unittest.main()
