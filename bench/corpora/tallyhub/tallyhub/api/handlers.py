"""Framework-free HTTP API: `handle(method, path, body, headers) -> (status, payload)`."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from tallyhub.api.auth import ApiKeyAuth
from tallyhub.api.serializers import money_json, parse_money, parse_order_request, serialize_order
from tallyhub.core.errors import (
    InvalidTransition,
    NotFound,
    OutOfStock,
    PaymentDeclined,
    RefundError,
    Unauthorized,
    ValidationError,
)
from tallyhub.reports.exporters import export

Response = Tuple[int, Any]
ERROR_STATUS = [
    (Unauthorized, 401),
    (PaymentDeclined, 402),
    (NotFound, 404),
    (OutOfStock, 409),
    (InvalidTransition, 409),
    (RefundError, 409),
    (ValidationError, 422),
]


class ApiApp:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.auth = ApiKeyAuth(app.settings)
        self.routes: List[Tuple[str, re.Pattern, str, Callable[..., Response]]] = [
            ("POST", re.compile(r"^/quotes$"), "orders:read", self.create_quote),
            ("POST", re.compile(r"^/orders$"), "orders:write", self.create_order),
            ("GET", re.compile(r"^/orders/(?P<order_id>[\w-]+)$"), "orders:read", self.get_order),
            ("POST", re.compile(r"^/orders/(?P<order_id>[\w-]+)/cancel$"), "orders:write", self.cancel_order),
            ("POST", re.compile(r"^/orders/(?P<order_id>[\w-]+)/ship$"), "orders:write", self.ship_order),
            ("POST", re.compile(r"^/orders/(?P<order_id>[\w-]+)/refunds$"), "refunds:write", self.refund_order),
            ("GET", re.compile(r"^/reports/sales$"), "reports:read", self.sales_report),
        ]

    def handle(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
               headers: Optional[Mapping[str, str]] = None) -> Response:
        path, _, query = path.partition("?")
        params = dict(part.split("=", 1) for part in query.split("&") if "=" in part)
        for verb, pattern, scope, handler in self.routes:
            match = pattern.match(path)
            if verb == method.upper() and match:
                try:
                    self.auth.check(headers, scope)
                    return handler(body or {}, params, **match.groupdict())
                except tuple(cls for cls, _ in ERROR_STATUS) as exc:
                    status = next(code for cls, code in ERROR_STATUS if isinstance(exc, cls))
                    detail: Any = getattr(exc, "problems", None) or str(exc)
                    return status, {"error": type(exc).__name__, "detail": detail}
        return 404, {"error": "NoRoute", "detail": f"{method} {path}"}

    # ---------------------------------------------------------------- handlers

    def create_quote(self, body: Dict[str, Any], params: Dict[str, str]) -> Response:
        quote = self.app.orders.quote(parse_order_request(body))
        totals = quote.totals
        return 200, {
            "subtotal": money_json(totals.subtotal),
            "shipping": money_json(totals.shipping),
            "tax": money_json(totals.tax),
            "grand_total": money_json(totals.grand_total),
            "notes": quote.breakdown.notes,
        }

    def create_order(self, body: Dict[str, Any], params: Dict[str, str]) -> Response:
        order = self.app.orders.place_order(parse_order_request(body))
        self.app.invoices.issue(order)
        return 201, serialize_order(order)

    def get_order(self, body: Dict[str, Any], params: Dict[str, str], order_id: str) -> Response:
        return 200, serialize_order(self.app.orders.get(order_id))

    def cancel_order(self, body: Dict[str, Any], params: Dict[str, str], order_id: str) -> Response:
        order = self.app.orders.cancel_order(order_id, body.get("reason", "customer request"))
        return 200, serialize_order(order)

    def ship_order(self, body: Dict[str, Any], params: Dict[str, str], order_id: str) -> Response:
        order = self.app.orders.ship_order(order_id, body.get("tracking", ""))
        return 200, serialize_order(order)

    def refund_order(self, body: Dict[str, Any], params: Dict[str, str], order_id: str) -> Response:
        order = self.app.orders.get(order_id)
        amount = parse_money(body["amount"], order.currency) if body.get("amount") is not None else None
        refund = self.app.refunds.refund(order_id, amount, body.get("reason", ""))
        return 201, {
            "id": refund.id,
            "total": money_json(refund.total),
            "parts": [{"payment_id": pid, "method": method, "amount": money_json(amount)}
                      for pid, method, amount in refund.parts],
        }

    def sales_report(self, body: Dict[str, Any], params: Dict[str, str]) -> Response:
        rows = self.app.sales.daily()
        return 200, {"format": params.get("format", "json"), "data": export(rows, params.get("format", "json"))}
