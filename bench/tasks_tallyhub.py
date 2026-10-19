"""Round 2 tasks: bugs injected into `tallyhub`, a synthetic project the agent has never seen.

Every symptom is described in business terms; the root cause sits a few files away from where
the symptom shows up. Hidden acceptance tests live in hidden_tests/test_hidden_tallyhub.py.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from tasks import Task

SUITE = "tallyhub"
CORPUS_SOURCE = Path(__file__).resolve().parent / "corpora" / "tallyhub"

VISIBLE_TESTS = ["tests.test_money", "tests.test_pricing", "tests.test_orders", "tests.test_api"]
REGRESSION = ["test_hidden_tallyhub"] + VISIBLE_TESTS

PROMPT_TEMPLATE = """You are working in the repository of tallyhub, an order, pricing, tax and billing service \
(see README.md). Run Python as `python3` from the repository root; the existing test suite runs with \
`python3 -m unittest discover -s tests -t .`.

Bug report:
{symptom}

Find the root cause and fix it with a minimal change to the source code. Do not add test files and do not \
change the existing tests. When finished, reply with a one-sentence summary of the fix."""


def build_corpus(python: str, dest: Path) -> Path:
    if not dest.exists():
        shutil.copytree(CORPUS_SOURCE, dest, ignore=shutil.ignore_patterns("__pycache__", "._*", ".pmap"))
    return dest


_API_ORDER = (
    "from tallyhub.app import build_app\n"
    "from tallyhub.api.handlers import ApiApp\n"
    "api = ApiApp(build_app())\n"
)

TASKS = [
    Task(
        id="api-tax-exempt",
        file="tallyhub/api/serializers.py",
        old='        tax_exempt_id=payload.get("tax_exempt_id"),\n',
        new="",
        symptom=(
            "Customers with a registered tax-exemption certificate are still charged sales tax when their order "
            "is submitted through the HTTP API, e.g. POST /orders for customer c_alice shipping to California with "
            '"tax_exempt_id": "CA-123456". The same order placed from the command line with '
            "`--tax-exempt-id CA-123456` is correctly tax-free."
        ),
        repro=_API_ORDER + (
            "status, order = api.handle('POST', '/orders', {'customer_id': 'c_alice', 'items': [{'sku': 'MUG', "
            "'quantity': 2}], 'currency': 'USD', 'tax_exempt_id': 'CA-123456', 'shipping_address': {'line1': '1', "
            "'city': 'SF', 'country': 'US', 'region': 'CA'}})\n"
            "print(status, order['tax_exempt'], order['totals']['tax'])\n"
        ),
        hidden_tests=["test_hidden_tallyhub.TestApiTaxExempt"],
        regression_tests=REGRESSION,
        navigation="api.handlers -> api.serializers (field dropped) -> orders.requests -> tax.exemptions",
    ),
    Task(
        id="allocation-reserved-stock",
        file="tallyhub/inventory/allocator.py",
        old="            if level.available >= quantity:\n",
        new="            if level.on_hand >= quantity:\n",
        symptom=(
            "When two orders for the last units of a product arrive back to back, both are accepted and assigned "
            "to the same warehouse, which then has more units reserved than it physically holds, even though "
            "another warehouse still had the item. Example: with 1 MUG on hand in US-WEST and 5 in US-EAST, two "
            "one-mug orders shipping to California are both assigned to US-WEST."
        ),
        repro=(
            "from tests.helpers import make_app, request\n"
            "app = make_app()\n"
            "app.stock.set_on_hand('MUG', 'US-WEST', 1); app.stock.set_on_hand('MUG', 'US-EAST', 5)\n"
            "orders = [app.orders.place_order(request('c_bob', [('MUG', 1)])) for _ in range(2)]\n"
            "print([o.lines[0].warehouse for o in orders], app.stock.level('MUG', 'US-WEST'))\n"
        ),
        hidden_tests=["test_hidden_tallyhub.TestAllocationRespectsReservations"],
        regression_tests=REGRESSION,
        navigation="orders.service -> inventory.allocator (default strategy override) -> inventory.stock",
    ),
    Task(
        id="jpy-tax-rounding",
        file="tallyhub/tax/calculator.py",
        old="        return Money.raw(base.amount * rate, base.currency).rounded(mode)\n",
        new='        return Money.raw((base.amount * rate).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP"), '
            "base.currency)\n",
        symptom=(
            "Orders in Japanese yen come out with fractional yen. For customer c_hana ordering 3 TEE-RED and 1 "
            "BOOK-PY to Tokyo in JPY, the order shows a tax of 1660.50 JPY and a grand total of 19429.50 JPY. "
            "The yen has no minor unit, so tax and totals must be whole yen (expected tax: 1661 JPY)."
        ),
        repro=(
            "from tests.helpers import make_app, request\n"
            "from tallyhub.orders.models import Address\n"
            "o = make_app().orders.place_order(request('c_hana', [('TEE-RED', 3), ('BOOK-PY', 1)], currency='JPY', "
            "address=Address('1', 'Tokyo', 'JP')))\n"
            "print(o.totals.tax, o.totals.grand_total)\n"
        ),
        hidden_tests=["test_hidden_tallyhub.TestZeroDecimalCurrencyTax"],
        regression_tests=REGRESSION,
        navigation="orders.totals -> tax.calculator rounding helper -> core.money currency exponents",
    ),
    Task(
        id="coupon-loyalty-stacking",
        file="tallyhub/pricing/rules/loyalty.py",
        old='breakdown.has_adjustment_from("coupon")',
        new='breakdown.has_adjustment_from("coupons")',
        symptom=(
            "Loyalty-tier customers who use a coupon get both the coupon and their loyalty discount, although with "
            "the default settings the two must not be combined. Example: gold-tier customer c_alice buying one "
            "DESK-LAMP (60.00 USD) with coupon WELCOME10 gets a subtotal of 50.22 USD instead of 54.00 USD."
        ),
        repro=(
            "from tests.helpers import make_app, request\n"
            "q = make_app().orders.quote(request('c_alice', [('DESK-LAMP', 1)], coupon_code='WELCOME10'))\n"
            "print(q.totals.subtotal, q.breakdown.notes)\n"
        ),
        hidden_tests=["test_hidden_tallyhub.TestCouponLoyaltyStacking"],
        regression_tests=REGRESSION,
        navigation="pricing.engine -> rules registry by name (core.settings) -> rules.loyalty vs rules.coupon",
    ),
    Task(
        id="refund-store-credit-first",
        file="tallyhub/billing/refunds.py",
        old="        for payment in sorted(self.payments.for_order(order.id), key=self.refund_priority):\n",
        new="        for payment in sorted(self.payments.for_order(order.id), key=self.refund_priority, reverse=True):\n",
        symptom=(
            "Partial refunds on orders paid partly with store credit and partly by card go back to the card first. "
            "The configured policy is to restore store credit first and refund the card only for the rest. "
            "Example: customer c_tess pays a 66.94 USD order with 30.00 USD of store credit plus her card; a "
            "20.00 USD refund goes to the card and her store credit stays at 0."
        ),
        repro=(
            "from tests.helpers import make_app, request\n"
            "from tallyhub.orders.models import Address\n"
            "from tallyhub.orders.requests import PaymentInstruction\n"
            "from tallyhub.core.money import Money\n"
            "app = make_app()\n"
            "o = app.orders.place_order(request('c_tess', [('DESK-LAMP', 1)], address=Address('1', 'Austin', 'US', "
            "'TX'), payments=[PaymentInstruction('store_credit', Money.of(30, 'USD')), PaymentInstruction('card')]))\n"
            "r = app.refunds.refund(o.id, Money.of(20, 'USD'))\n"
            "print([(m, str(a)) for _p, m, a in r.parts], app.customers.get('c_tess').store_credit)\n"
        ),
        hidden_tests=["test_hidden_tallyhub.TestRefundOrder"],
        regression_tests=REGRESSION,
        navigation="billing.refunds ordering vs core.settings billing.refund_order and billing.payments",
    ),
    Task(
        id="shipping-tax",
        file="tallyhub/orders/service.py",
        old="            shipping=shipping.amount,\n",
        new="",
        symptom=(
            "In states and countries that tax shipping fees (New York, Texas, Germany, Japan), customers are taxed "
            "on their merchandise but not on shipping. Example: customer c_bob ordering 2 MUG shipped to New York "
            "pays 2.13 USD tax instead of 2.53 USD; the 4.50 USD shipping fee is never taxed."
        ),
        repro=(
            "from tests.helpers import make_app, request\n"
            "from tallyhub.orders.models import Address\n"
            "q = make_app().orders.quote(request('c_bob', [('MUG', 2)], address=Address('1', 'NYC', 'US', 'NY')))\n"
            "print(q.totals.shipping, q.totals.tax, q.tax.shipping_tax)\n"
        ),
        hidden_tests=["test_hidden_tallyhub.TestShippingTax"],
        regression_tests=REGRESSION,
        navigation="tax.calculator looks right; the caller in orders.service omits the shipping amount",
    ),
]

TASKS_BY_ID = {t.id: t for t in TASKS}
