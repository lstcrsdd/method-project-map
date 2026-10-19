"""Function 1: accept a request."""

from booking.models.request import Request


def accept(service: str, phone: str) -> Request:
    return Request(service, phone)
