import unittest
from decimal import Decimal

from tallyhub.core.errors import CurrencyMismatch
from tallyhub.core.fx import FxRates
from tallyhub.core.money import Money, quantum_for, sum_money


class MoneyTest(unittest.TestCase):
    def test_rounds_to_currency(self):
        self.assertEqual(Money.of("10.005", "USD").amount, Decimal("10.01"))
        self.assertEqual(Money.of("1500.4", "JPY").amount, Decimal("1500"))
        self.assertEqual(quantum_for("KWD"), Decimal("0.001"))

    def test_arithmetic_and_mismatch(self):
        self.assertEqual(Money.of(5, "EUR") + Money.of("2.50", "EUR"), Money.of("7.50", "EUR"))
        with self.assertRaises(CurrencyMismatch):
            Money.of(1, "EUR") + Money.of(1, "USD")

    def test_allocate_keeps_total(self):
        parts = Money.of("100.00", "USD").allocate([1, 1, 1])
        self.assertEqual(sum_money(parts, "USD"), Money.of("100.00", "USD"))
        self.assertEqual(sorted(p.amount for p in parts), [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")])

    def test_fx_cross_rate(self):
        fx = FxRates({"EUR": "0.90", "GBP": "0.75"})
        self.assertEqual(fx.convert(Money.of(90, "EUR"), "GBP"), Money.of("75.00", "GBP"))


if __name__ == "__main__":
    unittest.main()
