"""Shared fixtures for the test suite."""

from datetime import datetime, timezone

from tallyhub.app import build_app
from tallyhub.core.clock import FixedClock
from tallyhub.orders.models import Address
from tallyhub.orders.requests import OrderRequest

NOW = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
CA = Address("1 Market St", "San Francisco", "US", "CA")
GB = Address("10 Downing St", "London", "GB")


def make_app(**overrides):
    return build_app(overrides or None, clock=FixedClock(NOW))


def request(customer="c_alice", items=(("TEE-RED", 1),), currency="USD", address=CA, **kwargs):
    return OrderRequest(customer_id=customer, items=list(items), currency=currency, shipping_address=address, **kwargs)
