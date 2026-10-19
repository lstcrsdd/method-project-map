import unittest

from tallyhub.api.handlers import ApiApp
from tallyhub.cli import main as cli_main
from tests.helpers import make_app

CA_ADDRESS = {"line1": "1 Market St", "city": "San Francisco", "country": "us", "region": "ca"}


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.api = ApiApp(self.app)

    def order_body(self, **extra):
        body = {"customer_id": "c_bob", "items": [{"sku": "MUG", "quantity": 2}], "currency": "USD",
                "shipping_address": CA_ADDRESS}
        body.update(extra)
        return body

    def test_create_and_fetch_order(self):
        status, order = self.api.handle("POST", "/orders", self.order_body())
        self.assertEqual(status, 201)
        self.assertEqual(order["totals"]["subtotal"], {"amount": "24.00", "currency": "USD"})
        status, fetched = self.api.handle("GET", f"/orders/{order['id']}")
        self.assertEqual(fetched["id"], order["id"])
        self.assertEqual(len(self.app.invoices.for_order(order["id"])), 1)

    def test_validation_error_status(self):
        status, payload = self.api.handle("POST", "/orders", {"customer_id": "c_bob"})
        self.assertEqual(status, 422)
        self.assertIn("missing field items", payload["detail"])

    def test_unknown_route(self):
        self.assertEqual(self.api.handle("DELETE", "/orders")[0], 404)

    def test_api_keys(self):
        api = ApiApp(make_app(api={"keys": {"k1": ["orders:read"]}}))
        self.assertEqual(api.handle("POST", "/orders", self.order_body(), {"X-Api-Key": "k1"})[0], 401)
        self.assertEqual(api.handle("POST", "/quotes", self.order_body(), {"X-Api-Key": "k1"})[0], 200)

    def test_quote_with_coupon(self):
        status, quote = self.api.handle("POST", "/quotes", self.order_body(coupon_code="WELCOME10"))
        self.assertEqual(quote["subtotal"]["amount"], "21.60")

    def test_cancel(self):
        _, order = self.api.handle("POST", "/orders", self.order_body())
        status, cancelled = self.api.handle("POST", f"/orders/{order['id']}/cancel", {"reason": "duplicate"})
        self.assertEqual(cancelled["status"], "cancelled")

    def test_sales_report_csv(self):
        self.api.handle("POST", "/orders", self.order_body())
        status, report = self.api.handle("GET", "/reports/sales?format=csv")
        self.assertTrue(report["data"].startswith("day,currency,orders"))


class CliTest(unittest.TestCase):
    def test_cli_exempt_order(self):
        import io
        from contextlib import redirect_stdout

        out = io.StringIO()
        with redirect_stdout(out):
            code = cli_main(["place", "--customer", "c_alice", "--item", "MUG:1", "--region", "CA",
                             "--tax-exempt-id", "CA-123456"])
        self.assertEqual(code, 0)
        self.assertIn('"tax_exempt": true', out.getvalue())


if __name__ == "__main__":
    unittest.main()
