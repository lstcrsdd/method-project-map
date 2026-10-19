import unittest

from tallyhub.core.errors import OutOfStock, PaymentDeclined, ValidationError
from tallyhub.core.events import OrderPlaced
from tallyhub.core.money import Money
from tallyhub.orders import state
from tallyhub.orders.requests import PaymentInstruction
from tests.helpers import GB, make_app, request


class PlaceOrderTest(unittest.TestCase):
    def setUp(self):
        self.app = make_app()

    def test_california_order(self):
        order = self.app.orders.place_order(request("c_bob", [("DESK-LAMP", 1), ("BOOK-PY", 1)]))
        self.assertEqual(order.status, state.PAID)
        self.assertEqual(order.totals.subtotal, Money.of("100.00", "USD"))
        # books are zero-rated in California; shipping is not taxed there
        self.assertEqual(order.totals.tax, Money.of("4.35", "USD"))
        self.assertEqual(order.tax_jurisdiction, "US-CA")
        self.assertEqual(self.app.events.of_type(OrderPlaced)[0].order_id, order.id)

    def test_free_shipping_over_threshold(self):
        order = self.app.orders.place_order(request("c_bob", [("DESK-LAMP", 3)]))
        self.assertTrue(order.totals.shipping.is_zero())

    def test_exempt_order_from_service(self):
        order = self.app.orders.place_order(request("c_alice", [("MUG", 2)], tax_exempt_id="CA-123456"))
        self.assertTrue(order.tax_exempt)
        self.assertTrue(order.totals.tax.is_zero())

    def test_wrong_certificate_is_not_exempt(self):
        order = self.app.orders.place_order(request("c_alice", [("MUG", 2)], tax_exempt_id="CA-999999"))
        self.assertFalse(order.tax_exempt)
        self.assertFalse(order.totals.tax.is_zero())

    def test_validation_errors(self):
        with self.assertRaises(ValidationError) as ctx:
            self.app.orders.place_order(request("c_bob", [("OLD-CAP", 1), ("NOPE", 0)]))
        self.assertEqual(len(ctx.exception.problems), 3)

    def test_out_of_stock(self):
        with self.assertRaises(OutOfStock):
            self.app.orders.place_order(request("c_bob", [("DESK-LAMP", 50)]))

    def test_declined_card_releases_stock(self):
        before = self.app.stock.level("MUG", "US-WEST").available
        with self.assertRaises(PaymentDeclined):
            self.app.orders.place_order(request("c_bob", [("MUG", 1)],
                                                payments=[PaymentInstruction("card", token="tok_decline")]))
        self.assertEqual(self.app.stock.level("MUG", "US-WEST").available, before)
        self.assertEqual(len(self.app.tables.orders), 0)

    def test_split_payment_with_store_credit(self):
        order = self.app.orders.place_order(request(
            "c_alice", [("DESK-LAMP", 1)],
            payments=[PaymentInstruction("store_credit", Money.of("20", "USD")), PaymentInstruction("card")]))
        methods = [p.method for p in self.app.payments.for_order(order.id)]
        self.assertEqual(methods, ["store_credit", "card"])
        self.assertEqual(self.app.customers.get("c_alice").store_credit, Money.of("30.00", "USD"))

    def test_cancel_releases_reservation(self):
        order = self.app.orders.place_order(request("c_bob", [("DESK-LAMP", 2)]))
        warehouse = order.lines[0].warehouse
        self.assertEqual(self.app.stock.level("DESK-LAMP", warehouse).reserved, 2)
        self.app.orders.cancel_order(order.id)
        self.assertEqual(self.app.stock.level("DESK-LAMP", warehouse).reserved, 0)
        self.assertEqual(order.status, state.CANCELLED)

    def test_ship_commits_stock(self):
        order = self.app.orders.place_order(request("c_bob", [("POSTER", 4)]))
        self.app.orders.ship_order(order.id, "PL123")
        self.assertEqual(self.app.stock.level("POSTER", "US-WEST").on_hand, 196)
        self.assertEqual(order.status, state.SHIPPED)

    def test_international_order(self):
        order = self.app.orders.place_order(request("c_bob", [("MUG", 1)], address=GB, currency="GBP"))
        self.assertEqual(order.carrier, "globex")
        self.assertEqual(order.tax_jurisdiction, "GB")


class RefundTest(unittest.TestCase):
    def test_full_refund_to_card(self):
        app = make_app()
        order = app.orders.place_order(request("c_bob", [("MUG", 2)]))
        refund = app.refunds.refund(order.id)
        self.assertEqual(refund.total, order.totals.grand_total)
        self.assertEqual(order.status, state.REFUNDED)
        self.assertEqual(app.card_processor.refunds[0][1], order.totals.grand_total)


if __name__ == "__main__":
    unittest.main()
