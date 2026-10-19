import unittest
from decimal import Decimal

from tallyhub.core.money import Money
from tallyhub.pricing.context import PricingContext
from tests.helpers import NOW, make_app


def price(app, customer_id, items, coupon=None, currency="USD"):
    ctx = PricingContext(app.customers.get(customer_id), currency, list(items), NOW, coupon_code=coupon)
    return app.orders.pricing.price(ctx)


class PricingTest(unittest.TestCase):
    def setUp(self):
        self.app = make_app()

    def test_volume_discount(self):
        breakdown = price(self.app, "c_bob", [("CABLE", 12)])
        self.assertEqual(breakdown.subtotal, Money.of("113.89", "USD"))  # 12 x 9.99 - 5%

    def test_bundle_discount(self):
        breakdown = price(self.app, "c_bob", [("BUNDLE-DEV", 1)])
        self.assertEqual(breakdown.subtotal, Money.of("61.20", "USD"))

    def test_percent_coupon(self):
        breakdown = price(self.app, "c_bob", [("DESK-LAMP", 1)], coupon="welcome10")
        self.assertEqual(breakdown.subtotal, Money.of("54.00", "USD"))
        self.assertIn("WELCOME10", breakdown.applied_codes)

    def test_fixed_coupon_needs_minimum(self):
        small = price(self.app, "c_bob", [("MUG", 1)], coupon="SAVE5")
        self.assertEqual(small.subtotal, Money.of("12.00", "USD"))
        self.assertTrue(any("needs a subtotal" in n for n in small.notes))

    def test_expired_coupon_is_ignored(self):
        breakdown = price(self.app, "c_bob", [("MUG", 1)], coupon="VIP20")
        self.assertEqual(breakdown.subtotal, Money.of("12.00", "USD"))

    def test_loyalty_discount(self):
        breakdown = price(self.app, "c_tess", [("DESK-LAMP", 1)])
        self.assertEqual(breakdown.subtotal, Money.of("54.00", "USD"))

    def test_wholesale_price_list(self):
        breakdown = price(self.app, "c_ernst", [("MUG", 2)])
        self.assertEqual(breakdown.lines[0].unit_price, Money.of("8.00", "USD"))

    def test_settings_change_rule_order(self):
        app = make_app(pricing={"rules": ["volume"]})
        breakdown = price(app, "c_tess", [("DESK-LAMP", 1)])
        self.assertEqual(breakdown.subtotal, Money.of("60.00", "USD"))
        self.assertEqual([r.name for r in app.orders.pricing.rules], ["volume"])
        self.assertEqual(app.settings.get("loyalty.tiers")["gold"], "0.07")
        self.assertEqual(Decimal(app.settings.get("pricing.volume_tiers")[0][1]), Decimal("0.05"))


if __name__ == "__main__":
    unittest.main()
