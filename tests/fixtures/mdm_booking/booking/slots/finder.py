"""Function 2: book the first free window."""

from booking.models.booking import Booking
from booking.slots.windows import free_windows


def book(client: str) -> Booking:
    return Booking(free_windows()[0], client)
